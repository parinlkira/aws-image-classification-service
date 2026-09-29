# Issues, Enhancements & Blockers Resolved

> Catalog Data Quality Checker with Image Classification — AWS Step Functions  
> Last updated: 2026-04-19

---

## 1. Design Enhancements

### 1.1 Image-Based Catalog Classification (New Feature)

| Attribute | Detail |
|-----------|--------|
| **Type** | Feature Enhancement |
| **Scope** | New Lambda (`classify_image`), State Machine, Sample Data, Tests |
| **Motivation** | The original workflow only validated label completeness. There was no mechanism to classify or categorise catalog items, making it impossible to detect misclassified products or assess classification confidence at scale. |

**What was added:**

- **`classify_image` Lambda** — A keyword-taxonomy classifier that scores catalog items against six product categories (Electronics, Mobile Phones, Footwear, Food & Beverage, Fitness, Home & Kitchen) using token matching with confidence scoring.
- **Step Functions Map state** — Processes each catalog item in parallel (`MaxConcurrency=10`) with automatic retries (3 attempts, exponential backoff) and a catch-all fallthrough so a single item failure doesn't abort the workflow.
- **Classification-aware quality checks** — `check_quality` now evaluates classification confidence alongside label completeness, flagging items with `CLASSIFICATION_FAILED` or `LOW_CLASSIFICATION_CONFIDENCE` issues.
- **`image` field** added to all 5 sample catalog items.
- **13 new tests** covering classification accuracy, edge cases (missing text, unrecognized text), and quality integration with classification results. Total test count: **20**.

**Alternatives considered and rejected:**

| Approach | Verdict | Reason |
|----------|---------|--------|
| Naive Bayes | Rejected | Overkill for short product text strings (2–4 words); requires training data and adds `scikit-learn` dependency |
| BERT / Transformer | Rejected | Massive model size, cold-start latency inappropriate for Lambda, orders-of-magnitude more compute than needed |
| AWS Comprehend | Rejected | Adds external service dependency and cost; custom categories require a trained classifier |
| **Keyword Taxonomy** | **Chosen** | Zero external dependencies (stdlib `re` only), sub-millisecond per item, fully deterministic, easy to extend |

---

### 1.2 Map State for Parallel Classification (Scalability)

| Attribute | Detail |
|-----------|--------|
| **Type** | Architectural Enhancement |
| **Scope** | `statemachine/definition.json` |

The Step Functions **Map** state was chosen over alternatives for per-item parallel processing:

- **Not AWS Batch** — Batch is designed for long-running, compute-heavy jobs (minutes to hours). Classification takes milliseconds per item — Lambda is the right fit.
- **Not EMR** — Map is a native Step Functions construct, not related to Elastic MapReduce.
- **MaxConcurrency=10** caps parallel Lambda invocations, preventing throttling while still scaling horizontally.
- **Retry** with exponential backoff (2s → 4s → 8s) handles transient Lambda failures.
- **Catch** with `States.ALL` ensures one poisoned item doesn't kill the entire workflow.

---

## 2. Issues Found & Resolved

### 2.1 S3 Pagination Bug — Silent Data Truncation

| Attribute | Detail |
|-----------|--------|
| **Type** | Bug (Correctness) |
| **Severity** | High |
| **File** | `lambdas/fetch_catalog/handler.py` |

**Problem:** `s3.list_objects_v2()` returns at most 1,000 keys per call. The original implementation made a single call without handling `IsTruncated` / `ContinuationToken`. For catalogs exceeding 1,000 items, the workflow would silently process only a partial dataset — quality reports would show fewer items than actually exist, with no error or warning.

**Fix:** Replaced with `s3.get_paginator("list_objects_v2")` which automatically handles continuation tokens across all pages.

```python
# Before (broken for >1000 items)
response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
for obj in response.get("Contents", []):
    ...

# After (correct)
paginator = s3.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
    for obj in page.get("Contents", []):
        ...
```

