"""
Pull server-authored forecast deltas/bootstrap into local central_forecast_* tables.

Pattern mirrors menu_merge_sync cursor + transactional apply.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from src.core.central_forecast_cache import (
    DEFAULT_SCOPE_KEY,
    ensure_central_forecast_tables,
    get_forecast_sync_cursor,
    prune_old_central_forecast_data,
    set_forecast_sync_cursor,
)
from src.core.config.cloud_sync_config import get_cloud_sync_config

logger = logging.getLogger(__name__)

DEFAULT_PULL_LIMIT = 1000
DEFAULT_FAMILIES = "revenue,items,volume,weather"


class MissingForecastParentRunsError(RuntimeError):
    """A forecast page contains rows whose parent runs are unavailable."""

    def __init__(self, run_ids: List[str]):
        self.run_ids = sorted(run_ids)
        preview = ", ".join(self.run_ids[:3])
        if len(self.run_ids) > 3:
            preview = f"{preview}, ..."
        super().__init__(
            f"Forecast page is missing {len(self.run_ids)} parent run(s): {preview}"
        )


def get_forecast_delta_endpoint(conn) -> Optional[str]:
    from src.core.config.client_learning_config import CLIENT_LEARNING_FORECAST_DELTA_URL

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url.rstrip('/')}/desktop-analytics-sync/forecasts/delta"
    url = (CLIENT_LEARNING_FORECAST_DELTA_URL or "").strip()
    return url or None


def get_forecast_bootstrap_endpoint(conn) -> Optional[str]:
    from src.core.config.client_learning_config import CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url.rstrip('/')}/desktop-analytics-sync/forecasts/central-bootstrap"
    url = (CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL or "").strip()
    return url or None


def get_forecast_status_endpoint(conn) -> Optional[str]:
    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url.rstrip('/')}/desktop-analytics-sync/forecasts/status"
    return None


def _coerce_server_seq(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _upsert_run(conn, run: Dict[str, Any]) -> None:
    families = run.get("families")
    if isinstance(families, list):
        families_json = json.dumps(families)
    else:
        families_json = json.dumps([])

    metrics = run.get("metrics")
    metrics_json = json.dumps(metrics) if isinstance(metrics, dict) else None

    conn.execute(
        """
        INSERT INTO central_forecast_runs (
            run_id, server_seq, scope_key, generated_on, status, completed_at,
            families, training_window_start, training_window_end, metrics
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            server_seq = excluded.server_seq,
            status = excluded.status,
            completed_at = excluded.completed_at,
            families = excluded.families,
            metrics = excluded.metrics,
            pulled_at = CURRENT_TIMESTAMP
        """,
        (
            str(run.get("run_id") or ""),
            _coerce_server_seq(run.get("server_seq")),
            str(run.get("scope_key") or DEFAULT_SCOPE_KEY),
            run.get("generated_on"),
            str(run.get("status") or "success"),
            run.get("completed_at"),
            families_json,
            run.get("training_window_start"),
            run.get("training_window_end"),
            metrics_json,
        ),
    )


def _insert_row(conn, row: Dict[str, Any], scope_key: str) -> bool:
    server_seq = _coerce_server_seq(row.get("server_seq"))
    if server_seq <= 0:
        return False
    payload = row.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    entity_id = row.get("entity_id")
    entity_name = row.get("entity_name") or payload.get("item_name")
    unit = row.get("unit") or payload.get("unit")
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO central_forecast_rows (
            server_seq, run_id, scope_key, family, kind, forecast_date,
            model_name, entity_id, entity_name, unit, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            server_seq,
            str(row.get("run_id") or ""),
            scope_key,
            str(row.get("family") or ""),
            str(row.get("kind") or ""),
            row.get("forecast_date"),
            row.get("model_name"),
            str(entity_id) if entity_id is not None else None,
            entity_name,
            unit,
            json.dumps(payload),
        ),
    )
    return cur.rowcount > 0


def _insert_weather_row(conn, row: Dict[str, Any]) -> bool:
    server_seq = _coerce_server_seq(row.get("server_seq"))
    if server_seq <= 0:
        return False
    payload = row.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    weather_date = row.get("weather_date") or payload.get("weather_date")
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO central_forecast_weather (server_seq, weather_date, payload)
        VALUES (?, ?, ?)
        """,
        (server_seq, weather_date, json.dumps(payload)),
    )
    return cur.rowcount > 0


def _validate_row_parent_runs(conn, page: Dict[str, Any]) -> None:
    """Reject orphan rows before SQLite reports an opaque FK failure."""
    required_run_ids = {
        str(row.get("run_id") or "")
        for row in page.get("rows") or []
        if isinstance(row, dict)
    }
    if not required_run_ids:
        return

    page_run_ids = {
        str(run.get("run_id"))
        for run in page.get("runs") or []
        if isinstance(run, dict) and run.get("run_id")
    }
    unresolved = required_run_ids - page_run_ids
    if unresolved:
        placeholders = ",".join("?" for _ in unresolved)
        local_run_ids = {
            str(row[0])
            for row in conn.execute(
                f"SELECT run_id FROM central_forecast_runs WHERE run_id IN ({placeholders})",
                tuple(sorted(unresolved)),
            ).fetchall()
        }
        unresolved -= local_run_ids
    if unresolved:
        raise MissingForecastParentRunsError(list(unresolved))


