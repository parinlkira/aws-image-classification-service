"""
run_local.py - Simulate the full Step Function workflow locally.

Uses moto to mock AWS S3, then runs each Lambda handler in sequence,
mimicking the Step Function state machine transitions.
"""

import json
import os
import sys

import boto3
from moto import mock_aws

# Add lambda dirs to path so we can import handlers
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "fetch_catalog"))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "classify_image"))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "check_quality"))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "write_results"))

import lambdas.fetch_catalog.handler as fetch_mod
import lambdas.classify_image.handler as classify_mod
import lambdas.check_quality.handler as quality_mod
import lambdas.write_results.handler as write_mod

BUCKET_NAME = "catalog-data-quality-bucket"
CATALOG_PREFIX = "catalog/"
SAMPLE_DIR = os.path.join(ROOT, "sample_data")


def upload_sample_data(s3_client):
    """Upload sample JSON files to the mocked S3 bucket."""
    s3_client.create_bucket(
        Bucket=BUCKET_NAME,
        CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
    )
    for filename in sorted(os.listdir(SAMPLE_DIR)):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(SAMPLE_DIR, filename)
        key = f"{CATALOG_PREFIX}{filename}"
        with open(filepath, "r") as f:
            s3_client.put_object(Bucket=BUCKET_NAME, Key=key, Body=f.read())
        print(f"  Uploaded: s3://{BUCKET_NAME}/{key}")


@mock_aws
def run_workflow():
    """Simulate the Step Function workflow locally."""
    print("=" * 60)
    print("  CATALOG DATA QUALITY CHECKER  -  Local Simulation")
    print("=" * 60)

    # Setup: create bucket and upload sample data
    s3 = boto3.client("s3", region_name="us-west-2")
    print("\n[Setup] Uploading sample catalog items to mock S3...")
    upload_sample_data(s3)

    # ---- State 1: FetchCatalogItems ----
    print("\n" + "-" * 60)
    print("[Step 1/4] FetchCatalogItems")
    print("-" * 60)
    fetch_input = {"bucket": BUCKET_NAME, "prefix": CATALOG_PREFIX}
    fetch_output = fetch_mod.handler(fetch_input, None)
    print(f"  Fetched {fetch_output['total_fetched']} items from S3")
    for item in fetch_output["items"]:
        print(f"    - {item['key']}  (item_id: {item['data'].get('item_id', '?')})")

    # ---- State 2: ClassifyItems (Map state - parallel per item) ----
    print("\n" + "-" * 60)
    print("[Step 2/4] ClassifyItems  (Map state)")
    print("-" * 60)
    classified_items = []
    for item in fetch_output["items"]:
        cls_output = classify_mod.handler(item, None)
        classified_items.append(cls_output)
        cls = cls_output["classification"]
        print(f"    - {cls_output['data'].get('item_id', '?')}  "
              f"image=\"{cls_output['data'].get('image', '')}\"  ->  "
              f"{cls['category']} (confidence: {cls['confidence']:.2f}, "
              f"status: {cls['classification_status']})")

    # Merge classified_items back into the payload (mimics Map state ResultPath)
    classify_output = dict(fetch_output)
    classify_output["classified_items"] = classified_items

    # ---- State 3: CheckDataQuality ----
    print("\n" + "-" * 60)
    print("[Step 3/4] CheckDataQuality")
    print("-" * 60)
    quality_output = quality_mod.handler(classify_output, None)
    print(f"  Total: {quality_output['total_items']}  |  "
          f"Passed: {quality_output['passed']}  |  "
          f"Failed: {quality_output['failed']}")
    for r in quality_output["quality_results"]:
        status = "PASS" if r["quality"] == "PASS" else "FAIL"
        cls_info = r.get("classification", {})
        cls_str = (f"  class={cls_info.get('category', '?')} "
                   f"conf={cls_info.get('confidence', 0):.2f}"
                   if cls_info else "")
        print(f"    [{status}] {r['item_id']} - {r['name']}  "
              f"(labels: {r['total_labels']}, empty: {len(r['empty_labels'])}){cls_str}")
        for issue in r["issues"]:
            print(f"           Issue: {issue}")

    # ---- State 4: Choice -> WriteReport ----
    print("\n" + "-" * 60)
    if quality_output["failed"] > 0:
        print("[Step 4/4] WriteFailureReport  (failures detected)")
    else:
        print("[Step 4/4] WriteSuccessReport  (all items passed)")
    print("-" * 60)
    write_output = write_mod.handler(quality_output, None)
    print(f"  Report written to: s3://{write_output['report_bucket']}/{write_output['report_key']}")
    print(f"  Summary: {json.dumps(write_output['summary'], indent=4)}")

    # Verify by reading the report back from S3
    report_body = s3.get_object(
        Bucket=BUCKET_NAME, Key=write_output["report_key"]
    )["Body"].read()
    report = json.loads(report_body)

    print("\n" + "=" * 60)
    print("  FULL QUALITY REPORT (from S3)")
    print("=" * 60)
    print(json.dumps(report, indent=2))

    print("\n" + "=" * 60)
    print("  WORKFLOW COMPLETE  -  State: Succeed")
    print("=" * 60)

    return write_output


if __name__ == "__main__":
    run_workflow()