---

### 2.2 Dead Import in `check_quality`

| Attribute | Detail |
|-----------|--------|
| **Type** | Code Quality |
| **Severity** | Low |
| **File** | `lambdas/check_quality/handler.py` |

**Problem:** `import json` was present but never used. Dead imports add confusion and can mask actual dependency requirements.

**Fix:** Removed the unused import.

---

### 2.3 Imports Inside Function Body in `run_local.py`

| Attribute | Detail |
|-----------|--------|
| **Type** | Code Quality / Best Practice |
| **Severity** | Low |
| **File** | `run_local.py` |

**Problem:** All four Lambda handler imports were inside the `run_workflow()` function. While functionally harmless, this violates PEP 8's recommendation to place imports at the top of the file and makes dependency scanning harder.

**Fix:** Moved all imports to module level (after the `sys.path` setup they depend on).

---

### 2.4 State Machine Definition Duplicated in CloudFormation

| Attribute | Detail |
|-----------|--------|
| **Type** | DRY Principle Violation |
| **Severity** | Medium |
| **File** | `cloudformation/template.yaml`, `statemachine/definition.json` |

**Problem:** The state machine definition was duplicated — once in `statemachine/definition.json` and again inline in the CloudFormation template via `DefinitionString`. Any change to the workflow required editing two files, with no guarantee they'd stay in sync.

**Fix:** Replaced `DefinitionString` with `DefinitionS3Location: ../statemachine/definition.json` and `DefinitionSubstitutions` to map placeholder ARNs to CloudFormation resource references. Now `definition.json` is the single source of truth.

---

### 2.5 Overly Broad CloudWatch Logs IAM Policy

| Attribute | Detail |
|-----------|--------|
| **Type** | Security |
| **Severity** | Medium |
| **File** | `cloudformation/template.yaml` |

**Problem:** The CloudWatch Logs policy used `arn:aws:logs:*:*:*` — granting log access across all regions and all AWS accounts. This violates the principle of least privilege.

**Fix:** Scoped to the deployment region and account: `arn:aws:logs:${AWS::Region}:${AWS::AccountId}:*`.

---

### 2.6 Report Key Collision on Concurrent Executions

| Attribute | Detail |
|-----------|--------|
| **Type** | Bug (Concurrency) |
| **Severity** | Medium |
| **File** | `lambdas/write_results/handler.py` |

**Problem:** The report S3 key used only a second-precision timestamp (`quality_report_20260418T191100Z.json`). If two workflow executions completed within the same second, one report would silently overwrite the other.

**Fix:** Appended an 8-character `uuid4` hex suffix: `quality_report_20260418T191126Z_4715a1e8.json`.

---

### 2.7 Inline Lambda Code in CloudFormation (ZipFile Anti-Pattern)

| Attribute | Detail |
|-----------|--------|
| **Type** | Maintainability / DRY |
| **Severity** | High |
| **File** | `cloudformation/template.yaml` |

**Problem:** The original CloudFormation template embedded full Lambda handler code inline using `ZipFile`. This duplicated every handler (the code existed both in `lambdas/*/handler.py` and inside the YAML), making maintenance error-prone and diffs unreadable.

**Fix:** Replaced all `ZipFile` blocks with `Code: ../lambdas/<function_name>` references. Deployment now requires `aws cloudformation package` to upload code to S3, which is the standard pattern for production Lambda deployments.

---

## 3. Summary

| Category | Count |
|----------|-------|
| New features | 1 (Image classification pipeline) |
| Bugs fixed | 2 (S3 pagination, report key collision) |
| Security fixes | 1 (IAM scope) |
| DRY / maintainability | 2 (definition dedup, ZipFile removal) |
| Code quality | 2 (dead import, import placement) |
| **Total items resolved** | **8** |

All fixes verified: **20/20 tests passing**, local simulation completes successfully.
