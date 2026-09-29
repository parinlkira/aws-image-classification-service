"""
test_workflow.py - Unit and integration tests for the catalog quality workflow.

Uses moto to mock S3, then verifies each Lambda and the full pipeline.
"""

import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "fetch_catalog"))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "classify_image"))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "check_quality"))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "write_results"))

BUCKET = "test-catalog-bucket"
PREFIX = "catalog/"


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


@pytest.fixture
def s3_with_data(aws_env):
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)

        items = [
            {
                "item_id": "T-001",
                "name": "Good Item",
                "image": "Sony Wireless Headphones",
                "labels": {"a": "1", "b": "2", "c": "3"},
                "price": 10,
            },
            {
                "item_id": "T-002",
                "name": "Bad Item",
                "image": "Nike Running Shoes",
                "labels": {"a": ""},
                "price": 20,
            },
            {
                "item_id": "T-003",
                "name": "No Labels",
                "image": "Starbucks Coffee Beans",
                "labels": {},
                "price": 30,
            },
        ]

        for i, item in enumerate(items):
            s3.put_object(
                Bucket=BUCKET,
                Key=f"{PREFIX}item_{i}.json",
                Body=json.dumps(item),
            )

        yield s3


# ---- Fetch Catalog Tests ----

class TestFetchCatalog:
    def test_fetches_all_items(self, s3_with_data):
        from lambdas.fetch_catalog.handler import handler

        result = handler({"bucket": BUCKET, "prefix": PREFIX}, None)
        assert result["total_fetched"] == 3
        assert len(result["items"]) == 3

    def test_returns_correct_bucket(self, s3_with_data):
        from lambdas.fetch_catalog.handler import handler

        result = handler({"bucket": BUCKET, "prefix": PREFIX}, None)
        assert result["bucket"] == BUCKET

    def test_empty_bucket(self, aws_env):
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=BUCKET)
            from lambdas.fetch_catalog.handler import handler

            result = handler({"bucket": BUCKET, "prefix": PREFIX}, None)
            assert result["total_fetched"] == 0


# ---- Check Quality Tests ----

class TestCheckQuality:
    def test_good_item_passes(self):
        from lambdas.check_quality.handler import handler

        event = {
            "bucket": BUCKET,
            "items": [
                {
                    "key": "k",
                    "data": {
                        "item_id": "X",
                        "name": "OK",
                        "labels": {"a": "1", "b": "2", "c": "3"},
                    },
                }
            ],
        }
        result = handler(event, None)
        assert result["passed"] == 1
        assert result["failed"] == 0

    def test_no_labels_fails(self):
        from lambdas.check_quality.handler import handler

        event = {
            "bucket": BUCKET,
            "items": [
                {"key": "k", "data": {"item_id": "X", "name": "Bad", "labels": {}}}
            ],
        }
        result = handler(event, None)
        assert result["failed"] == 1
        assert "NO_LABELS" in result["quality_results"][0]["issues"][0]

    def test_empty_label_values_detected(self):
        from lambdas.check_quality.handler import handler

        event = {
            "bucket": BUCKET,
            "items": [
                {
                    "key": "k",
                    "data": {
                        "item_id": "X",
                        "name": "Bad",
                        "labels": {"a": "", "b": "ok", "c": ""},
                    },
                }
            ],
        }
        result = handler(event, None)
        r = result["quality_results"][0]
        assert set(r["empty_labels"]) == {"a", "c"}

    def test_low_label_count(self):
        from lambdas.check_quality.handler import handler

        event = {
            "bucket": BUCKET,
            "items": [
                {
                    "key": "k",
                    "data": {
                        "item_id": "X",
                        "name": "Low",
                        "labels": {"a": "1"},
                    },
                }
            ],
        }
        result = handler(event, None)
        assert result["failed"] == 1
        assert "LOW_LABEL_COUNT" in result["quality_results"][0]["issues"][0]

    def test_mixed_results(self):
        from lambdas.check_quality.handler import handler

        event = {
            "bucket": BUCKET,
            "items": [
                {
                    "key": "k1",
                    "data": {
                        "item_id": "A",
                        "name": "Good",
                        "labels": {"x": "1", "y": "2", "z": "3"},
                    },
                },
                {
                    "key": "k2",
                    "data": {"item_id": "B", "name": "Bad", "labels": {}},
                },
            ],
        }
        result = handler(event, None)
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["total_items"] == 2


# ---- Classify Image Tests ----

