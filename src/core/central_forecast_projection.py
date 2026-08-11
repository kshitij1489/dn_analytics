"""
Project central_forecast_* row cache into chart-shaped API responses.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.core.central_forecast_cache import (
    DEFAULT_SCOPE_KEY,
    FAMILY_ITEMS,
    FAMILY_VOLUME,
    ITEM_FORWARD_DAYS,
    REVENUE_MODEL_NAMES,
    VOLUME_FORWARD_DAYS,
    ensure_central_forecast_tables,
    get_served_family_run,
    is_entity_family_run_complete,
    is_revenue_run_complete,
    load_central_forecast_rows,
    load_central_forecast_weather_map,
)
from src.core.forecast_sync import get_forecast_bootstrap_endpoint
from src.core.utils.business_date import get_current_business_date

logger = logging.getLogger(__name__)


def _normalize_volume_unit(unit: Optional[str]) -> str:
    if not unit:
        return "g"
    u = str(unit).lower()
    if u in ("units", "count"):
        return "units"
    return "g"


def _central_row_has_unit(row: Dict[str, Any]) -> bool:
    payload = row.get("payload") or {}
    return bool(row.get("unit") or payload.get("unit"))


def _merge_backtest_and_forecast(
    backtest: Dict[str, List[dict]],
    forecast: Dict[str, List[dict]],
    today_str: str,
) -> Dict[str, List[dict]]:
    merged: Dict[str, List[dict]] = {}
    for model in REVENUE_MODEL_NAMES:
        bt = backtest.get(model, [])
        fc = forecast.get(model, [])
        for row in bt:
            row.setdefault("temp_max", 0)
            row.setdefault("rain_category", "none")
        bt_past = [r for r in bt if r["date"] < today_str]
        fc_future = [r for r in fc if r["date"] >= today_str]
        combined = bt_past + fc_future
        combined.sort(key=lambda x: x["date"])
        merged[model] = combined
    return merged


def _apply_weather_overlay(
    merged: Dict[str, List[dict]],
    weather_map: Dict[str, Dict[str, Any]],
    today_str: str,
) -> None:
    """Fill forward-looking temp_max/rain_category from central_forecast_weather.

    Plan §5.3: "Prefer central_forecast_weather for forward-looking temp/rain."
    Backtest points already carry weather in their row payload; only forward
    (date >= today_str) points are missing it, since forward payloads don't
    ship weather fields (see plan §798-905 weather_rows example).
    """
    for points in merged.values():
        for point in points:
            if point["date"] < today_str:
                continue
            weather = weather_map.get(point["date"])
            if not weather:
                continue
            if weather.get("temp_max") is not None:
                point["temp_max"] = float(weather["temp_max"])
            if weather.get("rain_category"):
                point["rain_category"] = str(weather["rain_category"])


def _revenue_row_to_point(row: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    model = str(row.get("model_name") or "")
    payload = row.get("payload") or {}
    forecast_date = str(row.get("forecast_date") or "")[:10]
    point: Dict[str, Any] = {
        "date": forecast_date,
        "revenue": float(payload.get("revenue") or 0),
        "orders": int(payload.get("orders") or 0),
    }
    if model == "gp":
        lower = payload.get("gp_lower")
        if lower is None:
            lower = payload.get("lower_95")
        upper = payload.get("gp_upper")
        if upper is None:
            upper = payload.get("upper_95")
        if lower is not None:
            point["gp_lower"] = float(lower)
        if upper is not None:
            point["gp_upper"] = float(upper)
    if payload.get("temp_max") is not None:
        point["temp_max"] = float(payload["temp_max"])
    if payload.get("rain_category"):
        point["rain_category"] = str(payload["rain_category"])
    return model, point


def _group_revenue_rows(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {m: [] for m in REVENUE_MODEL_NAMES}
    for row in rows:
        model, point = _revenue_row_to_point(row)
        if model in grouped:
            grouped[model].append(point)
    for model in grouped:
        grouped[model].sort(key=lambda r: r["date"])
    return grouped


def get_served_revenue_run(
    conn,
    *,
    scope_key: str = DEFAULT_SCOPE_KEY,
    today_str: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    today_str = today_str or get_current_business_date()
    return get_served_family_run(
        conn,
        "revenue",
        scope_key=scope_key,
        today_str=today_str,
        completeness_check=is_revenue_run_complete,
    )


def build_revenue_forecast_response(
    conn,
    *,
    history_rows: List[Dict[str, Any]],
    today_str: Optional[str] = None,
    scope_key: str = DEFAULT_SCOPE_KEY,
) -> Optional[Dict[str, Any]]:
    today_str = today_str or get_current_business_date()
    run, using_fallback = get_served_revenue_run(conn, scope_key=scope_key, today_str=today_str)
    if not run:
        return None

    run_id = run["run_id"]
    forward_rows = load_central_forecast_rows(conn, run_id, family="revenue", kind="forward")
    backtest_rows = load_central_forecast_rows(conn, run_id, family="revenue", kind="backtest")

    raw_forecast = _group_revenue_rows(forward_rows)
    raw_backtest = _group_revenue_rows(backtest_rows)
    merged = _merge_backtest_and_forecast(raw_backtest, raw_forecast, today_str)
    _apply_weather_overlay(merged, load_central_forecast_weather_map(conn), today_str)

    future_weekday = [f for f in merged.get("weekday_avg", []) if f["date"] >= today_str]
    total_projected_revenue = sum(float(f.get("revenue") or 0) for f in future_weekday)
    total_projected_orders = sum(int(f.get("orders") or 0) for f in future_weekday)

    return {
        "summary": {
            "generated_at": today_str,
            "projected_7d_revenue": total_projected_revenue,
            "projected_7d_orders": total_projected_orders,
        },
        "historical": history_rows,
        "forecasts": {
            "weekday_avg": merged.get("weekday_avg", []),
            "holt_winters": merged.get("holt_winters", []),
            "prophet": merged.get("prophet", []),
            "gp": merged.get("gp", []),
        },
        "debug_info": {
            "served_from_central": True,
            "run_id": run_id,
            "using_fallback": using_fallback,
            "original_generated_on": run.get("generated_on") if using_fallback else None,
        },
    }


def _item_row_to_points(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload") or {}
    entity_name = row.get("entity_name") or payload.get("item_name") or row.get("entity_id")
    forecast_date = str(row.get("forecast_date") or "")[:10]
    return {
        "date": forecast_date,
        "item_id": row.get("entity_id"),
        "item_name": entity_name,
        "p50": round(float(payload.get("p50") or 0), 2),
        "p90": round(float(payload.get("p90") or 0), 2),
        "probability": round(float(payload.get("probability") or 0), 4),
        "recommended_prep": int(payload.get("recommended_prep") or 0),
    }


def build_item_forecast_response(
    conn,
    *,
    items_list: List[Dict[str, Any]],
    history_rows: List[Dict[str, Any]],
    item_id: Optional[str] = None,
    days: int = 14,
    today_str: Optional[str] = None,
    scope_key: str = DEFAULT_SCOPE_KEY,
) -> Optional[Dict[str, Any]]:
    today_str = today_str or get_current_business_date()
    run, using_fallback = get_served_family_run(
        conn,
        FAMILY_ITEMS,
        scope_key=scope_key,
        today_str=today_str,
        min_forward_days=ITEM_FORWARD_DAYS,
        completeness_check=lambda c, rid: is_entity_family_run_complete(
            c, rid, FAMILY_ITEMS, min_forward_days=ITEM_FORWARD_DAYS
        ),
    )
    if not run:
        return None

    run_id = run["run_id"]
    forward_rows = load_central_forecast_rows(conn, run_id, family=FAMILY_ITEMS, kind="forward")
    backtest_rows = load_central_forecast_rows(conn, run_id, family=FAMILY_ITEMS, kind="backtest")

    name_by_id = {i["item_id"]: i["item_name"] for i in items_list}
    for row in forward_rows + backtest_rows:
        eid = row.get("entity_id")
        if eid and eid not in name_by_id:
            payload = row.get("payload") or {}
            name_by_id[eid] = row.get("entity_name") or payload.get("item_name") or eid

    if not items_list and name_by_id:
        items_list = [{"item_id": k, "item_name": v} for k, v in sorted(name_by_id.items())]

    forecast_rows = []
    for row in forward_rows:
        point = _item_row_to_points(row)
        if item_id and point["item_id"] != item_id:
            continue
        if point["date"] < today_str:
            continue
        forecast_rows.append(point)

    forecast_rows.sort(key=lambda x: x["date"])
    distinct_dates = sorted({r["date"] for r in forecast_rows})
    limit_dates = set(distinct_dates[:days])
    forecast_rows = [r for r in forecast_rows if r["date"] in limit_dates]

    backtest_out = []
    for row in backtest_rows:
        point = _item_row_to_points(row)
        if item_id and point["item_id"] != item_id:
            continue
        backtest_out.append(point)
    backtest_out.sort(key=lambda x: (x["date"], x["item_id"]))

    return {
        "items": items_list,
        "history": history_rows,
        "forecast": forecast_rows,
        "backtest": backtest_out,
        "debug_info": {
            "served_from_central": True,
            "run_id": run_id,
            "using_fallback": using_fallback,
        },
    }


def _volume_row_to_points(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload") or {}
    entity_name = row.get("entity_name") or payload.get("item_name") or row.get("entity_id")
    unit = _normalize_volume_unit(row.get("unit") or payload.get("unit"))
    forecast_date = str(row.get("forecast_date") or "")[:10]
    return {
        "date": forecast_date,
        "item_id": row.get("entity_id"),
        "item_name": entity_name,
        "unit": unit,
        "p50": round(float(payload.get("p50") or 0), 2),
        "p90": round(float(payload.get("p90") or 0), 2),
        "probability": round(float(payload.get("probability") or 0), 4),
        "volume_value": round(float(payload.get("volume_value") or payload.get("p50") or 0), 2),
        "recommended_volume": round(float(payload.get("recommended_volume") or 0), 2),
    }


def build_volume_forecast_response(
    conn,
    *,
    items_list: List[Dict[str, Any]],
    history_rows: List[Dict[str, Any]],
    item_id: Optional[str] = None,
    days: int = 14,
    today_str: Optional[str] = None,
    scope_key: str = DEFAULT_SCOPE_KEY,
) -> Optional[Dict[str, Any]]:
    today_str = today_str or get_current_business_date()
    run, using_fallback = get_served_family_run(
        conn,
        FAMILY_VOLUME,
        scope_key=scope_key,
        today_str=today_str,
        min_forward_days=VOLUME_FORWARD_DAYS,
        completeness_check=lambda c, rid: is_entity_family_run_complete(
            c, rid, FAMILY_VOLUME, min_forward_days=VOLUME_FORWARD_DAYS
        ),
    )
    if not run:
        return None

    run_id = run["run_id"]
    forward_rows = load_central_forecast_rows(conn, run_id, family=FAMILY_VOLUME, kind="forward")
    backtest_rows = load_central_forecast_rows(conn, run_id, family=FAMILY_VOLUME, kind="backtest")

    unit_by_id = {i["item_id"]: i.get("unit", "g") for i in items_list}
    name_by_id = {i["item_id"]: i["item_name"] for i in items_list}
    for row in forward_rows + backtest_rows:
        eid = row.get("entity_id")
        if not eid:
            continue
        payload = row.get("payload") or {}
        if eid not in name_by_id:
            name_by_id[eid] = row.get("entity_name") or payload.get("item_name") or eid
        if eid not in unit_by_id:
            unit_by_id[eid] = _normalize_volume_unit(row.get("unit") or payload.get("unit"))

    if not items_list and name_by_id:
        items_list = [
            {"item_id": k, "item_name": name_by_id[k], "unit": unit_by_id.get(k, "g")}
            for k in sorted(name_by_id.keys())
        ]

    forecast_rows = []
    for row in forward_rows:
        point = _volume_row_to_points(row)
        # Central row unit is authoritative (OQ-4); local variant unit only
        # fills in when the central row shipped without one.
        if not _central_row_has_unit(row):
            point["unit"] = unit_by_id.get(point["item_id"], point["unit"])
        if item_id and point["item_id"] != item_id:
            continue
        if point["date"] < today_str:
            continue
        forecast_rows.append(point)

    forecast_rows.sort(key=lambda x: x["date"])
    distinct_dates = sorted({r["date"] for r in forecast_rows})
    limit_dates = set(distinct_dates[:days])
    forecast_rows = [r for r in forecast_rows if r["date"] in limit_dates]

    backtest_out = []
    for row in backtest_rows:
        point = _volume_row_to_points(row)
        if not _central_row_has_unit(row):
            point["unit"] = unit_by_id.get(point["item_id"], point["unit"])
        if item_id and point["item_id"] != item_id:
            continue
        backtest_out.append(point)
    backtest_out.sort(key=lambda x: (x["date"], x["item_id"]))

    return {
        "items": items_list,
        "history": history_rows,
        "forecast": forecast_rows,
        "backtest": backtest_out,
        "debug_info": {
            "served_from_central": True,
            "run_id": run_id,
            "using_fallback": using_fallback,
        },
    }


def build_awaiting_action_response(
    conn,
    *,
    family: str,
    default_message: str,
) -> Dict[str, Any]:
    cloud_configured = bool(get_forecast_bootstrap_endpoint(conn))
    message = default_message
    status = get_cached_central_forecast_status(conn)
    missing_prerequisite = None
    if status:
        metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else {}
        if metrics.get("assignment_coverage_pct") == 0:
            missing_prerequisite = "order_line_key"
            if family == FAMILY_ITEMS:
                message = "Central item forecasts are waiting for order-line assignment locators."
            elif family == FAMILY_VOLUME:
                message = "Central volume forecasts are waiting for order-line assignment locators."

    if not cloud_configured:
        message = (
            "Forecast cache is empty. Configure Cloud Server URL in Configuration, "
            "then run Sync DB to fetch central forecasts."
        )

    if family == "revenue":
        return {
            "summary": {
                "generated_at": get_current_business_date(),
                "projected_7d_revenue": 0,
                "projected_7d_orders": 0,
            },
            "historical": [],
            "forecasts": {
                "weekday_avg": [],
                "holt_winters": [],
                "prophet": [],
                "gp": [],
            },
            "debug_info": {
                "awaiting_action": True,
                "message": message,
                "cloud_not_configured": not cloud_configured,
                "missing_prerequisite": missing_prerequisite,
            },
        }

    base: Dict[str, Any] = {
        "items": [],
        "history": [],
        "forecast": [],
        "backtest": [],
        "awaiting_action": True,
        "cloud_not_configured": not cloud_configured,
        "message": message,
    }
    if missing_prerequisite:
        base["debug_info"] = {"missing_prerequisite": missing_prerequisite}
    return base


def has_central_revenue_forecast(conn, *, scope_key: str = DEFAULT_SCOPE_KEY) -> bool:
    run, _ = get_served_revenue_run(conn, scope_key=scope_key)
    return run is not None


def get_cached_central_forecast_status(conn) -> Optional[Dict[str, Any]]:
    ensure_central_forecast_tables(conn)
    row = conn.execute(
        "SELECT value FROM system_config WHERE key = 'central_forecast_status' LIMIT 1"
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        parsed = json.loads(row[0])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
