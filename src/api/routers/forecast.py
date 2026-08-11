"""
Sales Forecast API Router — read-only central cache consumer (Phase 5).
"""

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends

from src.api.dependencies import ScopedReader, get_reader
from src.core.central_forecast_projection import (
    build_awaiting_action_response,
    build_revenue_forecast_response,
)
from src.core.forecast_actuals import build_revenue_history_rows
from src.core.queries.multi_store_forecast import (
    combine_revenue_forecast,
    lift_awaiting_profiles,
)
from src.core.services.weather_service import WeatherService
from src.core.utils.business_date import get_current_business_date

logger = logging.getLogger(__name__)
router = APIRouter()


def sync_weather_task(profile, city: str = "Gurugram"):
    try:
        WeatherService(profile).sync_weather_data(city)
    except Exception as e:
        logger.warning("Background weather sync failed: %s", e)


@router.get("/")
def get_sales_forecast(
    background_tasks: BackgroundTasks,
    reader: ScopedReader = Depends(get_reader),
):
    def query(conn, profile):
        # A profile removed from the central allow-list stays readable offline,
        # but opening Forecast must not mutate it through an automatic weather
        # refresh. All Stores reads read-only connections, so its weather
        # refresh stays with the single-restaurant scope.
        if profile.authorization_state == "authorized" and not reader.is_all:
            background_tasks.add_task(sync_weather_task, profile)

        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
        history_rows = build_revenue_history_rows(conn, today_date)

        central_response = build_revenue_forecast_response(
            conn, history_rows=history_rows, today_str=today_str
        )
        if central_response is not None:
            logger.info("Serving revenue forecast from central cache")
            return central_response

        empty = build_awaiting_action_response(
            conn,
            family="revenue",
            default_message="Forecast cache is empty. Run Sync DB to fetch forecasts from the central server.",
        )
        empty["historical"] = history_rows
        return empty

    return lift_awaiting_profiles(
        reader.read(query, combine_revenue_forecast), reader.is_all
    )
