import json
import boto3


def handler(event, context):
    """
    Fetch all catalog item JSONs from S3 bucket.
    Input: { "bucket": "...", "prefix": "catalog/" }
    Output: { "bucket": "...", "items": [ { "key": "...", "data": {...} }, ... ] }
    """
    s3 = boto3.client("s3")
    bucket = event["bucket"]
    prefix = event.get("prefix", "catalog/")

    items = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            data = json.loads(body)
            items.append({"key": key, "data": data})

    return {
        "bucket": bucket,
        "prefix": prefix,
        "items": items,
        "total_fetched": len(items),
    }
