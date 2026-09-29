# Design Document — Catalog Data Quality Checker

> AWS Step Functions Workflow with Image Classification  
> Version: 1.0 | Date: 2026-04-19

---

## Table of Contents

1. [Context](#1-context)
2. [Problem Statement](#2-problem-statement)
3. [Solution Overview](#3-solution-overview)
4. [Architecture](#4-architecture)
5. [Workflow States](#5-workflow-states)
6. [Component Details](#6-component-details)
7. [Data Model](#7-data-model)
8. [Technology Stack](#8-technology-stack)
9. [Fault Tolerance & Scalability](#9-fault-tolerance--scalability)
10. [Testing Strategy](#10-testing-strategy)
11. [Deployment](#11-deployment)
12. [Project Structure](#12-project-structure)
13. [Future Considerations](#13-future-considerations)

---

## 1. Context

An e-commerce platform maintains a catalog of products stored as JSON files in Amazon S3. Each catalog item contains metadata (name, price, labels) and an `image` field — a short text descriptor representing the product image. The catalog grows continuously and must be audited for data quality at scale.

Manual quality reviews do not scale. The business needs an automated, repeatable pipeline that can:

- Ingest the full catalog from S3
- Classify each item into a product category
- Validate data completeness and classification quality
- Produce a structured quality report

---

## 2. Problem Statement

**Primary goal:** Build a serverless, scalable, fault-tolerant pipeline that automatically evaluates catalog data quality — including both label completeness and product classification confidence — and writes a structured report to S3.

**Specific requirements:**

| Requirement | Detail |
|-------------|--------|
| **Automated ingestion** | Read all catalog JSON files from an S3 prefix |
| **Classification** | Assign each item a product category with a confidence score |
| **Quality validation** | Flag items with missing/empty labels or low classification confidence |
| **Scalability** | Handle catalogs from 5 to 100,000+ items without code changes |
| **Fault tolerance** | A single bad item must not abort the entire workflow |
| **Idempotent reports** | Concurrent executions must not overwrite each other's reports |
| **Infrastructure as Code** | All resources defined in CloudFormation for repeatable deployment |

---

## 3. Solution Overview

The solution is an **AWS Step Functions** state machine that orchestrates four AWS Lambda functions in sequence, with a parallel **Map** state for per-item classification:

```
S3 Catalog  →  Fetch  →  Classify (parallel)  →  Quality Check  →  Report to S3
```

Step Functions was chosen over alternatives because:

| Alternative | Why not |
|-------------|---------|
| Single monolithic Lambda | No visibility into individual steps; hard to retry partial failures; 15-min timeout limits scale |
| SQS + Lambda fan-out | More complex to orchestrate; requires managing completion signals |
| AWS Batch | Designed for long-running compute (minutes–hours); catalog classification takes milliseconds per item |
| ECS / Fargate | Over-provisioned for a stateless, event-driven workload |

---

## 4. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  AWS Step Functions                       │
│                                                          │
│  ┌──────────────┐    ┌──────────────────┐                │
│  │ FetchCatalog │───▶│  ClassifyItems   │                │
│  │   (Lambda)   │    │  (Map State)     │                │
│  └──────────────┘    │                  │                │
│                      │  ┌────────────┐  │                │
│                      │  │ Classify   │  │  x N items     │
│                      │  │ SingleItem │  │  (parallel)    │
│                      │  │ (Lambda)   │  │                │
│                      │  └────────────┘  │                │
│                      └────────┬─────────┘                │
│                               │                          │
│                      ┌────────▼─────────┐                │
│                      │ CheckDataQuality │                │
│                      │    (Lambda)      │                │
│                      └────────┬─────────┘                │
│                               │                          │
│                      ┌────────▼─────────┐                │
│                      │   HasFailures    │                │
│                      │   (Choice)       │                │
│                      └───┬──────────┬───┘                │
│                     yes  │          │ no                  │
│               ┌──────────▼┐   ┌────▼──────────┐         │
│               │ WriteFail │   │ WriteSuccess   │         │
│               │  Report   │   │   Report       │         │
│               │ (Lambda)  │   │  (Lambda)      │         │
│               └─────┬─────┘   └──────┬─────────┘        │
│                     │                │                    │
│                     └──────┬─────────┘                   │
│                      ┌─────▼──────┐                      │
│                      │  Complete  │                      │
│                      │ (Succeed)  │                      │
│                      └────────────┘                      │
│                                                          │
└─────────────────────────────────────────────────────────┘
                          │
                  ┌───────▼───────┐
                  │   Amazon S3   │
                  │ ┌───────────┐ │
                  │ │ catalog/  │ │  ← input JSON files
                  │ │ reports/  │ │  ← output quality reports
                  │ └───────────┘ │
                  └───────────────┘
```

---

## 5. Workflow States

| # | State | Type | Purpose |
|---|-------|------|---------|
| 1 | **FetchCatalogItems** | Task (Lambda) | Read all catalog JSONs from S3 using paginated listing |
| 2 | **ClassifyItems** | Map | Fan out: invoke classifier Lambda once per item, in parallel (max 10 concurrent) |
| 2a | └ **ClassifySingleItem** | Task (Lambda) | Classify one item via keyword taxonomy; return category + confidence |
| 3 | **CheckDataQuality** | Task (Lambda) | Validate each item's labels and classification confidence |
| 4 | **HasFailures** | Choice | Route to failure or success report writer |
| 5a | **WriteFailureReport** | Task (Lambda) | Write report (failures found) to S3 |
| 5b | **WriteSuccessReport** | Task (Lambda) | Write report (all passed) to S3 |
| 6 | **WorkflowComplete** | Succeed | Terminal state |

---

## 6. Component Details

### 6.1 FetchCatalogItems (`lambdas/fetch_catalog/handler.py`)

- **Input:** `{ "bucket": "...", "prefix": "catalog/" }`
- **Output:** `{ "bucket", "prefix", "items": [{ "key", "data" }], "total_fetched" }`
- Uses S3 paginator to handle catalogs with >1,000 items
- Filters for `.json` files only

### 6.2 ClassifyImage (`lambdas/classify_image/handler.py`)

- **Input:** A single item `{ "key", "data": { "image": "...", ... } }`
- **Output:** Original item enriched with `classification: { category, confidence, matched_keywords, all_scores, classification_status }`
- **Algorithm:** Keyword taxonomy matching
  - Six categories with curated keyword lists and base confidence weights
  - Tokenises the `image` field, matches tokens against each category's keywords
  - Falls back to substring matching for compound words
  - Confidence = `base_confidence × (0.5 + 0.5 × match_ratio)`
  - Statuses: `OK` (≥0.5 confidence), `LOW_CONFIDENCE` (<0.5), `FAILED` (missing text)
- **Dependencies:** Python stdlib only (`re`)

### 6.3 CheckDataQuality (`lambdas/check_quality/handler.py`)

- **Input:** Full payload with `classified_items` (from Map) or raw `items` (fallback)
- **Output:** Adds `quality_results`, `total_items`, `passed`, `failed` counts
- **Quality rules:**

| Rule | Threshold | Issue Code |
|------|-----------|------------|
| Item has zero labels | 0 | `NO_LABELS` |
| Label count below minimum | <3 | `LOW_LABEL_COUNT` |
| Labels with blank values | any | `EMPTY_LABELS` |
| Classification failed | status=FAILED | `CLASSIFICATION_FAILED` |
| Classification confidence low | <0.5 | `LOW_CLASSIFICATION_CONFIDENCE` |

### 6.4 WriteResults (`lambdas/write_results/handler.py`)

- **Input:** Output from CheckDataQuality
- **Output:** `{ "report_bucket", "report_key", "summary" }`
- Writes a JSON report to `reports/quality_report_{timestamp}_{uuid}.json`
- UUID suffix prevents collisions on concurrent executions

---

## 7. Data Model

### Catalog Item (S3 Input)

```json
{
  "item_id": "PROD-001",
  "name": "Wireless Bluetooth Headphones",
  "image": "Sony Wireless Headphones",
  "labels": {
    "category": "Electronics",
    "brand": "SoundMax",
    "color": "Black"
  },
  "price": 79.99,
  "in_stock": true
}
```

### Quality Report (S3 Output)

```json
{
  "generated_at": "20260419T120000Z",
  "total_items": 5,
  "passed": 2,
  "failed": 3,
  "pass_rate": "40.0%",
  "results": [
    {
      "item_id": "PROD-001",
      "name": "Wireless Bluetooth Headphones",
      "total_labels": 5,
      "non_empty_labels": 5,
      "empty_labels": [],
      "classification": {
        "category": "Electronics",
        "confidence": 0.7225,
        "status": "OK"
      },
      "quality": "PASS",
      "issues": []
    }
  ]
}
```

---

## 8. Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Orchestration** | AWS Step Functions | Workflow coordination, state management, retry/catch |
| **Compute** | AWS Lambda (Python 3.11) | Stateless function execution per workflow step |
| **Storage** | Amazon S3 | Catalog item storage (input) and report storage (output) |
| **IaC** | AWS CloudFormation + SAM Transform | Infrastructure definition and deployment |
| **Language** | Python 3.11 | All Lambda handlers and local tooling |
| **Classification** | Python stdlib (`re`) | Keyword taxonomy classifier — no ML dependencies |
| **Testing** | pytest + moto | Unit/integration tests with mocked AWS services |
| **Local Simulation** | moto `mock_aws` | Full workflow replay against mocked S3 |

### Key Dependencies (`requirements.txt`)

| Package | Version | Purpose |
|---------|---------|---------|
| `boto3` | ≥1.34.0 | AWS SDK for S3 operations |
| `moto` | ≥5.0.0 | AWS service mocking (S3, Lambda, IAM, Step Functions) |
| `pytest` | ≥7.0.0 | Test framework |

---

## 9. Fault Tolerance & Scalability

### Fault Tolerance

| Mechanism | Where | Behaviour |
|-----------|-------|-----------|
| **Map Retry** | `ClassifyItems` state | 3 retries with exponential backoff (2s → 4s → 8s) on `States.TaskFailed`, `Lambda.ServiceException`, `Lambda.TooManyRequestsException` |
| **Map Catch** | `ClassifyItems` state | On unrecoverable error, captures error in `$.classification_error` and continues to `CheckDataQuality` |
| **Graceful fallback** | `CheckDataQuality` Lambda | If `classified_items` is absent (Map failed entirely), falls back to raw `items` and skips classification checks |
| **Per-item isolation** | Map state | Each item is classified independently; one failure doesn't affect others |

### Scalability

| Dimension | Mechanism | Limit |
|-----------|-----------|-------|
| **Catalog size** | S3 paginator in `fetch_catalog` | No practical limit (handles >1,000 items) |
| **Classification throughput** | Map `MaxConcurrency=10` | 10 concurrent Lambda invocations (tunable) |
| **Lambda concurrency** | AWS account-level | Default 1,000 concurrent executions (can request increase) |
| **Report uniqueness** | UUID suffix on report key | No collisions on concurrent workflow runs |

---

## 10. Testing Strategy

### Test Coverage: 20 tests across 6 test classes

| Class | Tests | Scope |
|-------|-------|-------|
| `TestFetchCatalog` | 3 | S3 read, pagination, empty bucket |
| `TestCheckQuality` | 5 | Label validation rules (pass, no labels, empty values, low count, mixed) |
| `TestClassifyImage` | 7 | Category accuracy (4 categories), missing text, unrecognized text, data preservation |
| `TestCheckQualityWithClassification` | 3 | Low confidence, good classification, failed classification |
| `TestWriteResults` | 1 | S3 write and report structure |
| `TestFullPipeline` | 1 | End-to-end: Fetch → Classify → Quality → Write |

### Test Infrastructure

- All tests use **moto** `mock_aws` to mock S3 — no real AWS calls
- Tests are fully deterministic and run offline
- Fixture `s3_with_data` provisions a mocked S3 bucket with 3 test catalog items

### Running Tests

```bash
pip install -r requirements.txt
python3 -m pytest tests/test_workflow.py -v
```

### Local Simulation

```bash
python3 run_local.py
```

Runs the full 4-step workflow against mocked S3 with the 5 sample catalog items, printing step-by-step output.

---

## 11. Deployment

### Prerequisites

- AWS CLI configured with appropriate credentials
- S3 bucket for CloudFormation artefacts (Lambda code upload)

### Package & Deploy

```bash
# Package: uploads Lambda code to S3 and rewrites local Code paths to S3 URIs
aws cloudformation package \
  --template-file cloudformation/template.yaml \
  --s3-bucket <your-artefact-bucket> \
  --output-template-file packaged.yaml

# Deploy
aws cloudformation deploy \
  --template-file packaged.yaml \
  --stack-name catalog-quality-checker \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides CatalogBucketName=<your-catalog-bucket>
```

### Triggering the Workflow

```bash
aws stepfunctions start-execution \
  --state-machine-arn <StateMachineArn from stack outputs> \
  --input '{"bucket": "<your-catalog-bucket>", "prefix": "catalog/"}'
```

---

## 12. Project Structure

```
python_stepfunction/
├── cloudformation/
│   └── template.yaml              # CloudFormation + SAM template (all resources)
├── lambdas/
│   ├── fetch_catalog/
│   │   └── handler.py             # Step 1: Read catalog items from S3
│   ├── classify_image/
│   │   └── handler.py             # Step 2: Classify item via keyword taxonomy
│   ├── check_quality/
│   │   └── handler.py             # Step 3: Validate labels + classification
│   └── write_results/
│       └── handler.py             # Step 4: Write quality report to S3
├── sample_data/
│   ├── catalog_item_1.json        # Sony Wireless Headphones (Electronics)
│   ├── catalog_item_2.json        # Nike Running Shoes (Footwear)
│   ├── catalog_item_3.json        # Starbucks Coffee Beans (Food & Beverage)
│   ├── catalog_item_4.json        # Samsung TV (Electronics)
│   └── catalog_item_5.json        # iPhone (Mobile Phones)
├── statemachine/
│   └── definition.json            # Step Functions state machine definition (source of truth)
├── tests/
│   └── test_workflow.py           # 20 unit + integration tests (pytest + moto)
├── requirements.txt               # Python dependencies
├── run_local.py                   # Local workflow simulation with mocked S3
└── ISSUES_AND_ENHANCEMENTS.md     # Issues, enhancements, and blockers resolved
```

---

## 13. Future Considerations

| Area | Potential Enhancement |
|------|----------------------|
| **Classification** | Replace keyword taxonomy with a lightweight ML model (e.g., TF-IDF + logistic regression) if category count grows beyond ~20 |
| **Notifications** | Add SNS topic for workflow failures / low pass-rate alerts |
| **Scheduling** | EventBridge rule to trigger the workflow on a cron schedule or S3 event |
| **Observability** | CloudWatch dashboard for pass rate trends, classification confidence distribution |
| **Multi-category** | Support items that belong to multiple categories (e.g., "Samsung Galaxy" → Electronics + Mobile Phones) |
| **Image analysis** | Integrate Amazon Rekognition for actual image-based classification when real images are available |
| **Larger catalogs** | For catalogs >100K items, consider batching the Map state or using Step Functions Distributed Map |
