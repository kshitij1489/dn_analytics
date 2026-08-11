"""All Stores forecast combination (plan §7.5).

Each profile keeps its own server-authored forecast cache; nothing here creates
a shared run or cursor. Published values are combined with explicit rules:

* revenue/orders/quantity/volume/recommended preparation are summed;
* published lower/upper bounds are summed only as a labelled operational
  envelope — this is not a recomputed portfolio interval;
* probability is reported as the weakest store-level probability, never
  averaged or multiplied into a fake portfolio probability;
* item and volume forecasts group by durable item name (and unit), not by a
  transient per-store entity ID;
* a store with no usable forecast makes the combined result incomplete and is
  named; it is never treated as zero.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from src.core.profiles import RestaurantProfile
from src.core.queries.multi_store_reducers import Min, Sum, group_rows

REVENUE_MODEL_NAMES = ("weekday_avg", "holt_winters", "prophet", "gp")

# Weather is profile metadata: one store's temperature is not the virtual
# store's weather, so forward weather overlays are dropped when combining.
DROPPED_WEATHER_FIELDS = ("temp_max", "rain_category")

PROBABILITY_BASIS = "min_store"

Pairs = Sequence[Tuple[RestaurantProfile, Dict[str, Any]]]


def lift_awaiting_profiles(result: Dict[str, Any], is_all: bool) -> Dict[str, Any]:
    """Report forecast-less stores as incomplete instead of silently zero."""
    if not is_all:
        return result
    for store in result.get("data", {}).pop("awaiting_profiles", []):
        result["incomplete_profiles"].append(store)
    return result


def _is_awaiting(value: Dict[str, Any]) -> bool:
    if not isinstance(value, dict):
        return True
    if value.get("awaiting_action"):
        return True
    debug_info = value.get("debug_info") or {}
    return bool(debug_info.get("awaiting_action"))


def split_ready(pairs: Pairs) -> Tuple[Pairs, List[Dict[str, str]]]:
    ready = [(profile, value) for profile, value in pairs if not _is_awaiting(value)]
    awaiting = [
        {
            "restaurant_id": profile.restaurant_id,
            "restaurant_name": profile.display_name,
            "code": "forecast_awaiting_action",
            "error": (
                (value or {}).get("message")
                or ((value or {}).get("debug_info") or {}).get("message")
                or "No usable central forecast for this store"
            ),
        }
        for profile, value in pairs
        if _is_awaiting(value)
    ]
    return ready, awaiting


def _drop_weather(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for row in rows:
        for field in DROPPED_WEATHER_FIELDS:
            row.pop(field, None)
    return rows


def combine_revenue_forecast(pairs: Pairs) -> Dict[str, Any]:
    ready, awaiting = split_ready(pairs)

    forecasts: Dict[str, List[Dict[str, Any]]] = {}
    for model in REVENUE_MODEL_NAMES:
        forecasts[model] = _drop_weather(
            group_rows(
                ready,
                group_by=("date",),
                spec={
                    "revenue": Sum(),
                    "orders": Sum(),
                    # Labelled operational envelope, not a recomputed interval.
                    "gp_lower": Sum(),
                    "gp_upper": Sum(),
                },
                sort_by="date",
                descending=False,
                rows_of=lambda value, model=model: (value.get("forecasts") or {}).get(model) or [],
            )
        )

    historical = _drop_weather(
        group_rows(
            ready,
            group_by=("sale_date",),
            spec={"revenue": Sum(), "orders": Sum()},
            sort_by="sale_date",
            descending=False,
            rows_of=lambda value: value.get("historical") or [],
        )
    )

    return {
        "summary": {
            "generated_at": max(
                (
                    str((value.get("summary") or {}).get("generated_at") or "")
                    for _profile, value in ready
                ),
                default=None,
            ),
            "projected_7d_revenue": sum(
                float((value.get("summary") or {}).get("projected_7d_revenue") or 0)
                for _profile, value in ready
            ),
            "projected_7d_orders": sum(
                int((value.get("summary") or {}).get("projected_7d_orders") or 0)
                for _profile, value in ready
            ),
        },
        "historical": historical,
        "forecasts": forecasts,
        "debug_info": {
            "served_from_central": bool(ready),
            "scope": "all",
            "interval_basis": "summed_store_bounds",
            "forecast_incomplete": bool(awaiting) or not ready,
            "incomplete_stores": awaiting,
            "run_ids": {
                profile.restaurant_id: ((value.get("debug_info") or {}).get("run_id"))
                for profile, value in ready
            },
            "using_fallback": any(
                bool((value.get("debug_info") or {}).get("using_fallback"))
                for _profile, value in ready
            ),
        },
        "awaiting_profiles": awaiting,
    }


def _durable_entity_rows(
    value: Dict[str, Any], key: str, *, unit_field: bool
) -> List[Dict[str, Any]]:
    """Replace transient per-store item IDs with the durable item name."""
    names = {
        str(item.get("item_id")): item.get("item_name")
        for item in value.get("items") or []
    }
    rows: List[Dict[str, Any]] = []
    for row in value.get(key) or []:
        enriched = dict(row)
        item_id = str(enriched.get("item_id"))
        enriched["item_name"] = enriched.get("item_name") or names.get(item_id) or item_id
        enriched["source_item_id"] = enriched.get("item_id")
        if unit_field:
            enriched["unit"] = enriched.get("unit")
        rows.append(enriched)
    return rows


def _history_rows_with_names(value: Dict[str, Any]) -> List[Dict[str, Any]]:
    names = {
        str(item.get("item_id")): item.get("item_name")
        for item in value.get("items") or []
    }
    rows = []
    for row in value.get("history") or []:
        enriched = dict(row)
        enriched["item_name"] = names.get(str(enriched.get("item_id"))) or str(
            enriched.get("item_id")
        )
        rows.append(enriched)
    return rows


def _combined_items(ready: Pairs) -> List[Dict[str, Any]]:
    """One entry per durable item name, keyed by that name in All Stores mode."""
    combined: Dict[str, Dict[str, Any]] = {}
    for profile, value in ready:
        for item in value.get("items") or []:
            name = str(item.get("item_name") or item.get("item_id"))
            entry = combined.setdefault(
                name, {"item_id": name, "item_name": name, "contributors": []}
            )
            entry["contributors"].append(
                {
                    "restaurant_id": profile.restaurant_id,
                    "restaurant_name": profile.display_name,
                    "item_id": item.get("item_id"),
                }
            )
    return [combined[name] for name in sorted(combined)]


def _entity_debug_info(ready: Pairs, awaiting: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "served_from_central": bool(ready),
        "scope": "all",
        "interval_basis": "summed_store_bounds",
        "probability_basis": PROBABILITY_BASIS,
        "forecast_incomplete": bool(awaiting) or not ready,
        "incomplete_stores": awaiting,
        "run_ids": {
            profile.restaurant_id: ((value.get("debug_info") or {}).get("run_id"))
            for profile, value in ready
        },
    }


def combine_item_forecast(pairs: Pairs, *, item_name: str | None = None) -> Dict[str, Any]:
    ready, awaiting = split_ready(pairs)

    def entity_rows(key: str):
        def rows_of(value):
            rows = _durable_entity_rows(value, key, unit_field=False)
            if item_name:
                rows = [row for row in rows if str(row.get("item_name")) == item_name]
            return rows

        return group_rows(
            ready,
            group_by=("item_name", "date"),
            spec={"p50": Sum(), "p90": Sum(), "recommended_prep": Sum(), "probability": Min()},
            sort_by="date",
            descending=False,
            contributor_fields=("source_item_id", "p50"),
            rows_of=rows_of,
        )

    def history_rows():
        def rows_of(value):
            rows = _history_rows_with_names(value)
            if item_name:
                rows = [row for row in rows if str(row.get("item_name")) == item_name]
            return rows

        return group_rows(
            ready,
            group_by=("item_name", "date"),
            spec={"qty": Sum()},
            sort_by="date",
            descending=False,
            rows_of=rows_of,
        )

    items = _combined_items(ready)
    if item_name:
        items = [item for item in items if item["item_name"] == item_name]

    forecast_rows = entity_rows("forecast")
    for row in forecast_rows:
        row["item_id"] = row["item_name"]
    backtest_rows = entity_rows("backtest")
    for row in backtest_rows:
        row["item_id"] = row["item_name"]
    history = history_rows()
    for row in history:
        row["item_id"] = row["item_name"]

    return {
        "items": items,
        "history": history,
        "forecast": forecast_rows,
        "backtest": backtest_rows,
        "debug_info": _entity_debug_info(ready, awaiting),
        "awaiting_profiles": awaiting,
    }


def combine_volume_forecast(pairs: Pairs, *, item_name: str | None = None) -> Dict[str, Any]:
    ready, awaiting = split_ready(pairs)

    def entity_rows(key: str):
        def rows_of(value):
            rows = _durable_entity_rows(value, key, unit_field=True)
            if item_name:
                rows = [row for row in rows if str(row.get("item_name")) == item_name]
            return rows

        return group_rows(
            ready,
            # Volume only adds up within one normalized unit.
            group_by=("item_name", "unit", "date"),
            spec={
                "p50": Sum(),
                "p90": Sum(),
                "volume_value": Sum(),
                "recommended_volume": Sum(),
                "probability": Min(),
            },
            sort_by="date",
            descending=False,
            contributor_fields=("source_item_id", "volume_value"),
            rows_of=rows_of,
        )

    def history_rows():
        def rows_of(value):
            rows = _history_rows_with_names(value)
            if item_name:
                rows = [row for row in rows if str(row.get("item_name")) == item_name]
            return rows

        return group_rows(
            ready,
            group_by=("item_name", "date"),
            spec={"qty": Sum(), "volume": Sum()},
            sort_by="date",
            descending=False,
            rows_of=rows_of,
        )

    items = _combined_items(ready)
    if item_name:
        items = [item for item in items if item["item_name"] == item_name]

    forecast_rows = entity_rows("forecast")
    backtest_rows = entity_rows("backtest")
    for row in forecast_rows + backtest_rows:
        row["item_id"] = row["item_name"]
    history = history_rows()
    for row in history:
        row["item_id"] = row["item_name"]

    return {
        "items": items,
        "history": history,
        "forecast": forecast_rows,
        "backtest": backtest_rows,
        "debug_info": _entity_debug_info(ready, awaiting),
        "awaiting_profiles": awaiting,
    }
