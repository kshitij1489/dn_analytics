"""
Forecast Cache — Persistent storage for revenue and item demand predictions.

Provides a read-through cache backed by SQLite tables:
  - forecast_cache: revenue model predictions (weekday_avg, holt_winters, prophet, gp)
  - item_forecast_cache: item-level demand predictions

Cache is keyed by generated_on (today's business date).  Stale entries from
previous days are kept for historical reference and cloud sync.

Cloud sync tracking: uploaded_at is NULL until pushed to cloud; the
forecast_shipper queries for unsent rows and marks them after upload.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Table creation (idempotent)
# ---------------------------------------------------------------------------

_REVENUE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS forecast_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date DATE NOT NULL,
    model_name TEXT NOT NULL,
    generated_on DATE NOT NULL,
    revenue FLOAT,
    orders INTEGER DEFAULT 0,
    pred_std FLOAT,
    lower_95 FLOAT,
    upper_95 FLOAT,
    temp_max FLOAT,
    rain_category TEXT,
    uploaded_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(forecast_date, model_name, generated_on)
)
"""

_ITEM_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS item_forecast_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date DATE NOT NULL,
    item_id TEXT NOT NULL,
    generated_on DATE NOT NULL,
    p50 FLOAT,
    p90 FLOAT,
    probability FLOAT,
    recommended_prep INTEGER,
    uploaded_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(forecast_date, item_id, generated_on)
)
"""

_REVENUE_BACKTEST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS revenue_backtest_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date DATE NOT NULL,
    model_name TEXT NOT NULL,
    model_trained_through DATE NOT NULL,
    revenue FLOAT,
    orders INTEGER DEFAULT 0,
    pred_std FLOAT,
    lower_95 FLOAT,
    upper_95 FLOAT,
    uploaded_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(forecast_date, model_name, model_trained_through)
)
"""

_BACKTEST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS item_backtest_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date DATE NOT NULL,
    item_id TEXT NOT NULL,
    model_trained_through DATE NOT NULL,
    p50 FLOAT,
    p90 FLOAT,
    probability FLOAT,
    uploaded_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(forecast_date, item_id, model_trained_through)
)
"""

_VOLUME_FORECAST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS volume_forecast_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date DATE NOT NULL,
    item_id TEXT NOT NULL,
    generated_on DATE NOT NULL,
    volume_value FLOAT NOT NULL,
    unit TEXT NOT NULL,
    p50 FLOAT,
    p90 FLOAT,
    probability FLOAT,
    recommended_volume FLOAT,
    uploaded_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(forecast_date, item_id, generated_on)
)
"""