def apply_forecast_page(conn, page: Dict[str, Any]) -> Dict[str, int]:
    """Apply one delta/bootstrap page inside the current transaction.

    Caller must have run ensure_central_forecast_tables() already (pull start
    does); no DDL here so the surrounding transaction stays intact.
    """
    scope_key = str(page.get("scope_key") or DEFAULT_SCOPE_KEY)
    stats = {"runs_upserted": 0, "rows_inserted": 0, "weather_inserted": 0}
    _validate_row_parent_runs(conn, page)

    for run in page.get("runs") or []:
        if not isinstance(run, dict):
            continue
        if not run.get("run_id"):
            continue
        _upsert_run(conn, run)
        stats["runs_upserted"] += 1

    for row in page.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if _insert_row(conn, row, scope_key):
            stats["rows_inserted"] += 1

    for wrow in page.get("weather_rows") or []:
        if not isinstance(wrow, dict):
            continue
        if _insert_weather_row(conn, wrow):
            stats["weather_inserted"] += 1

    prune_old_central_forecast_data(conn)
    return stats


def _fetch_page(
    endpoint: str,
    *,
    conn,
    auth: Optional[str],
    params: Dict[str, str],
) -> Dict[str, Any]:
    import requests

    from src.core.central_api import error_from_response, scoped_headers, validate_no_retired_query_params

    validate_no_retired_query_params(params)
    headers = scoped_headers(conn, auth_kind="sync", credential=auth)
    response = requests.get(endpoint, headers=headers, params=params, timeout=120)
    if response.status_code >= 400:
        raise error_from_response(response, conn=conn)
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Forecast pull returned non-object JSON")
    return data


def pull_and_apply_forecast_deltas(
    conn,
    *,
    families: str = DEFAULT_FAMILIES,
    limit: int = DEFAULT_PULL_LIMIT,
    force_bootstrap: bool = False,
) -> Dict[str, Any]:
    """
    Pull forecast bootstrap (no cursor) or delta pages and apply to SQLite.

    Cursor advances only after each page commits successfully.
    """
    ensure_central_forecast_tables(conn)
    cursor = get_forecast_sync_cursor(conn)
    use_bootstrap = force_bootstrap or not cursor

    endpoint = (
        get_forecast_bootstrap_endpoint(conn)
        if use_bootstrap
        else get_forecast_delta_endpoint(conn)
    )
    if not endpoint:
        return {
            "attempted": False,
            "skipped": True,
            "reason": "no forecast pull endpoint configured",
        }

    _, auth_key = get_cloud_sync_config(conn)
    summary: Dict[str, Any] = {
        "attempted": True,
        "mode": "bootstrap" if use_bootstrap else "delta",
        "pages": 0,
        "runs_upserted": 0,
        "rows_inserted": 0,
        "weather_inserted": 0,
        "next_cursor": cursor,
    }

    params: Dict[str, str] = {"families": families, "limit": str(limit)}
    if not use_bootstrap and cursor:
        params["cursor"] = cursor

    try:
        while True:
            page = _fetch_page(endpoint, conn=conn, auth=auth_key, params=params)
            summary["pages"] += 1

            conn.execute("BEGIN")
            try:
                page_stats = apply_forecast_page(conn, page)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            for key in ("runs_upserted", "rows_inserted", "weather_inserted"):
                summary[key] += page_stats.get(key, 0)

            next_cursor = page.get("next_cursor")
            if next_cursor is not None:
                set_forecast_sync_cursor(conn, str(next_cursor))
                summary["next_cursor"] = str(next_cursor)
                conn.commit()

            if not page.get("has_more"):
                break

            if use_bootstrap:
                # After first bootstrap page, switch to delta for remaining pages.
                delta_endpoint = get_forecast_delta_endpoint(conn)
                if not delta_endpoint or not next_cursor:
                    break
                endpoint = delta_endpoint
                use_bootstrap = False
                params = {
                    "families": families,
                    "limit": str(limit),
                    "cursor": str(next_cursor),
                }
            else:
                if not next_cursor:
                    break
                params["cursor"] = str(next_cursor)

        # Cache latest status for item/volume awaiting_action messages.
        status_ep = get_forecast_status_endpoint(conn)
        if status_ep:
            try:
                status_page = _fetch_page(status_ep, conn=conn, auth=auth_key, params={})
                latest = status_page.get("latest_run")
                if isinstance(latest, dict):
                    conn.execute(
                        """
                        INSERT INTO system_config (key, value, updated_at)
                        VALUES ('central_forecast_status', ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (json.dumps(latest),),
                    )
                    conn.commit()
            except Exception as exc:
                logger.warning("Forecast status cache failed: %s", exc)

        summary["status"] = "ok"
        return summary
    except MissingForecastParentRunsError as exc:
        if not force_bootstrap:
            logger.warning("%s; retrying once from forecast bootstrap", exc)
            recovery = pull_and_apply_forecast_deltas(
                conn,
                families=families,
                limit=limit,
                force_bootstrap=True,
            )
            recovery["recovery"] = {
                "attempted": True,
                "reason": "missing_parent_runs",
                "missing_run_ids": exc.run_ids,
                "succeeded": recovery.get("status") == "ok",
            }
            return recovery
        logger.exception("Forecast bootstrap contains orphan rows")
        summary["status"] = "error"
        summary["error"] = str(exc)
        return summary
    except Exception as exc:
        logger.exception("Forecast pull failed")
        summary["status"] = "error"
        summary["error"] = str(exc)
        return summary