class TestClassifyImage:
    def test_classifies_electronics(self):
        from lambdas.classify_image.handler import handler

        event = {"key": "k", "data": {"image": "Samsung TV", "item_id": "X"}}
        result = handler(event, None)
        assert result["classification"]["category"] == "Electronics"
        assert result["classification"]["confidence"] > 0.5
        assert result["classification"]["classification_status"] == "OK"

    def test_classifies_mobile_phones(self):
        from lambdas.classify_image.handler import handler

        event = {"key": "k", "data": {"image": "iPhone", "item_id": "X"}}
        result = handler(event, None)
        assert result["classification"]["category"] == "Mobile Phones"
        assert result["classification"]["confidence"] > 0.5

    def test_classifies_footwear(self):
        from lambdas.classify_image.handler import handler

        event = {"key": "k", "data": {"image": "Nike Running Shoes", "item_id": "X"}}
        result = handler(event, None)
        assert result["classification"]["category"] == "Footwear"
        assert result["classification"]["confidence"] > 0.5

    def test_classifies_food(self):
        from lambdas.classify_image.handler import handler

        event = {"key": "k", "data": {"image": "Starbucks Coffee Beans", "item_id": "X"}}
        result = handler(event, None)
        assert result["classification"]["category"] == "Food & Beverage"
        assert result["classification"]["confidence"] > 0.5

    def test_missing_image_text(self):
        from lambdas.classify_image.handler import handler

        event = {"key": "k", "data": {"item_id": "X"}}
        result = handler(event, None)
        assert result["classification"]["classification_status"] == "FAILED"
        assert result["classification"]["category"] == "Unknown"
        assert result["classification"]["confidence"] == 0.0

    def test_unrecognized_image_text(self):
        from lambdas.classify_image.handler import handler

        event = {"key": "k", "data": {"image": "xyzzy foobar", "item_id": "X"}}
        result = handler(event, None)
        assert result["classification"]["category"] == "Uncategorized"
        assert result["classification"]["classification_status"] == "LOW_CONFIDENCE"

    def test_preserves_item_data(self):
        from lambdas.classify_image.handler import handler

        data = {"image": "Samsung TV", "item_id": "X", "price": 999}
        event = {"key": "catalog/x.json", "data": data}
        result = handler(event, None)
        assert result["data"] == data
        assert result["key"] == "catalog/x.json"


# ---- Check Quality Tests (with classification) ----

class TestCheckQualityWithClassification:
    def test_low_classification_confidence_fails(self):
        from lambdas.check_quality.handler import handler

        event = {
            "bucket": BUCKET,
            "classified_items": [
                {
                    "key": "k",
                    "data": {
                        "item_id": "X",
                        "name": "OK",
                        "labels": {"a": "1", "b": "2", "c": "3"},
                    },
                    "classification": {
                        "category": "Uncategorized",
                        "confidence": 0.0,
                        "classification_status": "LOW_CONFIDENCE",
                    },
                }
            ],
        }
        result = handler(event, None)
        assert result["failed"] == 1
        assert any("LOW_CLASSIFICATION_CONFIDENCE" in i for i in result["quality_results"][0]["issues"])

    def test_good_classification_passes(self):
        from lambdas.check_quality.handler import handler

        event = {
            "bucket": BUCKET,
            "classified_items": [
                {
                    "key": "k",
                    "data": {
                        "item_id": "X",
                        "name": "OK",
                        "labels": {"a": "1", "b": "2", "c": "3"},
                    },
                    "classification": {
                        "category": "Electronics",
                        "confidence": 0.85,
                        "classification_status": "OK",
                    },
                }
            ],
        }
        result = handler(event, None)
        assert result["passed"] == 1
        assert result["failed"] == 0

    def test_classification_failed_adds_issue(self):
        from lambdas.check_quality.handler import handler

        event = {
            "bucket": BUCKET,
            "classified_items": [
                {
                    "key": "k",
                    "data": {
                        "item_id": "X",
                        "name": "OK",
                        "labels": {"a": "1", "b": "2", "c": "3"},
                    },
                    "classification": {
                        "category": "Unknown",
                        "confidence": 0.0,
                        "classification_status": "FAILED",
                        "reason": "MISSING_IMAGE_TEXT",
                    },
                }
            ],
        }
        result = handler(event, None)
        assert result["failed"] == 1
        assert any("CLASSIFICATION_FAILED" in i for i in result["quality_results"][0]["issues"])


# ---- Write Results Tests ----

class TestWriteResults:
    def test_writes_report_to_s3(self, aws_env):
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=BUCKET)

            from lambdas.write_results.handler import handler

            event = {
                "bucket": BUCKET,
                "total_items": 2,
                "passed": 1,
                "failed": 1,
                "quality_results": [
                    {"item_id": "A", "quality": "PASS"},
                    {"item_id": "B", "quality": "FAIL"},
                ],
            }
            result = handler(event, None)
            assert result["report_key"].startswith("reports/")
            assert result["summary"]["passed"] == 1

            # Verify report exists in S3
            body = s3.get_object(
                Bucket=BUCKET, Key=result["report_key"]
            )["Body"].read()
            report = json.loads(body)
            assert report["total_items"] == 2
            assert report["pass_rate"] == "50.0%"


# ---- Full Pipeline Integration Test ----

class TestFullPipeline:
    def test_end_to_end(self, s3_with_data):
        from lambdas.fetch_catalog.handler import handler as fetch_handler
        from lambdas.classify_image.handler import handler as classify_handler
        from lambdas.check_quality.handler import handler as quality_handler
        from lambdas.write_results.handler import handler as write_handler

        # Step 1: Fetch
        step1 = fetch_handler({"bucket": BUCKET, "prefix": PREFIX}, None)
        assert step1["total_fetched"] == 3

        # Step 2: Classify (simulates Map state)
        classified_items = []
        for item in step1["items"]:
            cls_result = classify_handler(item, None)
            classified_items.append(cls_result)
        assert len(classified_items) == 3
        # Verify all items got classifications
        for item in classified_items:
            assert "classification" in item

        step2_input = dict(step1)
        step2_input["classified_items"] = classified_items

        # Step 3: Check quality
        step3 = quality_handler(step2_input, None)
        assert step3["total_items"] == 3
        assert step3["passed"] == 1  # Only T-001 passes (good labels + good classification)
        assert step3["failed"] == 2  # T-002 (low count + empty) and T-003 (no labels)

        # Step 4 (Choice): failures > 0 -> WriteFailureReport
        assert step3["failed"] > 0

        # Step 4: Write report
        step4 = write_handler(step3, None)
        assert "report_key" in step4
        assert step4["summary"]["total_items"] == 3