_VOLUME_BACKTEST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS volume_backtest_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date DATE NOT NULL,
    item_id TEXT NOT NULL,
    model_trained_through DATE NOT NULL,
    volume_value FLOAT,
    p50 FLOAT,
    p90 FLOAT,
    probability FLOAT,
    uploaded_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(forecast_date, item_id, model_trained_through)
)
"""


def _migrate_volume_tables_to_item_id(conn) -> None:
    """Migrate volume tables from variant_id to item_id. Drops and recreates if old schema detected."""
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='volume_forecast_cache'"
        )
        if not cur.fetchone():
            return
        cur = conn.execute("PRAGMA table_info(volume_forecast_cache)")
        cols = [row[1] for row in cur.fetchall()]
        if cols and "variant_id" in cols and "item_id" not in cols:
            logger.info("Migrating volume tables from variant_id to item_id (menu-item-level)...")
            conn.execute("DROP TABLE IF EXISTS volume_forecast_cache")
            conn.execute("DROP TABLE IF EXISTS volume_backtest_cache")
    except Exception as e:
        logger.debug(f"Volume table migration check: {e}")


def ensure_tables_exist(conn) -> None:
    """Create cache tables if they do not exist yet."""
    try:
        _migrate_volume_tables_to_item_id(conn)
        conn.execute(_REVENUE_TABLE_SQL)
        conn.execute(_ITEM_TABLE_SQL)
        conn.execute(_BACKTEST_TABLE_SQL)
        conn.execute(_REVENUE_BACKTEST_TABLE_SQL)
        conn.execute(_VOLUME_FORECAST_TABLE_SQL)
        conn.execute(_VOLUME_BACKTEST_TABLE_SQL)
        conn.commit()
    except Exception as e:
        logger.warning(f"Could not ensure forecast cache tables: {e}")


# ---------------------------------------------------------------------------
# Revenue forecast cache
# ---------------------------------------------------------------------------

def is_revenue_cache_fresh(conn, generated_on: str) -> bool:
    """Return True if forecast_cache has rows for this generated_on date."""
    try:
        ensure_tables_exist(conn)
        cur = conn.execute(
            "SELECT COUNT(*) FROM forecast_cache WHERE generated_on = ?",
            (generated_on,),
        )
        count = cur.fetchone()[0]
        return count > 0
    except Exception:
        return False



def get_latest_revenue_cache_generated_on(conn) -> Optional[str]:
    """Return the most recent generated_on in forecast_cache, or None."""
    try:
        ensure_tables_exist(conn)
        cur = conn.execute(
            "SELECT MAX(generated_on) FROM forecast_cache"
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def get_previous_revenue_cache_generated_on(conn, current_date: str) -> Optional[str]:
    """Return the recent generated_on strictly before current_date, or None."""
    try:
        ensure_tables_exist(conn)
        cur = conn.execute(
            "SELECT MAX(generated_on) FROM forecast_cache WHERE generated_on < ?",
            (current_date,)
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def load_revenue_forecasts(
    conn, generated_on: str
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load cached revenue forecasts for a specific generation date.

    Returns:
        Dict keyed by model_name → list of forecast dicts.
        Example: {'gp': [{'date': '...', 'revenue': 123, ...}], ...}
    """
    ensure_tables_exist(conn)
    results: Dict[str, List[Dict[str, Any]]] = {}
    try:
        cur = conn.execute(
            """SELECT forecast_date, model_name, revenue, orders,
                      pred_std, lower_95, upper_95, temp_max, rain_category
               FROM forecast_cache
               WHERE generated_on = ?
               ORDER BY model_name, forecast_date""",
            (generated_on,),
        )
        for row in cur.fetchall():
            model = row[1]
            entry: Dict[str, Any] = {
                "date": row[0],
                "revenue": row[2],
                "orders": row[3] or 0,
            }
            # GP-specific fields
            if model == "gp":
                entry["gp_lower"] = row[5]
                entry["gp_upper"] = row[6]
                if row[4] is not None:
                    entry["pred_std"] = row[4]
            # Prophet weather fields
            if row[7] is not None:
                entry["temp_max"] = row[7]
            if row[8] is not None:
                entry["rain_category"] = row[8]

            results.setdefault(model, []).append(entry)
    except Exception as e:
        logger.warning(f"Failed to load revenue forecasts from cache: {e}")
    return results


