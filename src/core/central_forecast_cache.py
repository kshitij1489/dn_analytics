"""
SQLite cache for server-authored central forecast runs, rows, and weather.

Phase 5 pull-only desktop path. See docs/CENTRAL_FORECASTING_NIGHTLY_PLAN.md §5.1.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FORECAST_SYNC_CURSOR_KEY = "forecast_sync_cursor"
DEFAULT_SCOPE_KEY = "default"
REVENUE_MODEL_NAMES = ("weekday_avg", "holt_winters", "prophet", "gp")
REVENUE_FORWARD_DAYS = 7
ITEM_FORWARD_DAYS = 14
VOLUME_FORWARD_DAYS = 14
FAMILY_ITEMS = "items"
FAMILY_VOLUME = "volume"


# Executed one statement at a time: executescript() implicitly COMMITs any open
# transaction, which would break page-apply atomicity in forecast_sync.
_CENTRAL_FORECAST_DDL = (
    """
    CREATE TABLE IF NOT EXISTS system_config (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS central_forecast_runs (
        run_id TEXT PRIMARY KEY,
        server_seq INTEGER NOT NULL,
        scope_key TEXT NOT NULL DEFAULT 'default',
        generated_on DATE NOT NULL,
        status TEXT NOT NULL,
        completed_at TEXT,
        families TEXT NOT NULL,
        training_window_start DATE,
        training_window_end DATE,
        metrics TEXT,
        pulled_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(server_seq)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_central_forecast_runs_generated
        ON central_forecast_runs(scope_key, generated_on DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS central_forecast_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_seq INTEGER NOT NULL UNIQUE,
        run_id TEXT NOT NULL,
        scope_key TEXT NOT NULL DEFAULT 'default',
        family TEXT NOT NULL,
        kind TEXT NOT NULL,
        forecast_date DATE NOT NULL,
        model_name TEXT,
        entity_id TEXT,
        entity_name TEXT,
        unit TEXT,
        payload TEXT NOT NULL,
        pulled_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (run_id) REFERENCES central_forecast_runs(run_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_central_forecast_rows_lookup
        ON central_forecast_rows(scope_key, family, kind, forecast_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_central_forecast_rows_run
        ON central_forecast_rows(run_id, family, kind)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_central_forecast_rows_entity
        ON central_forecast_rows(scope_key, family, entity_id, forecast_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS central_forecast_weather (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_seq INTEGER NOT NULL UNIQUE,
        weather_date DATE NOT NULL,
        payload TEXT NOT NULL,
        pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_central_forecast_weather_date
        ON central_forecast_weather(weather_date)
    """,
)


def ensure_central_forecast_tables(conn) -> None:
    """Idempotent DDL for central forecast cache tables."""
    for statement in _CENTRAL_FORECAST_DDL:
        conn.execute(statement)


def get_forecast_sync_cursor(conn) -> Optional[str]:
    ensure_central_forecast_tables(conn)
    row = conn.execute(
        "SELECT value FROM system_config WHERE key = ? LIMIT 1",
        (FORECAST_SYNC_CURSOR_KEY,),
    ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def set_forecast_sync_cursor(conn, cursor: Optional[str]) -> None:
    ensure_central_forecast_tables(conn)
    if cursor is None:
        conn.execute("DELETE FROM system_config WHERE key = ?", (FORECAST_SYNC_CURSOR_KEY,))
        return
    conn.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (FORECAST_SYNC_CURSOR_KEY, cursor),
    )


def _parse_json_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except json.JSONDecodeError:
            pass
    return []


def _run_row_to_dict(row) -> Dict[str, Any]:
    return {
        "run_id": row[0],
        "server_seq": row[1],
        "scope_key": row[2],
        "generated_on": row[3],
        "status": row[4],
        "completed_at": row[5],
        "families": _parse_json_list(row[6]),
        "training_window_start": row[7],
        "training_window_end": row[8],
        "metrics": json.loads(row[9]) if row[9] else {},
    }


def list_success_runs(
    conn,
    *,
    scope_key: str = DEFAULT_SCOPE_KEY,
    family: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Success runs newest first; optionally filter to runs that include family."""
    ensure_central_forecast_tables(conn)
    rows = conn.execute(
        """
        SELECT run_id, server_seq, scope_key, generated_on, status, completed_at,
               families, training_window_start, training_window_end, metrics
        FROM central_forecast_runs
        WHERE scope_key = ? AND status = 'success'
        ORDER BY generated_on DESC, completed_at DESC
        """,
        (scope_key,),
    ).fetchall()
    runs = [_run_row_to_dict(r) for r in rows]
    if family is None:
        return runs
    return [r for r in runs if family in r.get("families", [])]


def count_revenue_forward_rows(conn, run_id: str) -> Dict[str, int]:
    """Count forward revenue rows per model for completeness checks."""
    ensure_central_forecast_tables(conn)
    counts: Dict[str, int] = {}
    cur = conn.execute(
        """
        SELECT model_name, COUNT(*) AS cnt
        FROM central_forecast_rows
        WHERE run_id = ? AND family = 'revenue' AND kind = 'forward'
        GROUP BY model_name
        """,
        (run_id,),
    )
    for model_name, cnt in cur.fetchall():
        if model_name:
            counts[str(model_name)] = int(cnt)
    return counts


def is_revenue_run_complete(conn, run_id: str) -> bool:
    counts = count_revenue_forward_rows(conn, run_id)
    return all(counts.get(m, 0) >= REVENUE_FORWARD_DAYS for m in REVENUE_MODEL_NAMES)


def _count_entity_forward_days(conn, run_id: str, family: str) -> Dict[str, int]:
    ensure_central_forecast_tables(conn)
    counts: Dict[str, int] = {}
    cur = conn.execute(
        """
        SELECT entity_id, COUNT(DISTINCT forecast_date) AS cnt
        FROM central_forecast_rows
        WHERE run_id = ? AND family = ? AND kind = 'forward' AND entity_id IS NOT NULL
        GROUP BY entity_id
        """,
        (run_id, family),
    )
    for entity_id, cnt in cur.fetchall():
        counts[str(entity_id)] = int(cnt)
    return counts


def is_entity_family_run_complete(
    conn, run_id: str, family: str, *, min_forward_days: int
) -> bool:
    counts = _count_entity_forward_days(conn, run_id, family)
    if not counts:
        return False
    return all(cnt >= min_forward_days for cnt in counts.values())


def get_served_family_run(
    conn,
    family: str,
    *,
    scope_key: str = DEFAULT_SCOPE_KEY,
    today_str: Optional[str] = None,
    min_forward_days: int = 1,
    completeness_check=None,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Generic run picker for revenue/items/volume."""
    ensure_central_forecast_tables(conn)
    from src.core.utils.business_date import get_current_business_date

    today_str = today_str or get_current_business_date()
    runs = list_success_runs(conn, scope_key=scope_key, family=family)
    if not runs:
        return None, False

    def _complete(run_id: str) -> bool:
        if completeness_check:
            return completeness_check(conn, run_id)
        counts = _count_entity_forward_days(conn, run_id, family)
        return bool(counts) and all(c >= min_forward_days for c in counts.values())

    for run in runs:
        if run.get("generated_on") == today_str and _complete(run["run_id"]):
            return run, False

    for run in runs:
        if _complete(run["run_id"]):
            return run, run.get("generated_on") != today_str

    return None, False


def load_central_forecast_rows(
    conn,
    run_id: str,
    *,
    family: str,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    ensure_central_forecast_tables(conn)
    params: List[Any] = [run_id, family]
    kind_sql = ""
    if kind:
        kind_sql = " AND kind = ?"
        params.append(kind)
    cur = conn.execute(
        f"""
        SELECT forecast_date, model_name, entity_id, entity_name, unit, payload, kind
        FROM central_forecast_rows
        WHERE run_id = ? AND family = ?{kind_sql}
        ORDER BY forecast_date, model_name, entity_id
        """,
        params,
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        try:
            payload = json.loads(row[5]) if row[5] else {}
        except json.JSONDecodeError:
            payload = {}
        out.append(
            {
                "forecast_date": row[0],
                "model_name": row[1],
                "entity_id": row[2],
                "entity_name": row[3],
                "unit": row[4],
                "payload": payload,
                "kind": row[6],
            }
        )
    return out


def load_central_forecast_weather_map(conn) -> Dict[str, Dict[str, Any]]:
    """weather_date (YYYY-MM-DD) -> payload, for forward-looking chart overlays."""
    ensure_central_forecast_tables(conn)
    cur = conn.execute("SELECT weather_date, payload FROM central_forecast_weather")
    out: Dict[str, Dict[str, Any]] = {}
    for weather_date, payload_raw in cur.fetchall():
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            payload = {}
        out[str(weather_date)[:10]] = payload
    return out


def clear_central_forecast_cache(conn, *, family: Optional[str] = None) -> None:
    """Clear central forecast cache (config reset)."""
    ensure_central_forecast_tables(conn)
    if family is None:
        conn.execute("DELETE FROM central_forecast_rows")
        conn.execute("DELETE FROM central_forecast_runs")
        conn.execute("DELETE FROM central_forecast_weather")
        set_forecast_sync_cursor(conn, None)
        conn.execute("DELETE FROM system_config WHERE key = 'central_forecast_status'")
        return
    # Family filter only: multi-family runs are immutable, so deleting by
    # run_id would wipe other families' rows from the same run.
    conn.execute("DELETE FROM central_forecast_rows WHERE family = ?", (family,))
    # Cursor points past the deleted server_seqs and the delta endpoint never
    # replays them; reset so the next pull bootstraps instead of waiting for
    # the next nightly publish.
    set_forecast_sync_cursor(conn, None)


def _delete_runs_and_rows(conn, run_ids: List[str]) -> None:
    placeholders = ",".join("?" * len(run_ids))
    # Explicit row delete: FK cascade only fires when PRAGMA foreign_keys is ON,
    # which not every connection guarantees.
    conn.execute(
        f"DELETE FROM central_forecast_rows WHERE run_id IN ({placeholders})",
        run_ids,
    )
    conn.execute(
        f"DELETE FROM central_forecast_runs WHERE run_id IN ({placeholders})",
        run_ids,
    )


def prune_old_central_forecast_data(
    conn, *, keep_runs: int = 14, stale_days: int = 30
) -> Dict[str, int]:
    """
    Drop old runs beyond retention.

    Retention is per family (plan §5.1): a success run is deleted only when
    every family it carries already has keep_runs newer success runs. This
    keeps e.g. the last items/volume run alive through a long stretch of
    revenue-only nightlies. Non-success runs older than stale_days are also
    dropped.
    """
    ensure_central_forecast_tables(conn)
    rows = conn.execute(
        """
        SELECT run_id, families FROM central_forecast_runs
        WHERE status = 'success'
        ORDER BY generated_on DESC, completed_at DESC
        """
    ).fetchall()
    newer_per_family: Dict[str, int] = {}
    drop_ids: List[str] = []
    for run_id, families_raw in rows:
        families = _parse_json_list(families_raw) or ["__unknown__"]
        keep = any(newer_per_family.get(f, 0) < keep_runs for f in families)
        for f in families:
            newer_per_family[f] = newer_per_family.get(f, 0) + 1
        if not keep:
            drop_ids.append(run_id)

    stale_ids = [
        r[0]
        for r in conn.execute(
            """
            SELECT run_id FROM central_forecast_runs
            WHERE status != 'success' AND generated_on < date('now', ?)
            """,
            (f"-{stale_days} days",),
        ).fetchall()
    ]
    drop_ids.extend(stale_ids)

    if not drop_ids:
        return {"runs_deleted": 0, "stale_runs_deleted": 0, "weather_deleted": 0}
    _delete_runs_and_rows(conn, drop_ids)
    # Weather is not FK-linked; prune by age relative to kept runs.
    min_kept = conn.execute(
        """
        SELECT MIN(generated_on) FROM central_forecast_runs WHERE status = 'success'
        """
    ).fetchone()
    weather_deleted = 0
    if min_kept and min_kept[0]:
        cur = conn.execute(
            "DELETE FROM central_forecast_weather WHERE weather_date < ?",
            (min_kept[0],),
        )
        weather_deleted = cur.rowcount if cur.rowcount is not None else 0
    return {
        "runs_deleted": len(drop_ids) - len(stale_ids),
        "stale_runs_deleted": len(stale_ids),
        "weather_deleted": weather_deleted,
    }
