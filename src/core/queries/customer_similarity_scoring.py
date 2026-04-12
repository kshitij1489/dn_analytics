from difflib import SequenceMatcher


def similarity_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _name_tokens(value: str) -> list[str]:
    return [token for token in (value or "").split() if token]


def _sorted_token_join(tokens: list[str]) -> str:
    return " ".join(sorted(tokens))


def compute_name_similarity(left_name: str, right_name: str) -> float:
    """Compute similarity between two customer names with a confidence penalty.

    For multi-token names, uses a weighted blend of sorted-token, first-token,
    last-token, and full-string similarity. A confidence multiplier (0.75–1.0)
    penalizes short or single-token names so that e.g. an exact match on "John"
    scores lower than a near-match on "John Smith", reducing false positives
    from common short names.
    """
    left_tokens = _name_tokens(left_name)
    right_tokens = _name_tokens(right_name)
    if not left_tokens or not right_tokens:
        return 0.0

    full_similarity = similarity_ratio(left_name, right_name)
    if len(left_tokens) == 1 and len(right_tokens) == 1:
        base_similarity = full_similarity
    else:
        sorted_similarity = similarity_ratio(
            _sorted_token_join(left_tokens),
            _sorted_token_join(right_tokens),
        )
        first_similarity = similarity_ratio(left_tokens[0], right_tokens[0])

        weighted_sum = (
            sorted_similarity * 0.35 +
            first_similarity * 0.25 +
            full_similarity * 0.20
        )
        if len(left_tokens) > 1 and len(right_tokens) > 1:
            weighted_sum += similarity_ratio(left_tokens[-1], right_tokens[-1]) * 0.20
        else:
            weighted_sum += full_similarity * 0.20
        base_similarity = weighted_sum

    max_token_count = max(len(left_tokens), len(right_tokens), 1)
    max_char_count = max(
        sum(len(token) for token in left_tokens),
        sum(len(token) for token in right_tokens),
        1,
    )
    token_richness = min(max_token_count, 3) / 3.0
    char_richness = min(max_char_count, 12) / 12.0
    confidence = 0.75 + 0.25 * ((token_richness * 0.60) + (char_richness * 0.40))
    return min(1.0, max(0.0, base_similarity * confidence))


def build_similarity_candidate(left_record: dict, right_record: dict, text_similarity: float, model_name: str):
    def numeric_closeness(left_value, right_value) -> float:
        left_float = float(left_value or 0.0)
        right_float = float(right_value or 0.0)
        denominator = max(abs(left_float), abs(right_float), 1.0)
        return max(0.0, 1.0 - abs(left_float - right_float) / denominator)

    def customer_rank(record: dict):
        try:
            customer_id_rank = -int(record["customer_id"])
        except (TypeError, ValueError):
            customer_id_rank = 0
        return (
            1 if record.get("is_verified") else 0,
            int(record.get("total_orders") or 0),
            float(record.get("total_spent") or 0.0),
            1 if record.get("phone_norm") else 0,
            customer_id_rank,
        )

    name_similarity = compute_name_similarity(left_record["name_norm"], right_record["name_norm"])
    address_similarity = similarity_ratio(left_record["address_norm"], right_record["address_norm"])
    phone_exact = bool(left_record["phone_norm"] and left_record["phone_norm"] == right_record["phone_norm"])
    behavior_similarity = (
        numeric_closeness(left_record["total_orders"], right_record["total_orders"]) +
        numeric_closeness(left_record["total_spent"], right_record["total_spent"])
    ) / 2.0
    score = (
        text_similarity * 0.45 +
        name_similarity * 0.25 +
        address_similarity * 0.15 +
        behavior_similarity * 0.15 +
        (0.20 if phone_exact else 0.0)
    )
    if phone_exact:
        score = max(score, 0.88)
    score = min(score, 0.99)

    reasons = []
    if phone_exact:
        reasons.append("Exact phone match")
    if name_similarity >= 0.85:
        reasons.append("Very similar customer names")
    if address_similarity >= 0.80:
        reasons.append("Very similar saved addresses")
    if behavior_similarity >= 0.75:
        reasons.append("Similar order count / spend profile")
    if text_similarity >= 0.80:
        reasons.append("Strong text similarity across name, phone, and address")
    if not reasons:
        reasons.append("High overall similarity score")

    source_record, target_record = left_record, right_record
    if customer_rank(left_record) > customer_rank(right_record):
        source_record, target_record = right_record, left_record
    elif customer_rank(left_record) == customer_rank(right_record):
        try:
            if int(left_record["customer_id"]) < int(right_record["customer_id"]):
                source_record, target_record = right_record, left_record
        except (TypeError, ValueError):
            pass

    return {
        "source_customer": {
            key: source_record[key]
            for key in ("customer_id", "name", "phone", "address", "total_orders", "total_spent", "last_order_date", "is_verified")
        },
        "target_customer": {
            key: target_record[key]
            for key in ("customer_id", "name", "phone", "address", "total_orders", "total_spent", "last_order_date", "is_verified")
        },
        "score": round(score, 4),
        "model_name": model_name,
        "reasons": reasons,
        "metrics": {
            "text_similarity": round(text_similarity, 4),
            "name_similarity": round(name_similarity, 4),
            "address_similarity": round(address_similarity, 4),
            "behavior_similarity": round(behavior_similarity, 4),
            "phone_exact_match": 1.0 if phone_exact else 0.0,
        },
    }
