from typing import List, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

from src.core.queries.customer_similarity_helpers import fetch_active_similarity_population, fetch_customer_summary
from src.core.queries.customer_similarity_scoring import build_similarity_candidate, similarity_ratio


def fetch_customer_similarity_candidates(conn, limit: int = 20, min_score: float = 0.72):
    model_name = "basic_duplicate_knn_v1"
    population = fetch_active_similarity_population(conn)
    if len(population) < 2:
        return []

    documents = [record["feature_text"] for record in population]
    tfidf_matrix = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1).fit_transform(documents)

    neighbor_count = min(6, len(population))
    distances, indices = NearestNeighbors(metric="cosine", algorithm="brute", n_neighbors=neighbor_count).fit(tfidf_matrix).kneighbors(tfidf_matrix)

    best_pairs = {}

    def register_pair(left_record, right_record, text_similarity):
        candidate = build_similarity_candidate(left_record, right_record, text_similarity, model_name)
        metrics = candidate["metrics"]
        should_keep = (
            candidate["score"] >= min_score or
            metrics["phone_exact_match"] == 1.0 or
            (metrics["name_similarity"] >= 0.80 and metrics["address_similarity"] >= 0.70)
        )
        if not should_keep:
            return

        pair_key = tuple(sorted([left_record["customer_id"], right_record["customer_id"]]))
        previous = best_pairs.get(pair_key)
        if previous is None or candidate["score"] > previous["score"]:
            best_pairs[pair_key] = candidate

    for row_index, neighbor_indexes in enumerate(indices):
        for distance, neighbor_index in zip(distances[row_index], neighbor_indexes):
            if neighbor_index != row_index:
                register_pair(population[row_index], population[neighbor_index], max(0.0, 1.0 - float(distance)))

    phone_groups = {}
    for record in population:
        if record["phone_norm"]:
            phone_groups.setdefault(record["phone_norm"], []).append(record)

    for group in phone_groups.values():
        for left_index in range(len(group)):
            for right_index in range(left_index + 1, len(group)):
                register_pair(group[left_index], group[right_index], similarity_ratio(group[left_index]["feature_text"], group[right_index]["feature_text"]))

    suggestions = sorted(
        best_pairs.values(),
        key=lambda item: (item["score"], item["target_customer"]["total_orders"], item["target_customer"]["total_spent"]),
        reverse=True,
    )
    return suggestions[:limit]


def fetch_customer_merge_preview(
    conn,
    source_customer_id: str,
    target_customer_id: str,
    similarity_score: Optional[float] = None,
    model_name: Optional[str] = None,
    reasons: Optional[List[str]] = None,
):
    source_summary = fetch_customer_summary(conn, source_customer_id)
    target_summary = fetch_customer_summary(conn, target_customer_id)
    if not source_summary or not target_summary:
        return {"status": "error", "message": "One or both customers were not found."}
    if source_summary["customer_id"] == target_summary["customer_id"]:
        return {"status": "error", "message": "Source and target customers must be different."}
    if source_summary["is_merged_source"]:
        return {"status": "error", "message": "The selected source customer has already been merged."}
    if target_summary["is_merged_source"]:
        return {"status": "error", "message": "The selected target customer is not active."}

    moved_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE customer_id = ?", (source_summary["customer_id"],)).fetchone()[0]
    if not reasons:
        candidate = build_similarity_candidate(
            source_summary,
            target_summary,
            similarity_ratio(source_summary["feature_text"], target_summary["feature_text"]),
            model_name or "basic_duplicate_knn_v1",
        )
        reasons = candidate["reasons"]
        similarity_score = similarity_score if similarity_score is not None else candidate["score"]
        model_name = model_name or candidate["model_name"]

    return {
        "source_customer": {
            key: source_summary[key]
            for key in ("customer_id", "name", "phone", "address", "total_orders", "total_spent", "last_order_date", "is_verified")
        },
        "target_customer": {
            key: target_summary[key]
            for key in ("customer_id", "name", "phone", "address", "total_orders", "total_spent", "last_order_date", "is_verified")
        },
        "orders_to_move": int(moved_orders),
        "source_address_count": int(source_summary["address_count"]),
        "target_address_count": int(target_summary["address_count"]),
        "reasons": reasons or [],
        "score": similarity_score,
        "model_name": model_name or "basic_duplicate_knn_v1",
    }
