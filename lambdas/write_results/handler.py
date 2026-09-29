import json
import uuid
from datetime import datetime, timezone

import boto3


def handler(event, context):
    """
    Write the quality report JSON back to S3.
    Input: output from check_quality Lambda
    Output: { "report_key": "...", "summary": {...} }
    """
    s3 = boto3.client("s3")
    bucket = event["bucket"]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    unique_id = uuid.uuid4().hex[:8]
    report_key = f"reports/quality_report_{timestamp}_{unique_id}.json"

    report = {
        "generated_at": timestamp,
        "total_items": event["total_items"],
        "passed": event["passed"],
        "failed": event["failed"],
        "pass_rate": (
            f"{event['passed'] / event['total_items'] * 100:.1f}%"
            if event["total_items"] > 0
            else "N/A"
        ),
        "results": event["quality_results"],
    }

    s3.put_object(
        Bucket=bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2),
        ContentType="application/json",
    )

    return {
        "report_bucket": bucket,
        "report_key": report_key,
        "summary": {
            "total_items": event["total_items"],
            "passed": event["passed"],
            "failed": event["failed"],
            "pass_rate": report["pass_rate"],
        },
    }
