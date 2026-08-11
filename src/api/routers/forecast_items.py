"""
Item-Level Demand Forecast API Router — read-only central cache consumer (Phase 5).
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends

from src.api.dependencies import ScopedReader, get_reader
from src.core.central_forecast_cache import FAMILY_ITEMS
from src.core.central_forecast_projection import (
    build_awaiting_action_response,
    build_item_forecast_response,
)
from src.core.forecast_actuals import build_item_history_rows
from src.core.queries.multi_store_forecast import (
    combine_item_forecast,
    lift_awaiting_profiles,
)
from src.core.utils.business_date import get_current_business_date

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/items")
def get_item_forecast(
    item_id: Optional[str] = None,
    days: int = 14,
    reader: ScopedReader = Depends(get_reader),
):
    # In All Stores mode a combined item is identified by its durable name, so
    # the filter is applied after the stores are combined rather than against
    # each store's transient entity IDs.
    store_item_id = None if reader.is_all else item_id

    def query(conn, _profile):
        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

        items_list, history_rows = build_item_history_rows(
            conn, today_date, item_id=store_item_id
        )

        central_response = build_item_forecast_response(
            conn,
            items_list=items_list,
            history_rows=history_rows,
            item_id=store_item_id,
            days=days,
            today_str=today_str,
        )
        if central_response is not None:
            logger.info("Serving item forecast from central cache")
            return central_response

        empty = build_awaiting_action_response(
            conn,
            family=FAMILY_ITEMS,
            default_message="Item forecast cache is empty. Run Sync DB to fetch from central server.",
        )
        empty["items"] = items_list
        empty["history"] = history_rows
        return empty

    def reduce(pairs):
        return combine_item_forecast(pairs, item_name=item_id)

    return lift_awaiting_profiles(reader.read(query, reduce), reader.is_all)
