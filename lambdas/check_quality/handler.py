# Minimum number of labels an item should have to be considered high quality
MIN_LABEL_COUNT = 3

# Minimum classification confidence to consider the classification reliable
MIN_CLASSIFICATION_CONFIDENCE = 0.5


def _check_item(item_data, classification=None):
    """
    Validate a single catalog item's labels and classification.
    Returns a quality report dict for the item.
    """
    item_id = item_data.get("item_id", "UNKNOWN")
    name = item_data.get("name", "UNKNOWN")
    labels = item_data.get("labels", {})

    total_labels = len(labels)
    empty_labels = [k for k, v in labels.items() if not str(v).strip()]
    non_empty_labels = total_labels - len(empty_labels)

    issues = []

    if total_labels == 0:
        issues.append("NO_LABELS: Item has zero labels")
    elif total_labels < MIN_LABEL_COUNT:
        issues.append(
            f"LOW_LABEL_COUNT: Item has {total_labels} labels "
            f"(minimum recommended: {MIN_LABEL_COUNT})"
        )

    if empty_labels:
        issues.append(
            f"EMPTY_LABELS: Labels with blank values: {empty_labels}"
        )

    # Evaluate classification results
    classification_report = {}
    if classification:
        cls_status = classification.get("classification_status", "MISSING")
        cls_confidence = classification.get("confidence", 0.0)
        cls_category = classification.get("category", "Unknown")

        classification_report = {
            "category": cls_category,
            "confidence": cls_confidence,
            "status": cls_status,
        }

        if cls_status == "FAILED":
            issues.append(
                f"CLASSIFICATION_FAILED: {classification.get('reason', 'unknown')}"
            )
        elif cls_confidence < MIN_CLASSIFICATION_CONFIDENCE:
            issues.append(
                f"LOW_CLASSIFICATION_CONFIDENCE: {cls_confidence:.2f} "
                f"< {MIN_CLASSIFICATION_CONFIDENCE}"
            )

    quality = "PASS" if not issues else "FAIL"

    return {
        "item_id": item_id,
        "name": name,
        "total_labels": total_labels,
        "non_empty_labels": non_empty_labels,
        "empty_labels": empty_labels,
        "classification": classification_report,
        "quality": quality,
        "issues": issues,
    }


def handler(event, context):
    """
    Check data quality of each catalog item based on label counts and
    classification confidence.

    Input: output from fetch_catalog Lambda, enriched with classified_items
           from the Map state
    Output: adds quality_results to the payload
    """
    # classified_items comes from the Map state; fall back to raw items
    classified_items = event.get("classified_items", [])
    raw_items = event.get("items", [])

    results = []

    if classified_items:
        for item in classified_items:
            data = item.get("data", {})
            classification = item.get("classification", None)
            report = _check_item(data, classification)
            results.append(report)
    else:
        for item in raw_items:
            data = item["data"]
            report = _check_item(data)
            results.append(report)

    passed = sum(1 for r in results if r["quality"] == "PASS")
    failed = sum(1 for r in results if r["quality"] == "FAIL")

    return {
        "bucket": event["bucket"],
        "prefix": event.get("prefix", ""),
        "total_items": len(results),
        "passed": passed,
        "failed": failed,
        "quality_results": results,
    }