def save_revenue_forecasts(
    conn,
    model_name: str,
    forecasts: List[Dict[str, Any]],
    generated_on: str,
) -> None:
    """
    Persist a model's forecast results to the cache.

    Uses INSERT OR REPLACE so re-runs on the same day update values.
    """
    ensure_tables_exist(conn)
    if not forecasts:
        return

    rows = []
    for f in forecasts:
        rows.append((
            f.get("date"),
            model_name,
            generated_on,
            f.get("revenue"),
            f.get("orders", 0),
            f.get("pred_std"),
            f.get("gp_lower") or f.get("lower_95"),
            f.get("gp_upper") or f.get("upper_95"),
            f.get("temp_max"),
            f.get("rain_category"),
        ))

    try:
        conn.executemany(
            """INSERT OR REPLACE INTO forecast_cache
               (forecast_date, model_name, generated_on,
                revenue, orders, pred_std, lower_95, upper_95,
                temp_max, rain_category)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info(f"Cached {len(rows)} {model_name} forecast rows for {generated_on}")
    except Exception as e:
        logger.warning(f"Failed to save {model_name} forecasts to cache: {e}")


# ---------------------------------------------------------------------------
# Item demand forecast cache
# ---------------------------------------------------------------------------

def is_item_cache_fresh(conn, generated_on: str) -> bool:
    """Return True if item_forecast_cache has rows for this generated_on date."""
    try:
        ensure_tables_exist(conn)
        cur = conn.execute(
            "SELECT COUNT(*) FROM item_forecast_cache WHERE generated_on = ?",
            (generated_on,),
        )
        count = cur.fetchone()[0]
        return count > 0
    except Exception:
        return False


def get_latest_item_cache_generated_on(conn) -> Optional[str]:
    """
    Return the most recent generated_on in item_forecast_cache, or None if empty.
    Used when Pull from Cloud inserts data with cloud's date (may differ from client today).
    """
    try:
        ensure_tables_exist(conn)
        cur = conn.execute(
            "SELECT MAX(generated_on) FROM item_forecast_cache"
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def load_item_forecasts(
    conn, generated_on: str
) -> List[Dict[str, Any]]:
    """Load cached item forecasts for a specific generation date."""
    ensure_tables_exist(conn)
    results: List[Dict[str, Any]] = []
    try:
        cur = conn.execute(
            """SELECT forecast_date, item_id, p50, p90, probability, recommended_prep
               FROM item_forecast_cache
               WHERE generated_on = ?
               ORDER BY forecast_date, item_id""",
            (generated_on,),
        )
        for row in cur.fetchall():
            results.append({
                "date": row[0],
                "item_id": row[1],
                "p50": row[2],
                "p90": row[3],
                "probability": row[4],
                "recommended_prep": row[5],
            })
    except Exception as e:
        logger.warning(f"Failed to load item forecasts from cache: {e}")
    return results


def save_item_forecasts(
    conn,
    forecasts: List[Dict[str, Any]],
    generated_on: str,
) -> None:
    """Persist item forecast results to the cache."""
    ensure_tables_exist(conn)
    if not forecasts:
        return

    rows = []
    for f in forecasts:
        rows.append((
            f.get("date"),
            f.get("item_id"),
            generated_on,
            f.get("p50"),
            f.get("p90"),
            f.get("probability"),
            f.get("recommended_prep"),
        ))

    try:
        conn.executemany(
            """INSERT OR REPLACE INTO item_forecast_cache
               (forecast_date, item_id, generated_on,
                p50, p90, probability, recommended_prep)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info(f"Cached {len(rows)} item forecast rows for {generated_on}")
    except Exception as e:
        logger.warning(f"Failed to save item forecasts to cache: {e}")


# ---------------------------------------------------------------------------
# Gap detection helpers
# ---------------------------------------------------------------------------

def get_missing_revenue_dates(
    conn, generated_on: str, model_name: str, expected_dates: List[str]
) -> List[str]:
    """Return expected dates that are NOT in the cache for this model/generation."""
    ensure_tables_exist(conn)
    try:
        placeholders = ",".join("?" * len(expected_dates))
        cur = conn.execute(
            f"""SELECT forecast_date FROM forecast_cache
                WHERE generated_on = ? AND model_name = ?
                AND forecast_date IN ({placeholders})""",
            [generated_on, model_name] + expected_dates,
        )
        cached_dates = {row[0] for row in cur.fetchall()}
        return [d for d in expected_dates if d not in cached_dates]
    except Exception:
        return expected_dates


def get_missing_item_dates(
    conn, generated_on: str, expected_dates: List[str]
) -> List[str]:
    """Return expected dates that have NO item forecasts in cache."""
    ensure_tables_exist(conn)
    try:
        placeholders = ",".join("?" * len(expected_dates))
        cur = conn.execute(
            f"""SELECT DISTINCT forecast_date FROM item_forecast_cache
                WHERE generated_on = ?
                AND forecast_date IN ({placeholders})""",
            [generated_on] + expected_dates,
        )
        cached_dates = {row[0] for row in cur.fetchall()}
        return [d for d in expected_dates if d not in cached_dates]
    except Exception:
        return expected_dates


# ---------------------------------------------------------------------------
# Item backtest cache (point-in-time T→T+1 forecasts)
# ---------------------------------------------------------------------------

def get_missing_backtest_dates(
    conn,
    forecast_dates: List[str],
    item_ids: List[str],
) -> List[str]:
    """
    Return forecast_dates that lack complete backtest cache for the given items.
    For T→T+1, model_trained_through = forecast_date - 1.
    """
    ensure_tables_exist(conn)
    if not forecast_dates or not item_ids:
        return list(forecast_dates)
    try:
        # Build (forecast_date, model_trained_through) for each forecast_date
        from datetime import datetime, timedelta
        missing = []
        for fd in forecast_dates:
            d = datetime.strptime(fd, "%Y-%m-%d").date()
            trained_through = (d - timedelta(days=1)).isoformat()
            placeholders = ",".join("?" * len(item_ids))
            cur = conn.execute(
                f"""SELECT COUNT(*) FROM item_backtest_cache
                    WHERE forecast_date = ? AND model_trained_through = ?
                    AND item_id IN ({placeholders})""",
                [fd, trained_through] + list(item_ids),
            )
            count = cur.fetchone()[0]
            if count < len(item_ids):
                missing.append(fd)
        return missing
    except Exception:
        return list(forecast_dates)


def load_backtest_forecasts(
    conn,
    forecast_dates: List[str],
    item_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Load cached point-in-time backtest forecasts."""
    ensure_tables_exist(conn)
    results: List[Dict[str, Any]] = []
    try:
        if not forecast_dates:
            return results
        placeholders = ",".join("?" * len(forecast_dates))
        params: List[Any] = list(forecast_dates)
        q = f"""SELECT forecast_date, item_id, p50, p90, probability
                FROM item_backtest_cache
                WHERE forecast_date IN ({placeholders})"""
        if item_ids:
            q += " AND item_id IN (" + ",".join("?" * len(item_ids)) + ")"
            params.extend(item_ids)
        q += " ORDER BY forecast_date, item_id"
        cur = conn.execute(q, params)
        for row in cur.fetchall():
            results.append({
                "date": row[0],
                "item_id": row[1],
                "p50": row[2],
                "p90": row[3],
                "probability": row[4],
            })
    except Exception as e:
        logger.warning(f"Failed to load backtest forecasts: {e}")
    return results


def save_backtest_forecasts(
    conn,
    forecasts: List[Dict[str, Any]],
    model_trained_through: str,
) -> None:
    """Persist point-in-time backtest forecasts to cache."""
    ensure_tables_exist(conn)
    if not forecasts:
        return
    rows = [
        (
            f.get("date"),
            f.get("item_id"),
            model_trained_through,
            f.get("p50"),
            f.get("p90"),
            f.get("probability"),
        )
        for f in forecasts
    ]
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO item_backtest_cache
               (forecast_date, item_id, model_trained_through, p50, p90, probability)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info(f"Cached {len(rows)} backtest rows for model_trained_through={model_trained_through}")
    except Exception as e:
        logger.warning(f"Failed to save backtest forecasts: {e}")


# ---------------------------------------------------------------------------
# Revenue backtest cache (point-in-time T→T+1 for all 4 models)
# ---------------------------------------------------------------------------

def get_missing_revenue_backtest_dates(
    conn,
    forecast_dates: List[str],
    model_names: List[str],
) -> List[str]:
    """Return forecast_dates that lack complete backtest cache for all 4 models."""
    ensure_tables_exist(conn)
    if not forecast_dates or not model_names:
        return list(forecast_dates)
    try:
        from datetime import datetime, timedelta
        missing = []
        for fd in forecast_dates:
            d = datetime.strptime(fd, "%Y-%m-%d").date()
            trained_through = (d - timedelta(days=1)).isoformat()
            placeholders = ",".join("?" * len(model_names))
            cur = conn.execute(
                f"""SELECT COUNT(DISTINCT model_name) FROM revenue_backtest_cache
                    WHERE forecast_date = ? AND model_trained_through = ?
                    AND model_name IN ({placeholders})""",
                [fd, trained_through] + list(model_names),
            )
            count = cur.fetchone()[0]
            if count < len(model_names):
                missing.append(fd)
        return missing
    except Exception:
        return list(forecast_dates)


def load_revenue_backtest_forecasts(
    conn,
    forecast_dates: List[str],
    model_names: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load cached revenue backtest forecasts. Returns Dict[model_name, list of rows]."""
    ensure_tables_exist(conn)
    results: Dict[str, List[Dict[str, Any]]] = {}
    if not forecast_dates:
        return results
    try:
        placeholders = ",".join("?" * len(forecast_dates))
        params: List[Any] = list(forecast_dates)
        q = f"""SELECT forecast_date, model_name, revenue, orders, pred_std, lower_95, upper_95
                FROM revenue_backtest_cache
                WHERE forecast_date IN ({placeholders})"""
        if model_names:
            q += " AND model_name IN (" + ",".join("?" * len(model_names)) + ")"
            params.extend(model_names)
        q += " ORDER BY model_name, forecast_date"
        cur = conn.execute(q, params)
        for row in cur.fetchall():
            model = row[1]
            entry = {
                "date": row[0],
                "revenue": row[2],
                "orders": row[3] or 0,
                "pred_std": row[4],
                "gp_lower": row[5],
                "gp_upper": row[6],
            }
            results.setdefault(model, []).append(entry)
    except Exception as e:
        logger.warning(f"Failed to load revenue backtest: {e}")
    return results


def save_revenue_backtest_forecasts(
    conn,
    model_name: str,
    forecasts: List[Dict[str, Any]],
    model_trained_through: str,
) -> None:
    """Persist revenue backtest forecasts for one model."""
    ensure_tables_exist(conn)
    if not forecasts:
        return
    rows = [
        (
            f.get("date"),
            model_name,
            model_trained_through,
            f.get("revenue"),
            f.get("orders", 0),
            f.get("pred_std"),
            f.get("gp_lower") or f.get("lower_95"),
            f.get("gp_upper") or f.get("upper_95"),
        )
        for f in forecasts
    ]
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO revenue_backtest_cache
               (forecast_date, model_name, model_trained_through,
                revenue, orders, pred_std, lower_95, upper_95)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info(f"Cached {len(rows)} {model_name} backtest rows for model_trained_through={model_trained_through}")
    except Exception as e:
        logger.warning(f"Failed to save revenue backtest: {e}")


# ---------------------------------------------------------------------------
# Volume forecast cache (variant-level volume predictions)
# ---------------------------------------------------------------------------

def is_volume_cache_fresh(conn, generated_on: str) -> bool:
    """Return True if volume_forecast_cache has rows for this generated_on date."""
    try:
        ensure_tables_exist(conn)
        cur = conn.execute(
            "SELECT COUNT(*) FROM volume_forecast_cache WHERE generated_on = ?",
            (generated_on,),
        )
        count = cur.fetchone()[0]
        return count > 0
    except Exception:
        return False


def get_latest_volume_cache_generated_on(conn) -> Optional[str]:
    """Return the most recent generated_on in volume_forecast_cache, or None if empty."""
    try:
        ensure_tables_exist(conn)
        cur = conn.execute(
            "SELECT MAX(generated_on) FROM volume_forecast_cache"
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def load_volume_forecasts(conn, generated_on: str) -> List[Dict[str, Any]]:
    """Load cached volume forecasts for a specific generation date (per menu item)."""
    ensure_tables_exist(conn)
    results: List[Dict[str, Any]] = []
    try:
        cur = conn.execute(
            """SELECT forecast_date, item_id, volume_value, unit,
                      p50, p90, probability, recommended_volume
               FROM volume_forecast_cache
               WHERE generated_on = ?
               ORDER BY forecast_date, item_id""",
            (generated_on,),
        )
        for row in cur.fetchall():
            results.append({
                "date": row[0],
                "item_id": row[1],
                "volume_value": row[2],
                "unit": row[3],
                "p50": row[4],
                "p90": row[5],
                "probability": row[6],
                "recommended_volume": row[7],
            })
    except Exception as e:
        logger.warning(f"Failed to load volume forecasts from cache: {e}")
    return results


def save_volume_forecasts(
    conn,
    forecasts: List[Dict[str, Any]],
    generated_on: str,
) -> None:
    """Persist volume forecast results to the cache."""
    ensure_tables_exist(conn)
    if not forecasts:
        return

    rows = []
    for f in forecasts:
        rows.append((
            f.get("date"),
            f.get("item_id"),
            generated_on,
            f.get("volume_value", 0),
            f.get("unit", "mg"),
            f.get("p50"),
            f.get("p90"),
            f.get("probability"),
            f.get("recommended_volume"),
        ))

    try:
        conn.executemany(
            """INSERT OR REPLACE INTO volume_forecast_cache
               (forecast_date, item_id, generated_on,
                volume_value, unit, p50, p90, probability, recommended_volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info(f"Cached {len(rows)} volume forecast rows for {generated_on}")
    except Exception as e:
        logger.warning(f"Failed to save volume forecasts to cache: {e}")


def get_missing_volume_backtest_dates(
    conn,
    forecast_dates: List[str],
    item_ids: List[str],
) -> List[str]:
    """Return forecast_dates that lack complete backtest cache for the given menu items."""
    ensure_tables_exist(conn)
    if not forecast_dates or not item_ids:
        return list(forecast_dates)
    try:
        from datetime import datetime, timedelta
        missing = []
        for fd in forecast_dates:
            d = datetime.strptime(fd, "%Y-%m-%d").date()
            trained_through = (d - timedelta(days=1)).isoformat()
            placeholders = ",".join("?" * len(item_ids))
            cur = conn.execute(
                f"""SELECT COUNT(*) FROM volume_backtest_cache
                    WHERE forecast_date = ? AND model_trained_through = ?
                    AND item_id IN ({placeholders})""",
                [fd, trained_through] + list(item_ids),
            )
            count = cur.fetchone()[0]
            if count < len(item_ids):
                missing.append(fd)
        return missing
    except Exception:
        return list(forecast_dates)


def load_volume_backtest_forecasts(
    conn,
    forecast_dates: List[str],
    item_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Load cached volume backtest forecasts (per menu item)."""
    ensure_tables_exist(conn)
    results: List[Dict[str, Any]] = []
    try:
        if not forecast_dates:
            return results
        placeholders = ",".join("?" * len(forecast_dates))
        params: List[Any] = list(forecast_dates)
        q = f"""SELECT forecast_date, item_id, volume_value, p50, p90, probability
                FROM volume_backtest_cache
                WHERE forecast_date IN ({placeholders})"""
        if item_ids:
            q += " AND item_id IN (" + ",".join("?" * len(item_ids)) + ")"
            params.extend(item_ids)
        q += " ORDER BY forecast_date, item_id"
        cur = conn.execute(q, params)
        for row in cur.fetchall():
            results.append({
                "date": row[0],
                "item_id": row[1],
                "volume_value": row[2],
                "p50": row[3],
                "p90": row[4],
                "probability": row[5],
            })
    except Exception as e:
        logger.warning(f"Failed to load volume backtest forecasts: {e}")
    return results


def save_volume_backtest_forecasts(
    conn,
    forecasts: List[Dict[str, Any]],
    model_trained_through: str,
) -> None:
    """Persist volume backtest forecasts to cache."""
    ensure_tables_exist(conn)
    if not forecasts:
        return
    rows = [
        (
            f.get("date"),
            f.get("item_id"),
            model_trained_through,
            f.get("volume_value"),
            f.get("p50"),
            f.get("p90"),
            f.get("probability"),
        )
        for f in forecasts
    ]
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO volume_backtest_cache
               (forecast_date, item_id, model_trained_through,
                volume_value, p50, p90, probability)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info(f"Cached {len(rows)} volume backtest rows for model_trained_through={model_trained_through}")
    except Exception as e:
        logger.warning(f"Failed to save volume backtest forecasts: {e}")
