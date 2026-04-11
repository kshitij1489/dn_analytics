"""
Customer query facade.

Keeps legacy import paths stable while delegating to focused query modules.
"""

from src.core.queries.customer_analytics_queries import (
    fetch_brand_awareness,
    fetch_customer_loyalty,
    fetch_top_customers,
)
from src.core.queries.customer_merge_history_queries import fetch_customer_merge_history
from src.core.queries.customer_merge_queries import merge_customers, undo_customer_merge
from src.core.queries.customer_profile_queries import (
    fetch_customer_profile_data,
    search_customers,
)
from src.core.queries.customer_query_utils import format_customer_address
from src.core.queries.customer_reorder_rate_queries import fetch_customer_reorder_rate
from src.core.queries.customer_reorder_trend_queries import fetch_reorder_rate_trend
from src.core.queries.customer_similarity_queries import (
    fetch_customer_merge_preview,
    fetch_customer_similarity_candidates,
)

__all__ = [
    "fetch_brand_awareness",
    "fetch_customer_loyalty",
    "fetch_customer_merge_history",
    "fetch_customer_merge_preview",
    "fetch_customer_profile_data",
    "fetch_customer_reorder_rate",
    "fetch_customer_similarity_candidates",
    "fetch_reorder_rate_trend",
    "fetch_top_customers",
    "format_customer_address",
    "merge_customers",
    "search_customers",
    "undo_customer_merge",
]
