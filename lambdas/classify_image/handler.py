import re


# Keyword taxonomy: category -> (keywords, base_confidence)
# Keywords are matched against the "image" text field.
CATEGORY_TAXONOMY = {
    "Electronics": {
        "keywords": [
            "tv", "television", "monitor", "headphones", "earbuds", "speaker",
            "camera", "laptop", "tablet", "samsung", "sony", "lg", "bose",
            "bluetooth", "wireless", "audio", "video", "remote", "charger",
        ],
        "base_confidence": 0.85,
    },
    "Mobile Phones": {
        "keywords": [
            "iphone", "android", "phone", "smartphone", "galaxy", "pixel",
            "oneplus", "xiaomi", "huawei", "mobile", "cellular",
        ],
        "base_confidence": 0.90,
    },
    "Footwear": {
        "keywords": [
            "shoes", "sneakers", "boots", "sandals", "slippers", "nike",
            "adidas", "puma", "reebok", "running", "walking", "hiking",
        ],
        "base_confidence": 0.85,
    },
    "Food & Beverage": {
        "keywords": [
            "coffee", "tea", "beans", "organic", "snack", "chocolate",
            "juice", "water", "starbucks", "nestle", "food", "drink",
        ],
        "base_confidence": 0.80,
    },
    "Fitness": {
        "keywords": [
            "yoga", "mat", "gym", "dumbbell", "resistance", "fitness",
            "exercise", "treadmill", "weights", "band",
        ],
        "base_confidence": 0.80,
    },
    "Home & Kitchen": {
        "keywords": [
            "bottle", "thermos", "mug", "pan", "pot", "knife", "blender",
            "toaster", "kettle", "utensil", "kitchen", "home",
        ],
        "base_confidence": 0.75,
    },
}


def _classify(image_text):
    """
    Classify a catalog item based on its image text descriptor.

    Uses keyword taxonomy matching with confidence scoring.
    Returns the top category and confidence, plus all scored categories.
    """
    if not image_text or not image_text.strip():
        return {
            "category": "Unknown",
            "confidence": 0.0,
            "all_scores": {},
            "classification_status": "FAILED",
            "reason": "MISSING_IMAGE_TEXT",
        }

    text_lower = image_text.lower().strip()
    tokens = set(re.findall(r"[a-z0-9]+", text_lower))

    scores = {}

    for category, config in CATEGORY_TAXONOMY.items():
        matched = tokens.intersection(config["keywords"])
        if not matched:
            # Check substring matches for compound words
            matched = {
                kw for kw in config["keywords"]
                if kw in text_lower
            }

        if matched:
            # Confidence = base * (matched_ratio capped at 1.0)
            match_ratio = min(len(matched) / max(len(tokens), 1), 1.0)
            confidence = round(config["base_confidence"] * (0.5 + 0.5 * match_ratio), 4)
            scores[category] = {
                "confidence": confidence,
                "matched_keywords": sorted(matched),
            }

    if not scores:
        return {
            "category": "Uncategorized",
            "confidence": 0.0,
            "all_scores": {},
            "classification_status": "LOW_CONFIDENCE",
            "reason": "NO_KEYWORD_MATCH",
        }

    best_category = max(scores, key=lambda c: scores[c]["confidence"])
    best = scores[best_category]

    return {
        "category": best_category,
        "confidence": best["confidence"],
        "matched_keywords": best["matched_keywords"],
        "all_scores": {
            cat: info["confidence"] for cat, info in scores.items()
        },
        "classification_status": "OK" if best["confidence"] >= 0.5 else "LOW_CONFIDENCE",
    }


def handler(event, context):
    """
    Classify a single catalog item based on its image text.

    Designed to be called from a Step Functions Map state for parallel
    processing with built-in retry/catch per item.

    Input:  { "key": "catalog/item.json", "data": { "image": "Samsung TV", ... } }
    Output: the same item enriched with "classification" results
    """
    data = event.get("data", {})
    image_text = data.get("image", "")

    classification = _classify(image_text)

    return {
        "key": event.get("key", ""),
        "data": data,
        "classification": classification,
    }
