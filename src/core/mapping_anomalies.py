"""
Silent-reuse surfacing for PetPooja id -> menu-item mappings.

Background: services/clustering_service.OrderItemCluster.add() keys on the
PetPooja itemid / addonid. The first order carrying an id fixes its menu-item
mapping; later orders with the same id inherit it via exact match. When the
mapping is verified it never re-enters the resolutions queue, so if PetPooja
recycles an id onto a different product the new product is silently booked as
the old one (see utils/mapping_core for the worked example and why a string
similarity threshold cannot catch it).

This module does not try to auto-classify. It reduces each incoming name to a
normalized product "core" (utils.mapping_core.mapping_core_key), remembers the
cores seen per id, and when a *new* core lands on an id that already carried a
different one — and the mapping is verified — records a row for a human to
triage on the resolutions page. The mapping itself is left untouched; the
operator either dismisses (a cosmetic relabel) or remaps (a real reuse).

Both tables are strictly local diagnostics. They are deliberately not referenced
by any cloud sync event builder or dev export payload, so they never leave the
machine.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from utils.mapping_core import mapping_core_key

logger = logging.getLogger(__name__)


def ensure_mapping_anomaly_schema(conn) -> None:
    """Create the local anomaly tables if missing. Idempotent; safe every call."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mapping_id_cores (
            order_item_id TEXT NOT NULL,
            core_key TEXT NOT NULL,
            is_addon INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (order_item_id, core_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mapping_anomalies (
            anomaly_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_item_id TEXT NOT NULL,
            core_key TEXT NOT NULL,
            baseline_core_key TEXT,
            name_raw TEXT NOT NULL,
            mapped_menu_item_id TEXT,
            mapped_name TEXT,
            is_addon INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            detected_via TEXT NOT NULL DEFAULT 'ingest',
            seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            UNIQUE (order_item_id, core_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mapping_anomalies_open ON mapping_anomalies(status, seen_at)"
    )


def _existing_cores(cursor, order_item_id: str) -> List[str]:
    cursor.execute(
        "SELECT core_key FROM mapping_id_cores WHERE order_item_id = ? ORDER BY first_seen ASC, rowid ASC",
        (str(order_item_id),),
    )
    return [str(r[0]) for r in cursor.fetchall()]


def record_core_and_flag(
    conn,
    *,
    order_item_id: str,
    name_raw: str,
    is_addon: bool,
    mapped_menu_item_id: Optional[str] = None,
    mapped_name: Optional[str] = None,
    is_verified: bool = False,
    detected_via: str = "ingest",
    cursor=None,
) -> Optional[Dict[str, Any]]:
    """
    Register the core of an incoming name for an id and flag a genuine change.

    Returns the anomaly dict when a new-core-on-verified-id anomaly was recorded,
    else None. Never raises: bookkeeping must not break ingest. Does NOT commit;
    the caller owns the transaction (mirrors OrderItemCluster.add()).
    """
    try:
        core = mapping_core_key(name_raw)
        if not core:
            return None

        oid = str(order_item_id)
        own_cursor = cursor is None
        cur = conn.cursor() if own_cursor else cursor
        try:
            existing = _existing_cores(cur, oid)
            if core in existing:
                return None  # already known for this id

            flagged: Optional[Dict[str, Any]] = None
            # Only a *change* on an id that already carried a different core is an
            # anomaly. The very first core for an id is its baseline, not a flag.
            # Unverified mappings already surface in the unclustered resolutions
            # queue, so restrict flags to the silent (verified) case.
            if existing and is_verified:
                baseline = existing[0]
                cur.execute(
                    """
                    INSERT OR IGNORE INTO mapping_anomalies (
                        order_item_id, core_key, baseline_core_key, name_raw,
                        mapped_menu_item_id, mapped_name, is_addon, status, detected_via
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
                    """,
                    (
                        oid, core, baseline, str(name_raw),
                        (str(mapped_menu_item_id) if mapped_menu_item_id is not None else None),
                        (str(mapped_name) if mapped_name is not None else None),
                        1 if is_addon else 0,
                        detected_via,
                    ),
                )
                if cur.rowcount:
                    flagged = {
                        "order_item_id": oid,
                        "core_key": core,
                        "baseline_core_key": baseline,
                        "name_raw": str(name_raw),
                        "mapped_menu_item_id": mapped_menu_item_id,
                        "mapped_name": mapped_name,
                        "is_addon": bool(is_addon),
                    }

            cur.execute(
                "INSERT OR IGNORE INTO mapping_id_cores (order_item_id, core_key, is_addon) VALUES (?, ?, ?)",
                (oid, core, 1 if is_addon else 0),
            )
            return flagged
        finally:
            if own_cursor:
                cur.close()
    except Exception:
        logger.debug("mapping anomaly bookkeeping skipped for %s", order_item_id, exc_info=True)
        return None


def list_open_anomalies(conn) -> List[Dict[str, Any]]:
    """Open anomalies enriched with current mapping + how many rows/qty are affected."""
    ensure_mapping_anomaly_schema(conn)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT a.anomaly_id, a.order_item_id, a.core_key, a.baseline_core_key,
                   a.name_raw, a.mapped_menu_item_id, a.mapped_name, a.is_addon,
                   a.detected_via, a.seen_at,
                   mv.menu_item_id AS current_menu_item_id,
                   mi.name AS current_mapped_name,
                   mi.type AS current_mapped_type,
                   mv.variant_id AS current_variant_id,
                   v.variant_name AS current_variant_name,
                   mv.is_verified AS current_is_verified
            FROM mapping_anomalies a
            LEFT JOIN menu_item_variants mv ON mv.order_item_id = a.order_item_id
            LEFT JOIN menu_items mi ON mi.menu_item_id = mv.menu_item_id
            LEFT JOIN variants v ON v.variant_id = mv.variant_id
            WHERE a.status = 'open'
            ORDER BY a.is_addon ASC, a.seen_at DESC, a.anomaly_id DESC
            """
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        def _table_exists(name: str) -> bool:
            return cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
            ).fetchone() is not None

        have_items = _table_exists("order_items")
        have_addons = _table_exists("order_item_addons")

        # Affected volume for the incoming (new-core) product under this id, so the
        # operator can gauge impact. Cheap per-row lookups; the list is small.
        for row in rows:
            row["affected_rows"] = 0
            row["affected_qty"] = 0
            table = "order_item_addons" if row["is_addon"] else "order_items"
            if (row["is_addon"] and not have_addons) or (not row["is_addon"] and not have_items):
                continue
            id_col = "petpooja_addonid" if row["is_addon"] else "petpooja_itemid"
            try:
                cur.execute(
                    f"""
                    SELECT COUNT(*) AS n, COALESCE(SUM(quantity), 0) AS qty
                    FROM {table}
                    WHERE TRIM(CAST({id_col} AS TEXT)) = ?
                      AND lower(name_raw) = lower(?)
                    """,
                    (str(row["order_item_id"]), str(row["name_raw"])),
                )
                agg = cur.fetchone()
                if agg:
                    row["affected_rows"] = int(agg[0] or 0)
                    row["affected_qty"] = int(agg[1] or 0)
            except Exception:
                logger.debug("affected-volume lookup skipped for %s", row["order_item_id"], exc_info=True)
        return rows
    finally:
        cur.close()


def dismiss_anomaly(conn, anomaly_id: int) -> Dict[str, Any]:
    """
    Mark an anomaly as a benign relabel.

    The core was already recorded in mapping_id_cores when the anomaly fired, so
    it will not re-flag on future orders. We only flip status here.
    """
    ensure_mapping_anomaly_schema(conn)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT order_item_id, core_key, status FROM mapping_anomalies WHERE anomaly_id = ?",
            (int(anomaly_id),),
        )
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "Anomaly not found"}
        if row[2] != "open":
            return {"status": "success", "message": "Anomaly already resolved", "anomaly_id": anomaly_id}

        cur.execute(
            "UPDATE mapping_anomalies SET status = 'dismissed', resolved_at = CURRENT_TIMESTAMP WHERE anomaly_id = ?",
            (int(anomaly_id),),
        )
        # Belt-and-suspenders: ensure the core is remembered so it never re-fires.
        cur.execute(
            "INSERT OR IGNORE INTO mapping_id_cores (order_item_id, core_key) VALUES (?, ?)",
            (str(row[0]), str(row[1])),
        )
        conn.commit()
        return {"status": "success", "message": "Marked as benign relabel", "anomaly_id": anomaly_id}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()


def close_anomalies_for_order_item(conn, order_item_id: str, *, resolution: str = "remapped") -> int:
    """Close any open anomalies for an id after it has been remapped. Returns count closed."""
    ensure_mapping_anomaly_schema(conn)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE mapping_anomalies SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE order_item_id = ? AND status = 'open'",
            (resolution, str(order_item_id)),
        )
        closed = cur.rowcount
        conn.commit()
        return int(closed or 0)
    except Exception:
        conn.rollback()
        return 0
    finally:
        cur.close()
