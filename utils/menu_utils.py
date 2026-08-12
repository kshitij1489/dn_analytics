"""
Menu Management Utilities (SQLite Version)
"""

import csv
import os
import re
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
import json
import uuid
from datetime import datetime, timezone
from src.core.itemcode_mapping import rebuild_itemcode_mappings_best_effort
from src.core.menu_assignment_schema import ensure_assignment_sync_schema
from src.core.order_item_key import (
    AssignmentKeyIndex,
    local_addon_pks_for_assignment_key,
    local_order_item_pks_for_assignment_key,
    update_local_order_rows_for_assignment_key,
)
from utils.id_generator import generate_deterministic_id
from utils.menu_item_variant_enforcement import ensure_menu_item_has_variant_mapping
from utils.variant_metadata import infer_variant_metadata

NULL_VARIANT_SENTINEL = "__NULL_VARIANT__"
NULL_VARIANT_LABEL = "UNASSIGNED"


def _commit_strict_plan(
    conn,
    plan,
    *,
    success_message: str,
    success_extra: Optional[Dict[str, Any]] = None,
    clear_item_models: bool = False,
    clear_volume_models: bool = False,
) -> Dict[str, Any]:
    """Rollback the capture transaction and commit via the server-authoritative path."""
    from src.core.menu_mutation_commit import commit_mutation, commit_result_to_menu_response
    from src.core.sync_identity import get_menu_state_revision

    plan.expected_menu_revision = get_menu_state_revision(conn)
    conn.rollback()
    result = commit_mutation(conn, plan)
    extra = dict(success_extra or {})
    if result.merge_id is not None:
        extra.setdefault("merge_id", result.merge_id)
    response = commit_result_to_menu_response(
        result,
        success_message=success_message,
        success_extra=extra,
    )
    if result.status == "ok":
        model_cleanup_error = _clear_impacted_models(
            clear_item_models=clear_item_models,
            clear_volume_models=clear_volume_models,
        )
        if model_cleanup_error and response.get("status") == "success":
            response["message"] = (
                f"{response['message']}. Cleared affected caches but could not delete all local models: {model_cleanup_error}"
            )
    return response


def _strict_mode_edit_blocked_response(conn, *, emit_sync_event: bool = True) -> Optional[Dict[str, Any]]:
    """Prevent legacy local-first edits when strict mode is on but cloud readiness is missing."""
    if emit_sync_event:
        try:
            from src.core.global_menu_schema import resolve_global_menu_capability

            global_status = resolve_global_menu_capability(conn)
        except Exception:
            global_status = None
        if global_status is not None and global_status.server_advertised:
            return {
                "status": "error",
                "message": (
                    "Canonical menu changes are group-owned in global menu mode; "
                    "use the global preview and commit workflow"
                ),
                "code": "global_menu_shadow_write_blocked",
                "recommended_action": "use_global_menu_mutations",
            }

    from src.core.menu_mutation_commit import strict_mode_edit_blocked_response

    return strict_mode_edit_blocked_response(conn, emit_sync_event=emit_sync_event)


def _build_catalog_delta_for_ids(cursor, item_ids: List[str], variant_ids: List[str]) -> Dict[str, Any]:
    catalog_delta = {"items": [], "variants": []}
    if item_ids:
        in_clause, params = _build_in_clause(item_ids)
        cursor.execute(f"SELECT menu_item_id, name, type, is_verified FROM menu_items WHERE menu_item_id {in_clause}", params)
        for r in cursor.fetchall():
            catalog_delta["items"].append({"menu_item_id": str(r[0]), "name": str(r[1]), "type": str(r[2]), "is_verified": bool(r[3])})
    if variant_ids:
        in_clause, params = _build_in_clause(variant_ids)
        variant_columns = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(variants)").fetchall()
            if len(row) > 1
        }
        select_columns = ["variant_id", "variant_name", "is_verified"]
        for optional_column in ("description", "unit", "value"):
            if optional_column in variant_columns:
                select_columns.append(optional_column)
        cursor.execute(
            f"SELECT {', '.join(select_columns)} FROM variants WHERE variant_id {in_clause}",
            params,
        )
        for r in cursor.fetchall():
            row = dict(zip(select_columns, r))
            variant = {
                "variant_id": str(row["variant_id"]),
                "variant_name": str(row["variant_name"]),
                "is_verified": bool(row["is_verified"]),
            }
            for optional_column in ("description", "unit", "value"):
                if optional_column in row:
                    variant[optional_column] = row[optional_column]
            catalog_delta["variants"].append(variant)
    return catalog_delta


def _catalog_delta_for_strict_commit(
    cursor,
    *,
    item_ids: Optional[List[str]] = None,
    variant_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Capture the full catalog projection for items/variants touched by an edit."""
    normalized_items = [str(item_id) for item_id in (item_ids or []) if item_id]
    normalized_variants = [str(variant_id) for variant_id in (variant_ids or []) if variant_id]
    return _build_catalog_delta_for_ids(
        cursor,
        list(dict.fromkeys(normalized_items)),
        list(dict.fromkeys(normalized_variants)),
    )


def _build_in_clause(items: List[str]) -> Tuple[str, List[str]]:
    """Helper to build IN (?, ?, ...) clause for SQLite."""
    if not items:
        return "IN ('')", []  # Return something that matches nothing
    placeholders = ",".join("?" * len(items))
    return f"IN ({placeholders})", list(items)


def _normalize_variant_key(variant_id: Any) -> str:
    if variant_id is None:
        return NULL_VARIANT_SENTINEL
    return str(variant_id)


def _decode_variant_key(variant_key: Any) -> Optional[str]:
    if variant_key in (None, NULL_VARIANT_SENTINEL, "None"):
        return None
    return str(variant_key)


def _build_variant_match_clause(column_name: str, variant_key: Any) -> Tuple[str, List[Any]]:
    decoded_variant_id = _decode_variant_key(variant_key)
    if decoded_variant_id is None:
        return f"{column_name} IS NULL", []
    return f"{column_name} = ?", [decoded_variant_id]


def _fetch_menu_item_variant_summary(
    conn,
    menu_item_id: str,
    variant_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aggregate variant usage for a menu item across mappings, order items, and addons."""
    summary: Dict[str, Dict[str, Any]] = {}

    def ensure_variant(variant_id: Any, variant_name: Optional[str]) -> Dict[str, Any]:
        variant_key = _normalize_variant_key(variant_id)
        if variant_key not in summary:
            summary[variant_key] = {
                "variant_id": variant_key,
                "variant_name": variant_name or (NULL_VARIANT_LABEL if variant_id is None else "UNKNOWN"),
                "order_item_rows": 0,
                "order_item_qty": 0,
                "addon_rows": 0,
                "addon_qty": 0,
                "mapping_rows": 0,
            }
        return summary[variant_key]

    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT oi.variant_id, v.variant_name, COUNT(*) as row_count, COALESCE(SUM(oi.quantity), 0) as qty
            FROM order_items oi
            LEFT JOIN variants v ON oi.variant_id = v.variant_id
            WHERE oi.menu_item_id = ?
            GROUP BY oi.variant_id, v.variant_name
        """, (menu_item_id,))
        for row in cursor.fetchall():
            variant = ensure_variant(row[0], row[1])
            variant["order_item_rows"] = int(row[2] or 0)
            variant["order_item_qty"] = int(row[3] or 0)

        cursor.execute("""
            SELECT oa.variant_id, v.variant_name, COUNT(*) as row_count, COALESCE(SUM(oa.quantity), 0) as qty
            FROM order_item_addons oa
            LEFT JOIN variants v ON oa.variant_id = v.variant_id
            WHERE oa.menu_item_id = ?
            GROUP BY oa.variant_id, v.variant_name
        """, (menu_item_id,))
        for row in cursor.fetchall():
            variant = ensure_variant(row[0], row[1])
            variant["addon_rows"] = int(row[2] or 0)
            variant["addon_qty"] = int(row[3] or 0)

        cursor.execute("""
            SELECT mv.variant_id, v.variant_name, COUNT(*) as row_count
            FROM menu_item_variants mv
            LEFT JOIN variants v ON mv.variant_id = v.variant_id
            WHERE mv.menu_item_id = ?
            GROUP BY mv.variant_id, v.variant_name
        """, (menu_item_id,))
        for row in cursor.fetchall():
            variant = ensure_variant(row[0], row[1])
            variant["mapping_rows"] = int(row[2] or 0)
    finally:
        cursor.close()

    for variant in summary.values():
        variant["total_rows"] = (
            variant["order_item_rows"] +
            variant["addon_rows"] +
            variant["mapping_rows"]
        )

    variants = sorted(
        summary.values(),
        key=lambda variant: (-variant["total_rows"], variant["variant_name"]),
    )

    if variant_key is None:
        return variants

    normalized_variant_key = _normalize_variant_key(variant_key)
    return [variant for variant in variants if variant["variant_id"] == normalized_variant_key]


def _recalculate_menu_item_stats(cursor, menu_item_id: str) -> None:
    cursor.execute("""
        UPDATE menu_items
        SET total_sold = (
                (SELECT COALESCE(SUM(oi.quantity), 0)
                 FROM order_items oi
                 JOIN orders o ON oi.order_id = o.order_id
                 WHERE oi.menu_item_id = ? AND o.order_status = 'Success') +
                (SELECT COALESCE(SUM(oia.quantity), 0)
                 FROM order_item_addons oia
                 JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                 JOIN orders o ON oi.order_id = o.order_id
                 WHERE oia.menu_item_id = ? AND o.order_status = 'Success')
            ),
            total_revenue = (
                (SELECT COALESCE(SUM(oi.total_price), 0)
                 FROM order_items oi
                 JOIN orders o ON oi.order_id = o.order_id
                 WHERE oi.menu_item_id = ? AND o.order_status = 'Success') +
                (SELECT COALESCE(SUM(oia.price * oia.quantity), 0)
                 FROM order_item_addons oia
                 JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                 JOIN orders o ON oi.order_id = o.order_id
                 WHERE oia.menu_item_id = ? AND o.order_status = 'Success')
            ),
            sold_as_item = (
                SELECT COALESCE(SUM(oi.quantity), 0)
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.order_id
                WHERE oi.menu_item_id = ? AND o.order_status = 'Success'
            ),
            sold_as_addon = (
                SELECT COALESCE(SUM(oia.quantity), 0)
                FROM order_item_addons oia
                JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                JOIN orders o ON oi.order_id = o.order_id
                WHERE oia.menu_item_id = ? AND o.order_status = 'Success'
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE menu_item_id = ?
    """, (
        menu_item_id,
        menu_item_id,
        menu_item_id,
        menu_item_id,
        menu_item_id,
        menu_item_id,
        menu_item_id,
    ))


def _global_link_table_exists(cursor, table: str) -> bool:
    return cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _is_global_menu_item_linked(cursor, menu_item_id: str) -> bool:
    return bool(
        _global_link_table_exists(cursor, "menu_item_global_links")
        and cursor.execute(
            "SELECT 1 FROM menu_item_global_links WHERE local_menu_item_id=?",
            (menu_item_id,),
        ).fetchone()
    )


def _sync_menu_item_resolution_state(cursor, menu_item_id: str) -> None:
    # In global mode the central canonical row owns verification and lifecycle.
    # Local assignment shape must not silently flip that authoritative flag or
    # garbage-collect its projection target.
    if _is_global_menu_item_linked(cursor, menu_item_id):
        return
    cursor.execute(
        "SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = ?",
        (menu_item_id,),
    )
    total_mappings = int(cursor.fetchone()[0] or 0)

    if total_mappings == 0:
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM order_items WHERE menu_item_id = ?) +
                (SELECT COUNT(*) FROM order_item_addons WHERE menu_item_id = ?)
            """,
            (menu_item_id, menu_item_id),
        )
        remaining_usage = int(cursor.fetchone()[0] or 0)

        if remaining_usage == 0:
            # A bare local projection owner is intentionally retained: global
            # rules/redirects may route the next unseen POS locator to it.
            # Another menu_item may point here via the suggestion_id self-FK
            # (a stale merge suggestion). Clear those pointers first, else the
            # DELETE trips FOREIGN KEY constraint failed and aborts the whole
            # menu ground-truth pull (surfaces to the user as "Sync Failed").
            cursor.execute(
                "UPDATE menu_items SET suggestion_id = NULL WHERE suggestion_id = ?",
                (menu_item_id,),
            )
            cursor.execute("DELETE FROM menu_items WHERE menu_item_id = ?", (menu_item_id,))
        else:
            cursor.execute(
                """
                UPDATE menu_items
                SET is_verified = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE menu_item_id = ?
                """,
                (menu_item_id,),
            )
        return

    cursor.execute(
        "SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = ? AND is_verified = 0",
        (menu_item_id,),
    )
    unresolved_mappings = int(cursor.fetchone()[0] or 0)

    cursor.execute("""
        UPDATE menu_items
        SET is_verified = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE menu_item_id = ?
    """, (1 if unresolved_mappings == 0 else 0, menu_item_id))


def sweep_orphan_menu_entities(cursor) -> Dict[str, List[str]]:
    """
    State-scoped GC: delete menu_items and variants that nothing references
    (no menu_item_variants row, no order_items row, no order_item_addons row).

    _sync_menu_item_resolution_state only garbage-collects the ids in the
    current batch's touched set, and reference-moving appliers can strip an
    item's last reference without ever putting that id in the touched set
    (e.g. update_local_order_rows_for_assignment_key moves order rows whose
    previous owner is never recorded). A missed id was previously a permanent
    husk — visible in /menu/list and /menu/variants/list dropdowns but absent
    from the Menu Matrix. This sweep re-derives liveness from state instead of
    events, so it is idempotent and self-heals husks from any past cause.
    Pattern note: docs/MENU_SYNC_ARCHITECTURE.md ("state-scoped GC").
    """
    global_item_guard = (
        """
            AND NOT EXISTS (
                SELECT 1 FROM menu_item_global_links gl
                WHERE gl.local_menu_item_id = mi.menu_item_id
            )
        """
        if _global_link_table_exists(cursor, "menu_item_global_links")
        else ""
    )
    orphan_item_sql = f"""
        SELECT mi.menu_item_id FROM menu_items mi
        WHERE NOT EXISTS (
                SELECT 1 FROM menu_item_variants miv
                WHERE miv.menu_item_id = mi.menu_item_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM order_items oi
                WHERE oi.menu_item_id = mi.menu_item_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM order_item_addons oa
                WHERE oa.menu_item_id = mi.menu_item_id
            )
            {global_item_guard}
    """
    cursor.execute(orphan_item_sql)
    husk_item_ids = [str(row[0]) for row in cursor.fetchall()]

    if husk_item_ids:
        placeholders = ",".join("?" for _ in husk_item_ids)
        # Stale merge suggestions may point at a husk via the suggestion_id
        # self-FK; clear them or the DELETE trips FOREIGN KEY constraint failed.
        cursor.execute(
            f"UPDATE menu_items SET suggestion_id = NULL WHERE suggestion_id IN ({placeholders})",
            husk_item_ids,
        )
        cursor.execute(
            f"DELETE FROM menu_items WHERE menu_item_id IN ({placeholders})",
            husk_item_ids,
        )

    # Variants have the same husk failure mode (merge moves the last mapping
    # off a variant, nothing deletes the row) and the same dropdown symptom.
    # Unlike menu items, a bare variant row is a legitimate transient state:
    # POST /menu/variants/create inserts the row before any mapping references
    # it. A 7-day grace window keeps those alive; husks converge on a later
    # sweep. NULL created_at (pre-column rows) counts as old. Schemas without
    # the created_at column (legacy) skip the variant sweep entirely — no age
    # signal, no delete. The UNKNOWN parking variant needs no exemption: it is
    # recreated on demand via ensure_variant_exists.
    husk_variant_ids: List[str] = []
    cursor.execute("PRAGMA table_info(variants)")
    if any(row[1] == "created_at" for row in cursor.fetchall()):
        global_variant_guard = (
            """
                AND NOT EXISTS (
                    SELECT 1 FROM variant_global_links gl
                    WHERE gl.local_variant_id = v.variant_id
                )
            """
            if _global_link_table_exists(cursor, "variant_global_links")
            else ""
        )
        cursor.execute(
            f"""
            SELECT v.variant_id FROM variants v
            WHERE (v.created_at IS NULL OR v.created_at < datetime('now', '-7 days'))
                AND NOT EXISTS (
                    SELECT 1 FROM menu_item_variants miv
                    WHERE miv.variant_id = v.variant_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM order_items oi
                    WHERE oi.variant_id = v.variant_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM order_item_addons oa
                    WHERE oa.variant_id = v.variant_id
                )
                {global_variant_guard}
            """
        )
        husk_variant_ids = [str(row[0]) for row in cursor.fetchall()]
        if husk_variant_ids:
            placeholders = ",".join("?" for _ in husk_variant_ids)
            cursor.execute(
                f"DELETE FROM variants WHERE variant_id IN ({placeholders})",
                husk_variant_ids,
            )

    return {"menu_item_ids": husk_item_ids, "variant_ids": husk_variant_ids}


def _fetch_menu_item_record(cursor, item_id: str):
    cursor.execute("""
        SELECT menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon
        FROM menu_items
        WHERE menu_item_id = ?
    """, (item_id,))
    return cursor.fetchone()


def _update_merge_target_stats(cursor, target_id: str, source_metrics: Tuple[Any, Any, Any, Any]) -> None:
    source_sold, source_revenue, source_as_item, source_as_addon = source_metrics
    cursor.execute("""
        UPDATE menu_items
        SET total_sold = COALESCE(total_sold, 0) + ?,
            sold_as_item = COALESCE(sold_as_item, 0) + ?,
            sold_as_addon = COALESCE(sold_as_addon, 0) + ?,
            total_revenue = COALESCE(total_revenue, 0) + ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE menu_item_id = ?
    """, (source_sold or 0, source_as_item or 0, source_as_addon or 0, source_revenue or 0, target_id))


def _insert_merge_history(cursor, source_id: str, target_id: str, source_name: str, source_type: str, payload: Any) -> int:
    cursor.execute("""
        INSERT INTO merge_history (source_id, target_id, source_name, source_type, affected_order_items)
        VALUES (?, ?, ?, ?, ?)
    """, (source_id, target_id, source_name, source_type, json.dumps(payload)))
    return int(cursor.lastrowid)


def _ensure_variant(conn, variant_name: str) -> str:
    """Create or reuse a verified variant row."""
    normalized_name = variant_name.strip()
    if not normalized_name:
        raise ValueError("Variant name cannot be empty")

    variant_id = generate_deterministic_id(normalized_name)
    metadata = infer_variant_metadata(normalized_name)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO variants (variant_id, variant_name, unit, value, is_verified)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT (variant_id) DO UPDATE SET
                variant_name = excluded.variant_name,
                unit = COALESCE(excluded.unit, unit),
                value = COALESCE(excluded.value, value),
                is_verified = 1
        """, (variant_id, normalized_name, metadata["unit"], metadata["value"]))
        return variant_id
    finally:
        cursor.close()


def create_variant_type(
    conn,
    variant_name: str,
    description: Optional[str] = None,
    unit: Optional[str] = None,
    value: Optional[float] = None,
) -> Dict[str, Any]:
    """Create a new verified variant type from the Variants tab.

    The name is canonicalized to UPPER_SNAKE_CASE and the ID is derived with
    generate_deterministic_id — the same scheme the clustering pipeline uses —
    so a later clustering run that produces this variant name reuses this row
    (ON CONFLICT variant_id) instead of tripping the UNIQUE(variant_name)
    constraint with a second ID.
    """
    normalized_name = re.sub(r"[\s_]+", "_", str(variant_name or "").strip().upper()).strip("_")
    if not normalized_name:
        return {"status": "error", "message": "Variant name cannot be empty."}

    variant_id = generate_deterministic_id(normalized_name)
    metadata = infer_variant_metadata(normalized_name, {"unit": unit, "value": value})
    description_text = (description or "").strip() or None

    blocked = _strict_mode_edit_blocked_response(conn)
    if blocked:
        return blocked

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT variant_name FROM variants WHERE variant_id = ?", (variant_id,))
        existing = cursor.fetchone()
        if existing:
            return {
                "status": "error",
                "message": f"Variant type '{existing[0]}' already exists.",
            }

        variant_columns = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(variants)").fetchall()
            if len(row) > 1
        }
        insert_columns = ["variant_id", "variant_name", "is_verified"]
        values = [variant_id, normalized_name, 1]
        optional_values = {
            "description": description_text,
            "unit": metadata["unit"],
            "value": metadata["value"],
        }
        for column_name, column_value in optional_values.items():
            if column_name in variant_columns:
                insert_columns.append(column_name)
                values.append(column_value)
        placeholders = ", ".join("?" for _ in insert_columns)
        cursor.execute(
            f"""
            INSERT INTO variants ({", ".join(insert_columns)})
            VALUES ({placeholders})
            """,
            values,
        )
        from src.core.menu_mutation_commit import (
            MUTATION_TYPE_CATALOG_UPDATE,
            build_plan,
        )

        plan = build_plan(
            mutation_type=MUTATION_TYPE_CATALOG_UPDATE,
            catalog_delta=_catalog_delta_for_strict_commit(
                cursor,
                variant_ids=[variant_id],
            ),
        )
        return _commit_strict_plan(
            conn,
            plan,
            success_message=f"Variant type '{normalized_name}' created.",
            success_extra={
                "variant_id": variant_id,
                "variant_name": normalized_name,
                "unit": metadata["unit"],
                "value": metadata["value"],
            },
        )
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cursor.close()


def _reassign_menu_item_variant(cursor, menu_item_id: str, variant_id: str) -> None:
    """Apply a single variant assignment across all rows currently linked to a menu item."""
    cursor.execute("""
        UPDATE order_items
        SET variant_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE menu_item_id = ?
    """, (variant_id, menu_item_id))

    cursor.execute("""
        UPDATE order_item_addons
        SET variant_id = ?
        WHERE menu_item_id = ?
    """, (variant_id, menu_item_id))

    cursor.execute("""
        UPDATE menu_item_variants
        SET variant_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE menu_item_id = ?
    """, (variant_id, menu_item_id))


def _clear_volume_forecast_cache(cursor) -> Dict[str, int]:
    """Clear cached volume forecasts because variant/unit assignments changed."""
    cleared = {"volume_forecast_cache": 0, "volume_backtest_cache": 0}
    for table_name in cleared:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        )
        if cursor.fetchone():
            cursor.execute(f"DELETE FROM {table_name}")
            cleared[table_name] = cursor.rowcount
            cursor.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))
    return cleared


def _clear_item_and_volume_forecast_cache(cursor, menu_item_ids: List[str]) -> Dict[str, int]:
    """Clear item/volume forecast caches for the affected menu items."""
    cleared = {
        "item_forecast_cache": 0,
        "item_backtest_cache": 0,
        "volume_forecast_cache": 0,
        "volume_backtest_cache": 0,
    }
    normalized_ids = []
    seen_ids = set()
    for menu_item_id in menu_item_ids:
        normalized_menu_item_id = str(menu_item_id or "").strip()
        if not normalized_menu_item_id or normalized_menu_item_id in seen_ids:
            continue
        seen_ids.add(normalized_menu_item_id)
        normalized_ids.append(normalized_menu_item_id)

    if not normalized_ids:
        return cleared

    placeholders = ",".join("?" for _ in normalized_ids)
    for table_name in cleared:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        )
        if cursor.fetchone():
            cursor.execute(
                f"DELETE FROM {table_name} WHERE item_id IN ({placeholders})",
                normalized_ids,
            )
            cleared[table_name] = cursor.rowcount
    return cleared


def _retarget_menu_item_suggestions(
    cursor,
    source_menu_item_id: str,
    target_menu_item_id: str,
) -> List[Dict[str, str]]:
    """Point suggestion references at the surviving target before a source item is deleted."""
    source_menu_item_id = str(source_menu_item_id or "").strip()
    target_menu_item_id = str(target_menu_item_id or "").strip()
    if not source_menu_item_id or not target_menu_item_id or source_menu_item_id == target_menu_item_id:
        return []

    cursor.execute("PRAGMA table_info(menu_items)")
    if "suggestion_id" not in {str(row[1]) for row in cursor.fetchall()}:
        return []

    cursor.execute(
        """
        SELECT menu_item_id
        FROM menu_items
        WHERE suggestion_id = ?
        """,
        (source_menu_item_id,),
    )
    rows = [
        {
            "menu_item_id": str(row[0]),
            "old_suggestion_id": source_menu_item_id,
            "new_suggestion_id": target_menu_item_id,
        }
        for row in cursor.fetchall()
    ]
    if rows:
        cursor.execute(
            """
            UPDATE menu_items
            SET suggestion_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE suggestion_id = ?
            """,
            (target_menu_item_id, source_menu_item_id),
        )
    return rows


def _restore_menu_item_suggestions(cursor, suggestion_rows: List[Dict[str, Any]]) -> None:
    """Restore suggestion references captured before a merge or variant move."""
    cursor.execute("PRAGMA table_info(menu_items)")
    if "suggestion_id" not in {str(row[1]) for row in cursor.fetchall()}:
        return

    for row in suggestion_rows or []:
        menu_item_id = str(row.get("menu_item_id") or "").strip()
        old_suggestion_id = str(row.get("old_suggestion_id") or "").strip()
        if not menu_item_id or not old_suggestion_id:
            continue
        cursor.execute(
            """
            UPDATE menu_items
            SET suggestion_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = ?
            """,
            (old_suggestion_id, menu_item_id),
        )


def _clear_impacted_models(
    clear_item_models: bool = False,
    clear_volume_models: bool = False,
) -> Optional[str]:
    """Legacy hook after menu assignment moves — local ML models removed in Phase 5."""
    return None


def _update_rows_for_variant_mapping(
    cursor,
    table_name: str,
    set_menu_item_id: str,
    set_variant_id: str,
    source_menu_item_id: str,
    source_variant_key: str,
) -> None:
    decoded_variant_id = _decode_variant_key(source_variant_key)
    # Mapping rows rewritten locally become provisional until the server echo
    # acknowledges them (assignment sync, plan Phase C3).
    pending_clause = ", pending_local = 1, assignment_seq = NULL" if table_name == "menu_item_variants" else ""
    if decoded_variant_id is None:
        cursor.execute(f"""
            UPDATE {table_name}
            SET menu_item_id = ?, variant_id = ?{pending_clause}
            WHERE menu_item_id = ? AND variant_id IS NULL
        """, (set_menu_item_id, set_variant_id, source_menu_item_id))
    else:
        cursor.execute(f"""
            UPDATE {table_name}
            SET menu_item_id = ?, variant_id = ?{pending_clause}
            WHERE menu_item_id = ? AND variant_id = ?
        """, (set_menu_item_id, set_variant_id, source_menu_item_id, decoded_variant_id))


def preview_merge_menu_items(
    conn,
    source_id: str,
    target_id: str,
    source_variant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Preview the impact of merging one menu item into another."""
    if source_id == target_id and source_variant_id is None:
        return {"status": "error", "message": "Cannot merge item into itself"}

    cursor = conn.cursor()
    try:
        source = _fetch_menu_item_record(cursor, source_id)
        target = _fetch_menu_item_record(cursor, target_id)

        if not source or not target:
            return {"status": "error", "message": "Source or Target item not found"}

        source_variants = _fetch_menu_item_variant_summary(conn, source_id, source_variant_id)
        if source_variant_id is not None and not source_variants:
            return {"status": "error", "message": "Source variant was not found"}

        if source_variant_id is None:
            cursor.execute("SELECT COUNT(*) FROM order_items WHERE menu_item_id = ?", (source_id,))
            order_items_relinked = int(cursor.fetchone()[0] or 0)

            cursor.execute("SELECT COUNT(*) FROM order_item_addons WHERE menu_item_id = ?", (source_id,))
            addon_items_relinked = int(cursor.fetchone()[0] or 0)

            cursor.execute("SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = ?", (source_id,))
            mappings_updated = int(cursor.fetchone()[0] or 0)

            source_total_sold = int(source[4] or 0)
            source_total_revenue = float(source[5] or 0)
        else:
            variant_clause, variant_params = _build_variant_match_clause("variant_id", source_variant_id)

            cursor.execute(
                f"SELECT COUNT(*) FROM order_items WHERE menu_item_id = ? AND {variant_clause}",
                [source_id] + variant_params,
            )
            order_items_relinked = int(cursor.fetchone()[0] or 0)

            cursor.execute(
                f"SELECT COUNT(*) FROM order_item_addons WHERE menu_item_id = ? AND {variant_clause}",
                [source_id] + variant_params,
            )
            addon_items_relinked = int(cursor.fetchone()[0] or 0)

            cursor.execute(
                f"SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = ? AND {variant_clause}",
                [source_id] + variant_params,
            )
            mappings_updated = int(cursor.fetchone()[0] or 0)

            source_total_sold = int(sum(
                int(variant.get("order_item_qty") or 0) + int(variant.get("addon_qty") or 0)
                for variant in source_variants
            ))

            cursor.execute(
                f"""
                SELECT COALESCE(SUM(total_price), 0)
                FROM order_items
                WHERE menu_item_id = ? AND {variant_clause}
                """,
                [source_id] + variant_params,
            )
            order_revenue = float(cursor.fetchone()[0] or 0)

            cursor.execute(
                f"""
                SELECT COALESCE(SUM(price * quantity), 0)
                FROM order_item_addons
                WHERE menu_item_id = ? AND {variant_clause}
                """,
                [source_id] + variant_params,
            )
            addon_revenue = float(cursor.fetchone()[0] or 0)
            source_total_revenue = order_revenue + addon_revenue

        preview = {
            "status": "success",
            "source": {
                "menu_item_id": str(source[0]),
                "name": source[1],
                "type": source[2],
                "is_verified": bool(source[3]),
            },
            "target": {
                "menu_item_id": str(target[0]),
                "name": target[1],
                "type": target[2],
                "is_verified": bool(target[3]),
            },
            "stats": {
                "order_items_relinked": order_items_relinked,
                "addon_items_relinked": addon_items_relinked,
                "mappings_updated": mappings_updated,
                "source_total_sold": source_total_sold,
                "source_total_revenue": source_total_revenue,
            },
        }
        preview["source_variants"] = source_variants if source_variant_id is not None else _fetch_menu_item_variant_summary(conn, source_id)
        preview["target_variants"] = _fetch_menu_item_variant_summary(conn, target_id)
        return preview
    except Exception as e:
        return {"status": "error", "message": f"Merge preview failed: {e}"}
    finally:
        cursor.close()


def merge_menu_items_with_variant_mappings(
    conn,
    source_id: str,
    target_id: str,
    variant_mappings: List[Dict[str, Any]],
    emit_sync_event: bool = True,
) -> Dict[str, Any]:
    """Merge a source item into a target item while remapping source variants."""
    if source_id == target_id:
        return {"status": "error", "message": "Cannot merge item into itself"}

    blocked = _strict_mode_edit_blocked_response(conn, emit_sync_event=emit_sync_event)
    if blocked:
        return blocked

    preview = preview_merge_menu_items(conn, source_id, target_id)
    if preview["status"] != "success":
        return preview

    source_variants = preview["source_variants"]
    if not source_variants:
        return merge_menu_items(conn, source_id, target_id)

    if not variant_mappings:
        return {"status": "error", "message": "Variant mappings are required"}

    source_variant_ids = {variant["variant_id"] for variant in source_variants}
    requested_source_ids = {mapping["source_variant_id"] for mapping in variant_mappings}

    if len(requested_source_ids) != len(variant_mappings):
        return {"status": "error", "message": "Duplicate source variant mappings were provided"}

    unknown_source_ids = requested_source_ids - source_variant_ids
    if unknown_source_ids:
        return {"status": "error", "message": "Unknown source variant mapping provided"}

    missing_variant_ids = source_variant_ids - requested_source_ids
    if missing_variant_ids:
        missing_names = [
            variant["variant_name"]
            for variant in source_variants
            if variant["variant_id"] in missing_variant_ids
        ]
        return {"status": "error", "message": f"Missing variant mapping for: {', '.join(missing_names)}"}

    ensure_assignment_sync_schema(conn)
    cursor = conn.cursor()
    try:
        source = _fetch_menu_item_record(cursor, source_id)
        target = _fetch_menu_item_record(cursor, target_id)
        if not source or not target:
            return {"status": "error", "message": "Source or Target item not found"}

        source_name = source[1]
        source_type = source[2]
        source_metrics = (source[4], source[5], source[6], source[7])

        resolved_variant_ids: Dict[str, str] = {}
        for mapping in variant_mappings:
            source_variant_id = mapping["source_variant_id"]
            target_variant_id = mapping.get("target_variant_id")
            new_variant_name = (mapping.get("new_variant_name") or "").strip()

            if not target_variant_id and not new_variant_name:
                return {"status": "error", "message": "Every source variant must map to an existing or new target variant"}

            if target_variant_id:
                cursor.execute("SELECT variant_id FROM variants WHERE variant_id = ?", (target_variant_id,))
                exists = cursor.fetchone()
                if not exists:
                    return {"status": "error", "message": "Selected target variant was not found"}
                resolved_variant_ids[source_variant_id] = target_variant_id
            else:
                resolved_variant_ids[source_variant_id] = _ensure_variant(conn, new_variant_name)

        cursor.execute("SELECT order_item_id, variant_id FROM menu_item_variants WHERE menu_item_id = ?", (source_id,))
        mapping_rows = cursor.fetchall()

        cursor.execute("SELECT order_item_id, variant_id FROM order_items WHERE menu_item_id = ?", (source_id,))
        order_item_rows = cursor.fetchall()

        cursor.execute("SELECT order_item_addon_id, variant_id FROM order_item_addons WHERE menu_item_id = ?", (source_id,))
        addon_rows = cursor.fetchall()

        suggestion_rows = _retarget_menu_item_suggestions(cursor, source_id, target_id)
        history_payload = {
            "kind": "variant_merge_v1",
            "suggestion_refs": suggestion_rows,
            "mapping_rows": [
                {
                    "order_item_id": row[0],
                    "old_variant_id": row[1],
                    "new_variant_id": resolved_variant_ids.get(_normalize_variant_key(row[1]), row[1]),
                }
                for row in mapping_rows
            ],
            "order_items": [
                {
                    "order_item_id": row[0],
                    "old_variant_id": row[1],
                    "new_variant_id": resolved_variant_ids.get(_normalize_variant_key(row[1]), row[1]),
                }
                for row in order_item_rows
            ],
            "order_item_addons": [
                {
                    "order_item_addon_id": row[0],
                    "old_variant_id": row[1],
                    "new_variant_id": resolved_variant_ids.get(_normalize_variant_key(row[1]), row[1]),
                }
                for row in addon_rows
            ],
        }
        merge_id = _insert_merge_history(cursor, source_id, target_id, source_name, source_type, history_payload)

        _update_merge_target_stats(cursor, target_id, source_metrics)

        for source_variant_id, resolved_variant_id in resolved_variant_ids.items():
            _update_rows_for_variant_mapping(cursor, "order_items", target_id, resolved_variant_id, source_id, source_variant_id)
            _update_rows_for_variant_mapping(cursor, "order_item_addons", target_id, resolved_variant_id, source_id, source_variant_id)
            _update_rows_for_variant_mapping(cursor, "menu_item_variants", target_id, resolved_variant_id, source_id, source_variant_id)

        cursor.execute("DELETE FROM menu_items WHERE menu_item_id = ?", (source_id,))
        ensure_menu_item_has_variant_mapping(conn, target_id, cursor=cursor)
        cleared_caches = _clear_item_and_volume_forecast_cache(cursor, [source_id, target_id])

        if emit_sync_event:
            from src.core.menu_mutation_commit import (
                MUTATION_TYPE_MENU_MERGE_APPLIED,
                build_plan,
            )
            from src.core.menu_merge_sync_events import (
                EVENT_TYPE_APPLIED,
                build_menu_merge_event_payload,
            )

            event = build_menu_merge_event_payload(conn, merge_id, EVENT_TYPE_APPLIED)
            if not event:
                conn.rollback()
                return {"status": "error", "message": "Failed to build merge event"}
            plan = build_plan(
                mutation_type=MUTATION_TYPE_MENU_MERGE_APPLIED,
                event=event,
                catalog_delta=_catalog_delta_for_strict_commit(
                    cursor,
                    item_ids=[target_id, source_id],
                    variant_ids=list(resolved_variant_ids.values()) + list(resolved_variant_ids.keys()),
                ),
                order_item_ids=[
                    str(row["order_item_id"])
                    for row in history_payload.get("mapping_rows", [])
                    if isinstance(row, dict) and row.get("order_item_id")
                ],
            )
            return _commit_strict_plan(
                conn,
                plan,
                success_message=f"Merged '{source_name}' into '{target[1]}' with variant mapping",
                success_extra={
                    "stats": {
                        "variant_mappings": len(resolved_variant_ids),
                        "source_total_sold": int(source[4] or 0),
                        "source_total_revenue": float(source[5] or 0),
                        "suggestion_refs_updated": len(suggestion_rows),
                        "item_forecast_cache_cleared": cleared_caches["item_forecast_cache"],
                        "item_backtest_cache_cleared": cleared_caches["item_backtest_cache"],
                        "volume_forecast_cache_cleared": cleared_caches["volume_forecast_cache"],
                        "volume_backtest_cache_cleared": cleared_caches["volume_backtest_cache"],
                    }
                },
                clear_item_models=True,
                clear_volume_models=True,
            )

        rebuild_itemcode_mappings_best_effort(conn, cursor=cursor)
        conn.commit()
        model_cleanup_error = _clear_impacted_models(clear_item_models=True, clear_volume_models=True)

        message = f"Merged '{source_name}' into '{target[1]}' with variant mapping"
        if model_cleanup_error:
            message = f"{message}. Cleared affected caches but could not delete all local models: {model_cleanup_error}"

        return {
            "status": "success",
            "message": message,
            "merge_id": merge_id,
            "stats": {
                "variant_mappings": len(resolved_variant_ids),
                "source_total_sold": int(source[4] or 0),
                "source_total_revenue": float(source[5] or 0),
                "suggestion_refs_updated": len(suggestion_rows),
                "item_forecast_cache_cleared": cleared_caches["item_forecast_cache"],
                "item_backtest_cache_cleared": cleared_caches["item_backtest_cache"],
                "volume_forecast_cache_cleared": cleared_caches["volume_forecast_cache"],
                "volume_backtest_cache_cleared": cleared_caches["volume_backtest_cache"],
            },
        }
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()


def merge_menu_items(
    conn,
    source_id: str,
    target_id: str,
    adopt_source_prices: bool = False,
    emit_sync_event: bool = True,
) -> Dict[str, Any]:
    """
    Merge source_id (UUID) into target_id (UUID).
    
    Actions:
    1. Transfer stats (revenue, sold count)
    2. Re-link order_items
    3. Re-link order_item_addons
    4. Re-link item mappings (menu_item_variants)
    5. Delete source item
    """
    if source_id == target_id:
        return {"status": "error", "message": "Cannot merge item into itself"}

    blocked = _strict_mode_edit_blocked_response(conn, emit_sync_event=emit_sync_event)
    if blocked:
        return blocked

    ensure_assignment_sync_schema(conn)
    cursor = conn.cursor()
    try:
        # 1. Get Details
        cursor.execute("SELECT name, type, total_sold, total_revenue, sold_as_item, sold_as_addon FROM menu_items WHERE menu_item_id = ?", (source_id,))
        source = cursor.fetchone()
        
        cursor.execute("SELECT name, type, total_sold, total_revenue, sold_as_item, sold_as_addon FROM menu_items WHERE menu_item_id = ?", (target_id,))
        target = cursor.fetchone()
        
        if not source or not target:
            return {"status": "error", "message": "Source or Target item not found"}
            
        source_name, source_type, source_sold, source_revenue, source_as_item, source_as_addon = source
        target_name, target_type, target_sold, target_revenue, target_as_item, target_as_addon = target
        
        # 1.5 Record History (Collect affected order_item_ids from mappings)
        cursor.execute("SELECT order_item_id FROM menu_item_variants WHERE menu_item_id = ?", (source_id,))
        affected_ids = [row[0] for row in cursor.fetchall()]
        suggestion_rows = _retarget_menu_item_suggestions(cursor, source_id, target_id)
        history_payload = {
            "kind": "basic_merge_v1",
            "affected_order_item_ids": affected_ids,
            "suggestion_refs": suggestion_rows,
        }
        merge_id = _insert_merge_history(cursor, source_id, target_id, source_name, source_type, history_payload)
        
        # 2. Update Target Stats
        _update_merge_target_stats(cursor, target_id, (source_sold, source_revenue, source_as_item, source_as_addon))
        
        # 3. Relink Order Items
        cursor.execute("""
            UPDATE order_items 
            SET menu_item_id = ? 
            WHERE menu_item_id = ?
        """, (target_id, source_id))
        relinked_count = cursor.rowcount
        
        # 4. Relink Order Item Addons
        cursor.execute("""
            UPDATE order_item_addons 
            SET menu_item_id = ? 
            WHERE menu_item_id = ?
        """, (target_id, source_id))
        
        # 5. Relink Mappings (provisional until the server echo acks them)
        cursor.execute("""
            UPDATE menu_item_variants
            SET menu_item_id = ?, pending_local = 1, assignment_seq = NULL
            WHERE menu_item_id = ?
        """, (target_id, source_id))
        mappings_updated = cursor.rowcount

        # 6. Delete Source Item
        cursor.execute("DELETE FROM menu_items WHERE menu_item_id = ?", (source_id,))
        ensure_menu_item_has_variant_mapping(conn, target_id, cursor=cursor)
        cleared_caches = _clear_item_and_volume_forecast_cache(cursor, [source_id, target_id])

        if emit_sync_event:
            from src.core.menu_mutation_commit import (
                MUTATION_TYPE_MENU_MERGE_APPLIED,
                build_plan,
            )
            from src.core.menu_merge_sync_events import (
                EVENT_TYPE_APPLIED,
                build_menu_merge_event_payload,
            )

            event = build_menu_merge_event_payload(conn, merge_id, EVENT_TYPE_APPLIED)
            if not event:
                conn.rollback()
                return {"status": "error", "message": "Failed to build merge event"}
            plan = build_plan(
                mutation_type=MUTATION_TYPE_MENU_MERGE_APPLIED,
                event=event,
                catalog_delta=_catalog_delta_for_strict_commit(
                    cursor,
                    item_ids=[target_id, source_id],
                ),
                order_item_ids=affected_ids,
            )
            return _commit_strict_plan(
                conn,
                plan,
                success_message=f"Merged '{source_name}' into '{target_name}'",
                success_extra={
                    "stats": {
                        "orders_relinked": relinked_count,
                        "mappings_updated": mappings_updated,
                        "revenue_added": float(source_revenue or 0),
                        "suggestion_refs_updated": len(suggestion_rows),
                        "item_forecast_cache_cleared": cleared_caches["item_forecast_cache"],
                        "item_backtest_cache_cleared": cleared_caches["item_backtest_cache"],
                        "volume_forecast_cache_cleared": cleared_caches["volume_forecast_cache"],
                        "volume_backtest_cache_cleared": cleared_caches["volume_backtest_cache"],
                    }
                },
                clear_item_models=True,
                clear_volume_models=True,
            )

        rebuild_itemcode_mappings_best_effort(conn, cursor=cursor)
        conn.commit()
        model_cleanup_error = _clear_impacted_models(clear_item_models=True, clear_volume_models=True)

        message = f"Merged '{source_name}' into '{target_name}'"
        if model_cleanup_error:
            message = f"{message}. Cleared affected caches but could not delete all local models: {model_cleanup_error}"

        return {
            "status": "success", 
            "message": message,
            "merge_id": merge_id,
            "stats": {
                "orders_relinked": relinked_count,
                "mappings_updated": mappings_updated,
                "revenue_added": float(source_revenue or 0),
                "suggestion_refs_updated": len(suggestion_rows),
                "item_forecast_cache_cleared": cleared_caches["item_forecast_cache"],
                "item_backtest_cache_cleared": cleared_caches["item_backtest_cache"],
                "volume_forecast_cache_cleared": cleared_caches["volume_forecast_cache"],
                "volume_backtest_cache_cleared": cleared_caches["volume_backtest_cache"],
            }
        }
        
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()


def remap_order_item_cluster(conn, order_item_id: str, new_menu_item_id: str, new_variant_id: str) -> Dict[str, Any]:
    """
    Remap an individual order item to a different menu_item cluster.

    The remap rides the menu-merge assignment stream (order_item_remap_v1):
    it records a merge_history row and emits a menu_merge.applied event whose
    assignments carry the new menu/variant, so peers and server ground truth
    receive the full mapping (not just a verified flag) and the echo of our
    own event clears pending_local.
    """
    blocked = _strict_mode_edit_blocked_response(conn)
    if blocked:
        return blocked

    ensure_assignment_sync_schema(conn)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT menu_item_id, variant_id, is_verified FROM menu_item_variants WHERE order_item_id = ?",
            (str(order_item_id),),
        )
        prev_row = cursor.fetchone()
        prev_menu_item_id = str(prev_row[0]) if prev_row else None
        prev_variant_id = prev_row[1] if prev_row else None
        prev_is_verified = int(prev_row[2] or 0) if prev_row else 0

        # 1. Update Mapping (SQLite UPSERT); provisional until the echo acks it
        cursor.execute("""
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified, pending_local, assignment_seq)
            VALUES (?, ?, ?, 1, 1, NULL)
            ON CONFLICT (order_item_id) DO UPDATE SET
                menu_item_id = excluded.menu_item_id,
                variant_id = excluded.variant_id,
                is_verified = 1,
                pending_local = 1,
                assignment_seq = NULL
        """, (order_item_id, new_menu_item_id, new_variant_id))

        # 1b. Relink the order_items / order_item_addons rows for this order item
        # locally, capturing their prior values first. The cloud echo would
        # eventually move these too, but doing it here keeps the three tables
        # consistent immediately AND lets undo restore them: undo replays
        # history["order_items"] / ["order_item_addons"], so without these rows
        # an undo would revert menu_item_variants while leaving order_items and
        # order_item_addons pointing at the target.
        # order_item_id here is the assignment key (POS itemid or generated
        # name hash), not a local PK: resolve it to local rows by each row's
        # OWN identity. Addon rows attached to a parent line are separate
        # products and must never be moved by parent membership.
        order_pks = local_order_item_pks_for_assignment_key(conn, str(order_item_id))
        addon_pks = local_addon_pks_for_assignment_key(conn, str(order_item_id))

        order_items_history = []
        if order_pks:
            placeholders = ", ".join("?" for _ in order_pks)
            cursor.execute(
                f"SELECT order_item_id, menu_item_id, variant_id FROM order_items "
                f"WHERE order_item_id IN ({placeholders})",
                order_pks,
            )
            order_items_history = [
                {
                    "order_item_id": row[0],
                    "old_menu_item_id": row[1],
                    "old_variant_id": row[2],
                    "new_menu_item_id": str(new_menu_item_id),
                    "new_variant_id": new_variant_id,
                }
                for row in cursor.fetchall()
            ]
        order_item_addons_history = []
        if addon_pks:
            placeholders = ", ".join("?" for _ in addon_pks)
            cursor.execute(
                f"SELECT order_item_addon_id, menu_item_id, variant_id FROM order_item_addons "
                f"WHERE order_item_addon_id IN ({placeholders})",
                addon_pks,
            )
            order_item_addons_history = [
                {
                    "order_item_addon_id": row[0],
                    "old_menu_item_id": row[1],
                    "old_variant_id": row[2],
                    "new_menu_item_id": str(new_menu_item_id),
                    "new_variant_id": new_variant_id,
                }
                for row in cursor.fetchall()
            ]

        update_local_order_rows_for_assignment_key(
            conn,
            str(order_item_id),
            menu_item_id=str(new_menu_item_id),
            variant_id=new_variant_id,
            variant_specified=True,
        )

        if prev_menu_item_id and prev_menu_item_id != str(new_menu_item_id):
            ensure_menu_item_has_variant_mapping(conn, prev_menu_item_id, cursor=cursor)
        ensure_menu_item_has_variant_mapping(conn, new_menu_item_id, cursor=cursor)

        # 2. Record the remap in merge_history and emit a merge-stream event.
        # A previously-unmapped order item has no prior state to restore, so
        # its old_* fields mirror the new values (undo becomes a no-op write).
        source_menu_item_id = prev_menu_item_id or str(new_menu_item_id)
        old_variant_for_history = prev_variant_id if prev_row else new_variant_id
        old_verified_for_history = prev_is_verified if prev_row else 1
        cursor.execute(
            "SELECT name, type FROM menu_items WHERE menu_item_id = ?",
            (source_menu_item_id,),
        )
        source_item_row = cursor.fetchone()
        source_name = str(source_item_row[0]) if source_item_row else source_menu_item_id
        source_type = str(source_item_row[1]) if source_item_row else "Unknown"

        history_payload = {
            "kind": "order_item_remap_v1",
            "order_item_id": str(order_item_id),
            "source_variant_id": _normalize_variant_key(old_variant_for_history),
            "target_variant_id": _normalize_variant_key(new_variant_id),
            "mapping_rows": [
                {
                    "order_item_id": str(order_item_id),
                    "old_menu_item_id": source_menu_item_id,
                    "old_variant_id": old_variant_for_history,
                    "old_is_verified": old_verified_for_history,
                    "new_menu_item_id": str(new_menu_item_id),
                    "new_variant_id": new_variant_id,
                    "new_is_verified": 1,
                }
            ],
            "order_items": order_items_history,
            "order_item_addons": order_item_addons_history,
        }
        merge_id = _insert_merge_history(
            cursor,
            source_menu_item_id,
            str(new_menu_item_id),
            source_name,
            source_type,
            history_payload,
        )

        from src.core.menu_merge_sync_events import (
            EVENT_TYPE_APPLIED,
            build_menu_merge_event_payload,
        )
        from src.core.menu_mapping_verification_sync_events import (
            build_menu_mapping_verification_event_payloads,
        )
        from src.core.menu_mutation_commit import (
            MUTATION_TYPE_ORDER_ITEM_REMAP,
            build_plan,
        )

        verify_emit_rows = [
            {
                "order_item_id": str(order_item_id),
                "menu_item_id": str(new_menu_item_id),
                "variant_id": new_variant_id,
                "is_verified": 1,
            }
        ]

        event = build_menu_merge_event_payload(conn, merge_id, EVENT_TYPE_APPLIED)
        if not event:
            conn.rollback()
            return {"status": "error", "message": "Failed to build remap event"}
        plan = build_plan(
            mutation_type=MUTATION_TYPE_ORDER_ITEM_REMAP,
            event=event,
            verification_events=build_menu_mapping_verification_event_payloads(conn, verify_emit_rows),
            catalog_delta=_catalog_delta_for_strict_commit(
                cursor,
                item_ids=[str(new_menu_item_id), source_menu_item_id],
                variant_ids=[new_variant_id] if new_variant_id else [],
            ),
            order_item_ids=[str(order_item_id)],
        )
        return _commit_strict_plan(
            conn,
            plan,
            success_message="Order item remapped successfully",
        )
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()


def update_menu_variant_mapping(
    conn,
    menu_item_id: str,
    current_variant_id: str,
    new_variant_id: str,
) -> Dict[str, Any]:
    """Move an existing menu-item/variant mapping to a different variant everywhere it is used."""
    blocked = _strict_mode_edit_blocked_response(conn)
    if blocked:
        return blocked

    menu_item_id = str(menu_item_id).strip()
    current_variant_id = str(current_variant_id).strip()
    new_variant_id = str(new_variant_id).strip()

    if not menu_item_id or not current_variant_id or not new_variant_id:
        return {"status": "error", "message": "Menu item, current variant, and new variant are required"}

    if current_variant_id == new_variant_id:
        return {"status": "error", "message": "Current and new variant cannot be the same"}

    cursor = conn.cursor()
    model_cleanup_error = None
    try:
        cursor.execute(
            "SELECT name FROM menu_items WHERE menu_item_id = ?",
            (menu_item_id,),
        )
        menu_item_row = cursor.fetchone()
        if not menu_item_row:
            return {"status": "error", "message": "Selected menu item was not found"}
        menu_item_name = menu_item_row[0]

        cursor.execute(
            "SELECT variant_name FROM variants WHERE variant_id = ?",
            (current_variant_id,),
        )
        current_variant_row = cursor.fetchone()
        if not current_variant_row:
            return {"status": "error", "message": "Current variant was not found"}
        current_variant_name = current_variant_row[0]

        cursor.execute(
            "SELECT variant_name FROM variants WHERE variant_id = ?",
            (new_variant_id,),
        )
        new_variant_row = cursor.fetchone()
        if not new_variant_row:
            return {"status": "error", "message": "New variant was not found"}
        new_variant_name = new_variant_row[0]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM menu_item_variants
            WHERE menu_item_id = ? AND variant_id = ?
            """,
            (menu_item_id, current_variant_id),
        )
        mapping_rows = int(cursor.fetchone()[0] or 0)

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM order_items
            WHERE menu_item_id = ? AND variant_id = ?
            """,
            (menu_item_id, current_variant_id),
        )
        order_item_rows = int(cursor.fetchone()[0] or 0)

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM order_item_addons
            WHERE menu_item_id = ? AND variant_id = ?
            """,
            (menu_item_id, current_variant_id),
        )
        addon_rows = int(cursor.fetchone()[0] or 0)

        if mapping_rows == 0 and order_item_rows == 0 and addon_rows == 0:
            return {
                "status": "error",
                "message": "No existing rows were found for the selected menu item and current variant",
            }

        cursor.execute(
            """
            UPDATE menu_item_variants
            SET variant_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = ? AND variant_id = ?
            """,
            (new_variant_id, menu_item_id, current_variant_id),
        )
        updated_mapping_rows = cursor.rowcount

        cursor.execute(
            """
            UPDATE order_items
            SET variant_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = ? AND variant_id = ?
            """,
            (new_variant_id, menu_item_id, current_variant_id),
        )
        updated_order_item_rows = cursor.rowcount

        cursor.execute(
            """
            UPDATE order_item_addons
            SET variant_id = ?
            WHERE menu_item_id = ? AND variant_id = ?
            """,
            (new_variant_id, menu_item_id, current_variant_id),
        )
        updated_addon_rows = cursor.rowcount

        cleared_caches = _clear_volume_forecast_cache(cursor)

        conn.commit()

        message = (
            f"Updated '{menu_item_name}' from variant '{current_variant_name}' "
            f"to '{new_variant_name}' across mappings and historical rows"
        )
        if model_cleanup_error:
            message = f"{message}. Volume models could not be cleared automatically: {model_cleanup_error}"

        return {
            "status": "success",
            "message": message,
            "stats": {
                "menu_item_variants_updated": updated_mapping_rows,
                "order_items_updated": updated_order_item_rows,
                "order_item_addons_updated": updated_addon_rows,
                "volume_forecast_cache_cleared": cleared_caches["volume_forecast_cache"],
                "volume_backtest_cache_cleared": cleared_caches["volume_backtest_cache"],
            },
        }
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()


def resolve_menu_item_variant(
    conn,
    source_menu_item_id: str,
    source_variant_id: str,
    target_menu_item_id: str = None,
    new_name: str = None,
    new_type: str = None,
    target_variant_id: str = None,
    new_variant_name: str = None,
    emit_sync_event: bool = True,
) -> Dict[str, Any]:
    """Resolve a single unresolved menu item + variant pair."""
    source_menu_item_id = str(source_menu_item_id or "").strip()
    source_variant_key = _normalize_variant_key(source_variant_id)
    target_menu_item_id = str(target_menu_item_id or "").strip() or None
    normalized_new_name = (new_name or "").strip()
    normalized_new_type = (new_type or "").strip()
    normalized_new_variant_name = (new_variant_name or "").strip()
    target_variant_id = str(target_variant_id or "").strip() or None

    if not source_menu_item_id or not source_variant_key:
        return {"status": "error", "message": "Source menu item and source variant are required"}

    blocked = _strict_mode_edit_blocked_response(conn, emit_sync_event=emit_sync_event)
    if blocked:
        return blocked

    if not target_menu_item_id and (not normalized_new_name or not normalized_new_type):
        return {"status": "error", "message": "Choose an existing target item or provide a new name and type"}

    if not target_variant_id and not normalized_new_variant_name:
        return {"status": "error", "message": "Choose an existing target variant or provide a new variant name"}

    ensure_assignment_sync_schema(conn)
    cursor = conn.cursor()
    try:
        source_item = _fetch_menu_item_record(cursor, source_menu_item_id)
        if not source_item:
            return {"status": "error", "message": "Source item was not found"}

        source_variant_summary = _fetch_menu_item_variant_summary(conn, source_menu_item_id, source_variant_key)
        if not source_variant_summary:
            return {"status": "error", "message": "Source variant was not found"}

        source_variant = source_variant_summary[0]
        full_source_variant_summary = _fetch_menu_item_variant_summary(conn, source_menu_item_id)
        source_variant_db_id = _decode_variant_key(source_variant_key)

        if target_menu_item_id:
            resolved_target_id = target_menu_item_id
            cursor.execute("""
                SELECT menu_item_id, name, type, is_verified
                FROM menu_items
                WHERE menu_item_id = ?
            """, (resolved_target_id,))
            target_item = cursor.fetchone()
            if not target_item:
                return {"status": "error", "message": "Selected target item was not found"}
            target_name = target_item[1]
            target_type = target_item[2]
        else:
            resolved_target_id = generate_deterministic_id(normalized_new_name, normalized_new_type)
            cursor.execute("""
                SELECT menu_item_id, name, type, is_verified
                FROM menu_items
                WHERE menu_item_id = ?
            """, (resolved_target_id,))
            target_item = cursor.fetchone()
            if not target_item:
                cursor.execute("""
                    INSERT INTO menu_items (menu_item_id, name, type, is_verified)
                    VALUES (?, ?, ?, 1)
                """, (resolved_target_id, normalized_new_name, normalized_new_type))
                ensure_menu_item_has_variant_mapping(conn, resolved_target_id, cursor=cursor)
                target_name = normalized_new_name
                target_type = normalized_new_type
            else:
                target_name = target_item[1]
                target_type = target_item[2]

        if normalized_new_variant_name:
            resolved_target_variant_id = _ensure_variant(conn, normalized_new_variant_name)
            target_variant_name = normalized_new_variant_name
        else:
            cursor.execute(
                "SELECT variant_name FROM variants WHERE variant_id = ?",
                (target_variant_id,),
            )
            target_variant_row = cursor.fetchone()
            if not target_variant_row:
                return {"status": "error", "message": "Selected target variant was not found"}
            resolved_target_variant_id = target_variant_id
            target_variant_name = target_variant_row[0]
            cursor.execute("""
                UPDATE variants
                SET is_verified = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE variant_id = ?
            """, (resolved_target_variant_id,))

        variant_clause, variant_params = _build_variant_match_clause("variant_id", source_variant_key)

        cursor.execute(
            f"""
            SELECT order_item_id, menu_item_id, variant_id, is_verified
            FROM menu_item_variants
            WHERE menu_item_id = ? AND {variant_clause}
            """,
            [source_menu_item_id] + variant_params,
        )
        mapping_rows = [
            {
                "order_item_id": row[0],
                "old_menu_item_id": row[1],
                "old_variant_id": row[2],
                "old_is_verified": int(row[3] or 0),
                "new_menu_item_id": resolved_target_id,
                "new_variant_id": resolved_target_variant_id,
                "new_is_verified": 1,
            }
            for row in cursor.fetchall()
        ]

        if not mapping_rows:
            return {"status": "error", "message": "No mapping rows were found for this source variant"}

        cursor.execute(
            f"""
            SELECT order_item_id, menu_item_id, variant_id
            FROM order_items
            WHERE menu_item_id = ? AND {variant_clause}
            """,
            [source_menu_item_id] + variant_params,
        )
        order_item_rows = [
            {
                "order_item_id": row[0],
                "old_menu_item_id": row[1],
                "old_variant_id": row[2],
                "new_menu_item_id": resolved_target_id,
                "new_variant_id": resolved_target_variant_id,
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            f"""
            SELECT order_item_addon_id, menu_item_id, variant_id
            FROM order_item_addons
            WHERE menu_item_id = ? AND {variant_clause}
            """,
            [source_menu_item_id] + variant_params,
        )
        addon_rows = [
            {
                "order_item_addon_id": row[0],
                "old_menu_item_id": row[1],
                "old_variant_id": row[2],
                "new_menu_item_id": resolved_target_id,
                "new_variant_id": resolved_target_variant_id,
            }
            for row in cursor.fetchall()
        ]

        source_will_be_removed = (
            resolved_target_id != source_menu_item_id and
            len(full_source_variant_summary) == 1 and
            full_source_variant_summary[0]["variant_id"] == source_variant_key
        )
        suggestion_rows = (
            _retarget_menu_item_suggestions(cursor, source_menu_item_id, resolved_target_id)
            if source_will_be_removed
            else []
        )
        # Record every resolution, including verify-in-place (same item + variant),
        # so Resolution History is a complete, undoable audit trail.
        history_payload = {
            "kind": "resolution_variant_v1",
            "source_variant_id": source_variant_key,
            "target_variant_id": resolved_target_variant_id,
            "source_variant_name": source_variant["variant_name"],
            "target_variant_name": target_variant_name,
            "suggestion_refs": suggestion_rows,
            "mapping_rows": mapping_rows,
            "order_items": order_item_rows,
            "order_item_addons": addon_rows,
        }
        merge_id = _insert_merge_history(
            cursor,
            source_menu_item_id,
            resolved_target_id,
            source_item[1],
            source_item[2],
            history_payload,
        )

        if resolved_target_id == source_menu_item_id and normalized_new_name and normalized_new_type:
            cursor.execute("""
                UPDATE menu_items
                SET name = ?,
                    type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE menu_item_id = ?
            """, (normalized_new_name, normalized_new_type, source_menu_item_id))

        if resolved_target_id == source_menu_item_id and resolved_target_variant_id == source_variant_db_id:
            cursor.execute(
                f"""
                UPDATE menu_item_variants
                SET is_verified = 1,
                    pending_local = 1,
                    assignment_seq = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE menu_item_id = ? AND {variant_clause}
                """,
                [source_menu_item_id] + variant_params,
            )
        else:
            cursor.execute(
                f"""
                UPDATE menu_item_variants
                SET menu_item_id = ?,
                    variant_id = ?,
                    is_verified = 1,
                    pending_local = 1,
                    assignment_seq = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE menu_item_id = ? AND {variant_clause}
                """,
                [resolved_target_id, resolved_target_variant_id, source_menu_item_id] + variant_params,
            )

            cursor.execute(
                f"""
                UPDATE order_items
                SET menu_item_id = ?,
                    variant_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE menu_item_id = ? AND {variant_clause}
                """,
                [resolved_target_id, resolved_target_variant_id, source_menu_item_id] + variant_params,
            )

            cursor.execute(
                f"""
                UPDATE order_item_addons
                SET menu_item_id = ?,
                    variant_id = ?
                WHERE menu_item_id = ? AND {variant_clause}
                """,
                [resolved_target_id, resolved_target_variant_id, source_menu_item_id] + variant_params,
            )

        cursor.execute("""
            UPDATE variants
            SET is_verified = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE variant_id = ?
        """, (resolved_target_variant_id,))
        cursor.execute("""
            UPDATE menu_items
            SET is_verified = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = ?
        """, (resolved_target_id,))

        for menu_item_id in {source_menu_item_id, resolved_target_id}:
            _recalculate_menu_item_stats(cursor, menu_item_id)
        for menu_item_id in {source_menu_item_id, resolved_target_id}:
            _sync_menu_item_resolution_state(cursor, menu_item_id)

        cleared_item_caches = {
            "item_forecast_cache": 0,
            "item_backtest_cache": 0,
            "volume_forecast_cache": 0,
            "volume_backtest_cache": 0,
        }
        cleared_volume_caches = {
            "volume_forecast_cache": 0,
            "volume_backtest_cache": 0,
        }
        clear_item_models = False
        clear_volume_models = False
        if resolved_target_id != source_menu_item_id:
            cleared_item_caches = _clear_item_and_volume_forecast_cache(cursor, [source_menu_item_id, resolved_target_id])
            clear_item_models = True
            clear_volume_models = True
        elif resolved_target_variant_id != source_variant_db_id:
            cleared_volume_caches = _clear_volume_forecast_cache(cursor)
            clear_volume_models = True

        if emit_sync_event and merge_id is not None:
            from src.core.menu_mutation_commit import (
                MUTATION_TYPE_RESOLUTION_VARIANT,
                build_plan,
            )
            from src.core.menu_merge_sync_events import (
                EVENT_TYPE_APPLIED,
                build_menu_merge_event_payload,
            )
            from src.core.menu_mapping_verification_sync_events import (
                build_menu_mapping_verification_event_payloads,
            )

            verify_emit_rows = [
                {
                    "order_item_id": row["order_item_id"],
                    "menu_item_id": row["new_menu_item_id"],
                    "variant_id": row["new_variant_id"],
                    "is_verified": row.get("new_is_verified", 1),
                }
                for row in mapping_rows
            ]

            event = build_menu_merge_event_payload(conn, merge_id, EVENT_TYPE_APPLIED)
            if not event:
                conn.rollback()
                return {"status": "error", "message": "Failed to build resolution event"}

            catalog_delta = _catalog_delta_for_strict_commit(
                cursor,
                item_ids=[resolved_target_id, source_menu_item_id],
                variant_ids=[
                    resolved_target_variant_id,
                    source_variant_db_id,
                    source_variant_key,
                ],
            )

            plan = build_plan(
                mutation_type=MUTATION_TYPE_RESOLUTION_VARIANT,
                event=event,
                verification_events=build_menu_mapping_verification_event_payloads(conn, verify_emit_rows),
                catalog_delta=catalog_delta,
                order_item_ids=[str(row["order_item_id"]) for row in mapping_rows],
            )
            return _commit_strict_plan(
                conn,
                plan,
                success_message=(
                    f"Verified '{source_item[1]}' ({source_variant['variant_name']}) as a resolved menu item + variant pair"
                    if resolved_target_id == source_menu_item_id and resolved_target_variant_id == source_variant_db_id
                    else (
                        f"Resolved '{source_item[1]}' ({source_variant['variant_name']}) "
                        f"into '{target_name}' ({target_variant_name})"
                    )
                ),
                success_extra={
                    "stats": {
                        "mapping_rows_updated": len(mapping_rows),
                        "order_items_updated": len(order_item_rows),
                        "order_item_addons_updated": len(addon_rows),
                        "suggestion_refs_updated": len(suggestion_rows),
                        "item_forecast_cache_cleared": cleared_item_caches["item_forecast_cache"],
                        "item_backtest_cache_cleared": cleared_item_caches["item_backtest_cache"],
                        "volume_forecast_cache_cleared": (
                            cleared_item_caches["volume_forecast_cache"] + cleared_volume_caches["volume_forecast_cache"]
                        ),
                        "volume_backtest_cache_cleared": (
                            cleared_item_caches["volume_backtest_cache"] + cleared_volume_caches["volume_backtest_cache"]
                        ),
                    }
                },
                clear_item_models=clear_item_models,
                clear_volume_models=clear_volume_models,
            )

        # Only the remote-replay applier reaches here (emit_sync_event=False);
        # human edits always return through _commit_strict_plan above. Replay
        # never re-emits verification events — the verification stream already
        # carries the authoritative flags.
        rebuild_itemcode_mappings_best_effort(conn, cursor=cursor)
        conn.commit()
        model_cleanup_error = _clear_impacted_models(
            clear_item_models=clear_item_models,
            clear_volume_models=clear_volume_models,
        )

        if resolved_target_id == source_menu_item_id and resolved_target_variant_id == source_variant_db_id:
            message = f"Verified '{source_item[1]}' ({source_variant['variant_name']}) as a resolved menu item + variant pair"
        else:
            message = (
                f"Resolved '{source_item[1]}' ({source_variant['variant_name']}) "
                f"into '{target_name}' ({target_variant_name})"
            )
        if model_cleanup_error:
            message = f"{message}. Cleared affected caches but could not delete all local models: {model_cleanup_error}"

        return {
            "status": "success",
            "message": message,
            "merge_id": merge_id,
            "stats": {
                "mapping_rows_updated": len(mapping_rows),
                "order_items_updated": len(order_item_rows),
                "order_item_addons_updated": len(addon_rows),
                "suggestion_refs_updated": len(suggestion_rows),
                "item_forecast_cache_cleared": cleared_item_caches["item_forecast_cache"],
                "item_backtest_cache_cleared": cleared_item_caches["item_backtest_cache"],
                "volume_forecast_cache_cleared": (
                    cleared_item_caches["volume_forecast_cache"] + cleared_volume_caches["volume_forecast_cache"]
                ),
                "volume_backtest_cache_cleared": (
                    cleared_item_caches["volume_backtest_cache"] + cleared_volume_caches["volume_backtest_cache"]
                ),
            },
        }
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Variant resolution failed: {e}"}
    finally:
        cursor.close()


def resolve_item_rename(conn, source_id: str, new_name: str, new_type: str, emit_sync_event: bool = True) -> Dict[str, Any]:
    """
    Handle resolution where an item is renamed.
    This effectively means:
    1. Generate ID for new name/type
    2. Check if that target item exists
    3. If not, create it (verified)
    4. Merge source into target

    Target creation stays in the same transaction as the merge so a failed
    strict-mode commit cannot leave an empty orphan catalog item behind.
    """
    cursor = conn.cursor()
    created_target = False
    try:
        target_id = generate_deterministic_id(new_name, new_type)

        # Check existence
        cursor.execute("SELECT menu_item_id FROM menu_items WHERE menu_item_id = ?", (target_id,))
        exists = cursor.fetchone()

        if not exists:
            # Create Target (uncommitted until merge succeeds)
            cursor.execute("""
                INSERT INTO menu_items (menu_item_id, name, type, is_verified)
                VALUES (?, ?, ?, 1)
            """, (target_id, new_name, new_type))
            ensure_menu_item_has_variant_mapping(conn, target_id, cursor=cursor)
            created_target = True

        # Merge Source -> Target
        # Note: This handles the full merge logic including deletions
        result = merge_menu_items(conn, source_id, target_id, emit_sync_event=emit_sync_event)
        if created_target and result.get("status") != "success":
            conn.rollback()
        return result

    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Resolution failed: {e}"}
    finally:
        cursor.close()


def retype_menu_item(conn, menu_item_id: str, new_type: str, emit_sync_event: bool = True) -> Dict[str, Any]:
    """
    Change a menu item's type while keeping its name.

    Because menu_item_id is deterministic over (name, type), a type change is
    a new catalog identity: the item is merged into the (name, new_type) item
    — created verified if it does not exist yet — so every variant mapping,
    order item, addon row, forecast cache, and sync event follows the same
    path as a normal merge (undoable via merge history, strict-mode commit
    when active). If an item with the same name and the target type already
    exists, their histories are consolidated.

    Exception: if the item's current ID already equals hash(name, new_type)
    — the type label drifted in place during an earlier edit — the label is
    flipped back in place with no relink or merge.
    """
    menu_item_id = str(menu_item_id or "").strip()
    normalized_new_type = (new_type or "").strip()
    if not menu_item_id or not normalized_new_type:
        return {"status": "error", "message": "Menu item and new type are required"}

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT name, type FROM menu_items WHERE menu_item_id = ?",
            (menu_item_id,),
        )
        item = cursor.fetchone()
    finally:
        cursor.close()

    if not item:
        return {"status": "error", "message": "Menu item was not found"}

    item_name = item[0]
    current_type = item[1]
    if normalized_new_type == (current_type or "").strip():
        return {"status": "error", "message": "New type matches the current type. Choose a different target type."}

    success_message = f"Changed type of '{item_name}' from '{current_type}' to '{normalized_new_type}'"

    if generate_deterministic_id(item_name, normalized_new_type) == menu_item_id:
        # The item's ID already encodes (name, new_type): an earlier edit
        # changed the type label in place without reissuing the ID. Flip the
        # label back in place — every mapping already points at this ID, so
        # there is nothing to relink and no merge to record.
        blocked = _strict_mode_edit_blocked_response(conn, emit_sync_event=emit_sync_event)
        if blocked:
            return blocked
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE menu_items
                SET type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE menu_item_id = ?
                """,
                (normalized_new_type, menu_item_id),
            )
            if emit_sync_event:
                from src.core.menu_mutation_commit import (
                    MUTATION_TYPE_CATALOG_UPDATE,
                    build_plan,
                )

                plan = build_plan(
                    mutation_type=MUTATION_TYPE_CATALOG_UPDATE,
                    catalog_delta=_catalog_delta_for_strict_commit(
                        cursor,
                        item_ids=[menu_item_id],
                    ),
                )
                return _commit_strict_plan(
                    conn,
                    plan,
                    success_message=success_message,
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            return {"status": "error", "message": f"Type update failed: {e}"}
        finally:
            cursor.close()
        return {"status": "success", "message": success_message}

    result = resolve_item_rename(
        conn,
        menu_item_id,
        item_name,
        normalized_new_type,
        emit_sync_event=emit_sync_event,
    )
    if result.get("status") == "success":
        message = success_message
        # Keep the model-cleanup warning the merge path may have appended.
        prior_message = str(result.get("message") or "")
        cleanup_marker = ". Cleared affected caches but could not delete all local models:"
        marker_index = prior_message.find(cleanup_marker)
        if marker_index != -1:
            message += prior_message[marker_index:]
        result["message"] = message
    return result

def undo_merge(conn, merge_id: int, emit_sync_event: bool = True) -> Dict[str, Any]:
    """
    Reverse a merge operation.
    1. Re-insert source menu item
    2. Point affected mappings back to source item
    3. Point affected order_items/addons back to source item
    4. Recalculate stats for both items
    5. Delete history record
    """
    blocked = _strict_mode_edit_blocked_response(conn, emit_sync_event=emit_sync_event)
    if blocked:
        return blocked

    ensure_assignment_sync_schema(conn)
    cursor = conn.cursor()
    try:
        # 1. Get History Entry
        cursor.execute("SELECT * FROM merge_history WHERE merge_id = ?", (merge_id,))
        history = cursor.fetchone()
        if not history:
            return {"status": "error", "message": "Merge history record not found"}
        
        # Convert Row to dict for easier access
        history_dict = dict(history)
        source_id = history_dict['source_id']
        target_id = history_dict['target_id']
        affected_ids_json = history_dict['affected_order_items']
        
        # Parse legacy list payload or richer variant-aware payload.
        history_payload = json.loads(affected_ids_json) if isinstance(affected_ids_json, str) else affected_ids_json

        history_kind = history_payload.get("kind") if isinstance(history_payload, dict) else None

        if history_kind == "mapping_audit_v1":
            return {
                "status": "error",
                "message": "This history entry records a mapping cleanup only; it cannot be undone from the UI.",
            }

        # 2. Re-insert Source Item (SQLite UPSERT with INSERT OR IGNORE)
        cursor.execute("""
            INSERT OR IGNORE INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES (?, ?, ?, 1)
        """, (source_id, history_dict['source_name'], history_dict['source_type']))
        if isinstance(history_payload, dict):
            _restore_menu_item_suggestions(cursor, history_payload.get("suggestion_refs", []))

        if history_kind == "variant_merge_v1":
            for mapping_row in history_payload.get("mapping_rows", []):
                cursor.execute("""
                    UPDATE menu_item_variants
                    SET menu_item_id = ?, variant_id = ?, pending_local = 1, assignment_seq = NULL
                    WHERE order_item_id = ?
                """, (source_id, mapping_row["old_variant_id"], mapping_row["order_item_id"]))

            for order_row in history_payload.get("order_items", []):
                cursor.execute("""
                    UPDATE order_items
                    SET menu_item_id = ?, variant_id = ?
                    WHERE order_item_id = ?
                """, (source_id, order_row["old_variant_id"], order_row["order_item_id"]))

            for addon_row in history_payload.get("order_item_addons", []):
                cursor.execute("""
                    UPDATE order_item_addons
                    SET menu_item_id = ?, variant_id = ?
                    WHERE order_item_addon_id = ?
                """, (source_id, addon_row["old_variant_id"], addon_row["order_item_addon_id"]))
        elif history_kind in ("resolution_variant_v1", "order_item_remap_v1"):
            for mapping_row in history_payload.get("mapping_rows", []):
                cursor.execute("""
                    UPDATE menu_item_variants
                    SET menu_item_id = ?, variant_id = ?, is_verified = ?, pending_local = 1, assignment_seq = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE order_item_id = ?
                """, (
                    mapping_row["old_menu_item_id"],
                    mapping_row["old_variant_id"],
                    mapping_row.get("old_is_verified", 0),
                    mapping_row["order_item_id"],
                ))

            for order_row in history_payload.get("order_items", []):
                cursor.execute("""
                    UPDATE order_items
                    SET menu_item_id = ?, variant_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE order_item_id = ?
                """, (
                    order_row["old_menu_item_id"],
                    order_row["old_variant_id"],
                    order_row["order_item_id"],
                ))

            for addon_row in history_payload.get("order_item_addons", []):
                cursor.execute("""
                    UPDATE order_item_addons
                    SET menu_item_id = ?, variant_id = ?
                    WHERE order_item_addon_id = ?
                """, (
                    addon_row["old_menu_item_id"],
                    addon_row["old_variant_id"],
                    addon_row["order_item_addon_id"],
                ))
        else:
            affected_ids = history_payload
            if history_kind == "basic_merge_v1" and isinstance(history_payload, dict):
                affected_ids = history_payload.get("affected_order_item_ids", [])
            # 3. Relink Mappings (menu_item_variants)
            if affected_ids:
                in_clause, params = _build_in_clause(affected_ids)
                cursor.execute(f"""
                    UPDATE menu_item_variants
                    SET menu_item_id = ?, pending_local = 1, assignment_seq = NULL
                    WHERE order_item_id {in_clause}
                """, [source_id] + params)
                
                # 4/5. Relink local order rows. affected_ids are assignment
                # keys (POS itemid or generated name hash), not local PKs:
                # resolve each to order_items / order_item_addons rows by the
                # row's OWN identity — addon rows are separate products and
                # must never be moved via parent-line membership.
                key_index = AssignmentKeyIndex(conn)
                for affected_key in affected_ids:
                    update_local_order_rows_for_assignment_key(
                        conn,
                        affected_key,
                        menu_item_id=source_id,
                        key_index=key_index,
                    )
        
        # 6. Recalculate Stats (Filtered by Success status)
        for mid in [target_id, source_id]:
            _recalculate_menu_item_stats(cursor, mid)

        if history_kind in ("resolution_variant_v1", "order_item_remap_v1"):
            for mid in [target_id, source_id]:
                _sync_menu_item_resolution_state(cursor, mid)

        cleared_item_caches = {
            "item_forecast_cache": 0,
            "item_backtest_cache": 0,
            "volume_forecast_cache": 0,
            "volume_backtest_cache": 0,
        }
        cleared_volume_caches = {
            "volume_forecast_cache": 0,
            "volume_backtest_cache": 0,
        }
        clear_item_models = False
        clear_volume_models = False
        if history_kind in ("resolution_variant_v1", "order_item_remap_v1") and source_id == target_id:
            cleared_volume_caches = _clear_volume_forecast_cache(cursor)
            clear_volume_models = True
        else:
            cleared_item_caches = _clear_item_and_volume_forecast_cache(cursor, [source_id, target_id])
            clear_item_models = True
            clear_volume_models = True

        if emit_sync_event:
            from src.core.menu_mutation_commit import (
                MUTATION_TYPE_MENU_MERGE_UNDONE,
                build_plan,
            )
            from src.core.menu_merge_sync import lookup_applied_remote_event_id
            from src.core.menu_merge_sync_events import (
                EVENT_TYPE_UNDONE,
                build_menu_merge_event_payload,
            )
            from src.core.menu_mapping_verification_sync_events import (
                build_menu_mapping_verification_event_payloads,
            )

            undo_verify_rows = []
            if history_kind in ("resolution_variant_v1", "order_item_remap_v1"):
                for mapping_row in (history_payload.get("mapping_rows", []) if isinstance(history_payload, dict) else []):
                    if not isinstance(mapping_row, dict):
                        continue
                    oid = str(mapping_row.get("order_item_id") or "").strip()
                    old_menu_item_id = str(mapping_row.get("old_menu_item_id") or source_id or "").strip()
                    if not oid or not old_menu_item_id:
                        continue
                    undo_verify_rows.append(
                        {
                            "order_item_id": oid,
                            "menu_item_id": old_menu_item_id,
                            "variant_id": mapping_row.get("old_variant_id"),
                            "is_verified": int(mapping_row.get("old_is_verified", 0) or 0),
                        }
                    )

            applied_remote_id = lookup_applied_remote_event_id(conn, merge_id)
            event = build_menu_merge_event_payload(
                conn,
                merge_id,
                EVENT_TYPE_UNDONE,
                reverts_remote_event_id=applied_remote_id,
                occurred_at=datetime.now(timezone.utc).isoformat(),
            )
            if not event:
                conn.rollback()
                return {"status": "error", "message": "Failed to build undo event"}
            if applied_remote_id:
                event["reverts_remote_event_id"] = applied_remote_id
            order_item_ids: List[str] = []
            if history_kind in ("resolution_variant_v1", "order_item_remap_v1"):
                order_item_ids = [row["order_item_id"] for row in undo_verify_rows]
            elif history_kind == "variant_merge_v1":
                order_item_ids = [
                    str(row.get("order_item_id"))
                    for row in history_payload.get("mapping_rows", [])
                    if isinstance(row, dict) and row.get("order_item_id")
                ]
            elif isinstance(history_payload, dict):
                order_item_ids = [str(oid) for oid in history_payload.get("affected_order_item_ids", [])]
            elif isinstance(history_payload, list):
                order_item_ids = [str(oid) for oid in history_payload]
            plan = build_plan(
                mutation_type=MUTATION_TYPE_MENU_MERGE_UNDONE,
                event=event,
                verification_events=build_menu_mapping_verification_event_payloads(conn, undo_verify_rows),
                order_item_ids=order_item_ids,
            )
            return _commit_strict_plan(
                conn,
                plan,
                success_message=f"Undid merge of '{history_dict['source_name']}' into target",
                clear_item_models=clear_item_models,
                clear_volume_models=clear_volume_models,
            )

        ensure_menu_item_has_variant_mapping(conn, source_id, cursor=cursor)
        ensure_menu_item_has_variant_mapping(conn, target_id, cursor=cursor)

        # 7. Delete History
        cursor.execute("DELETE FROM merge_history WHERE merge_id = ?", (merge_id,))

        rebuild_itemcode_mappings_best_effort(conn, cursor=cursor)
        conn.commit()
        model_cleanup_error = _clear_impacted_models(
            clear_item_models=clear_item_models,
            clear_volume_models=clear_volume_models,
        )

        message = f"Successfully reversed merge of '{history_dict['source_name']}'"
        if model_cleanup_error:
            message = f"{message}. Cleared affected caches but could not delete all local models: {model_cleanup_error}"
        
        return {
            "status": "success",
            "message": message,
            "stats": {
                "item_forecast_cache_cleared": cleared_item_caches["item_forecast_cache"],
                "item_backtest_cache_cleared": cleared_item_caches["item_backtest_cache"],
                "volume_forecast_cache_cleared": (
                    cleared_item_caches["volume_forecast_cache"] + cleared_volume_caches["volume_forecast_cache"]
                ),
                "volume_backtest_cache_cleared": (
                    cleared_item_caches["volume_backtest_cache"] + cleared_volume_caches["volume_backtest_cache"]
                ),
            },
        }
        
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Undo failed: {e}"}
    finally:
        cursor.close()

def verify_item(
    conn,
    item_id: str,
    new_name: str = None,
    new_type: str = None,
    new_variant_id: str = None,
    emit_mapping_verification_events: bool = True,
) -> Dict[str, Any]:
    """Mark an item as verified, optionally updating name/type"""
    blocked = _strict_mode_edit_blocked_response(
        conn,
        emit_sync_event=emit_mapping_verification_events,
    )
    if blocked:
        return blocked

    cursor = conn.cursor()
    try:
        if new_variant_id:
            cursor.execute("SELECT variant_id FROM variants WHERE variant_id = ?", (new_variant_id,))
            variant_exists = cursor.fetchone()
            if not variant_exists:
                return {"status": "error", "message": "Selected variant was not found"}

            source_variants = _fetch_menu_item_variant_summary(conn, item_id)
            if len(source_variants) > 1:
                return {
                    "status": "error",
                    "message": "This item has multiple source variants. Use Search & Merge to map each variant explicitly.",
                }
        else:
            source_variants = []

        if new_name and new_type:
            # Check if this new name+type triggers a collision
            new_id = generate_deterministic_id(new_name, new_type)
            
            # If ID changes, we need to handle merge/move logic
            # For simplicity, if ID matches existing, we merge. If not, we rename.
            
            cursor.execute("SELECT menu_item_id FROM menu_items WHERE menu_item_id = ?", (new_id,))
            exists = cursor.fetchone()
            
            if exists and exists[0] != item_id:
                if new_variant_id:
                    if not source_variants:
                        return merge_menu_items(conn, item_id, new_id)
                    return merge_menu_items_with_variant_mappings(
                        conn,
                        item_id,
                        new_id,
                        [{
                            "source_variant_id": source_variants[0]["variant_id"],
                            "target_variant_id": new_variant_id,
                        }],
                    )
                return merge_menu_items(conn, item_id, new_id)
            else:
                 # Just rename and verify
                cursor.execute("""
                    UPDATE menu_items 
                    SET name = ?, type = ?, is_verified = 1
                    WHERE menu_item_id = ?
                """, (new_name, new_type, item_id))
        else:
            cursor.execute("UPDATE menu_items SET is_verified = 1 WHERE menu_item_id = ?", (item_id,))

        if new_variant_id and emit_mapping_verification_events:
            if not source_variants:
                conn.rollback()
                return {
                    "status": "error",
                    "message": "No source variant mapping was found for this item.",
                }
            return resolve_menu_item_variant(
                conn,
                item_id,
                source_variants[0]["variant_id"],
                target_menu_item_id=item_id,
                new_name=new_name,
                new_type=new_type,
                target_variant_id=new_variant_id,
                emit_sync_event=True,
            )

        if new_variant_id:
            _reassign_menu_item_variant(cursor, item_id, new_variant_id)

        if emit_mapping_verification_events:
            from src.core.menu_mutation_commit import (
                MUTATION_TYPE_VERIFY,
                build_plan,
            )
            from src.core.menu_mapping_verification_sync_events import (
                build_menu_mapping_verification_event_payloads,
            )

            cursor.execute(
                """
                SELECT order_item_id, menu_item_id, variant_id, is_verified
                FROM menu_item_variants
                WHERE menu_item_id = ?
                """,
                (item_id,),
            )
            mapping_rows = cursor.fetchall()

            # Emit is_verified=1 for every mapping under this item: the server
            # materializes the events and the accepted response applies the
            # same rows locally, so local and server stay in lockstep. Never
            # emit is_verified=0 from verify — that would reopen mappings
            # verified on another install.
            verify_emit_rows = [
                {
                    "order_item_id": str(r[0]),
                    "menu_item_id": str(r[1]),
                    "variant_id": r[2],
                    "is_verified": 1,
                }
                for r in mapping_rows
            ]
            if not verify_emit_rows:
                from src.core.menu_mutation_commit import (
                    MUTATION_TYPE_CATALOG_UPDATE,
                    build_plan,
                )

                plan = build_plan(
                    mutation_type=MUTATION_TYPE_CATALOG_UPDATE,
                    catalog_delta=_catalog_delta_for_strict_commit(
                        cursor,
                        item_ids=[item_id],
                    ),
                )
                return _commit_strict_plan(
                    conn,
                    plan,
                    success_message="Item verified successfully",
                )
            verify_variant_ids = [new_variant_id] if new_variant_id else []
            if source_variants:
                verify_variant_ids.extend(
                    str(variant["variant_id"])
                    for variant in source_variants
                    if variant.get("variant_id")
                )
            plan = build_plan(
                mutation_type=MUTATION_TYPE_VERIFY,
                verification_events=build_menu_mapping_verification_event_payloads(conn, verify_emit_rows),
                catalog_delta=_catalog_delta_for_strict_commit(
                    cursor,
                    item_ids=[item_id],
                    variant_ids=verify_variant_ids,
                ),
                order_item_ids=[row["order_item_id"] for row in verify_emit_rows],
            )
            return _commit_strict_plan(
                conn,
                plan,
                success_message="Item verified successfully",
            )

        conn.commit()
        return {"status": "success", "message": "Item verified successfully"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Verification failed: {e}"}
    finally:
        cursor.close()


def verify_menu_mapping_assignments(
    conn,
    assignment_order_item_ids: List[str],
    *,
    expected_global_menu_item_id: str,
    expected_global_variant_id: str,
    mutation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Centrally verify exact assignment keys without changing their identities.

    This is the shadow/global-resolution counterpart to identity repair.  It
    deliberately bypasses the global-canonical-write blocker because §25 keeps
    restaurant assignment verification available in shadow mode, but it still
    requires the ordinary strict menu commit path and never writes the local
    verification flag before central acknowledgement.
    """
    from src.core.menu_mutation_commit import (
        LOCAL_APPLY_FAILED_MESSAGE,
        MUTATION_TYPE_VERIFY,
        build_plan,
        strict_mode_edit_blocked_response,
    )
    from src.core.menu_mapping_verification_sync_events import (
        build_menu_mapping_verification_event_payloads,
    )

    blocked = strict_mode_edit_blocked_response(conn)
    if blocked:
        # Global shadow sync intentionally skips the legacy merge/verification
        # tail, but its additive assignment snapshot still advertises the §19
        # restaurant menu revision. Older local profiles may not have mirrored
        # that token yet. Refresh the ordinary global projection/snapshot once
        # before refusing the centrally-authorized verification operation.
        try:
            from src.core.global_menu_schema import resolve_global_menu_capability
            from src.core.global_menu_sync import (
                pull_global_assignment_snapshot,
                pull_global_menu_state,
            )
            from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK

            capability = resolve_global_menu_capability(conn)
            if capability.resolution_ready:
                with CLOUD_PULL_LOCK:
                    global_result = pull_global_menu_state(conn)
                    assignment_result = (
                        pull_global_assignment_snapshot(conn)
                        if global_result.get("status") == "applied"
                        else {"status": "error"}
                    )
                if (
                    global_result.get("status") == "applied"
                    and assignment_result.get("status") == "applied"
                ):
                    blocked = strict_mode_edit_blocked_response(conn)
        except Exception:
            # Preserve the established strict-mode/network failure response;
            # the caller must never fall back to a local verification write.
            pass
    if blocked:
        return blocked

    assignment_ids = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in assignment_order_item_ids
            if str(value or "").strip()
        )
    )
    expected_global_item_id = str(expected_global_menu_item_id or "").strip()
    expected_global_variant_id = str(expected_global_variant_id or "").strip()
    if (
        not assignment_ids
        or not expected_global_item_id
        or not expected_global_variant_id
    ):
        return {
            "status": "error",
            "message": (
                "Exact assignment keys and the expected global menu identity "
                "are required."
            ),
        }

    placeholders = ", ".join("?" for _ in assignment_ids)
    rows = conn.execute(
        f"""
        SELECT
            mv.order_item_id,
            mv.menu_item_id,
            mv.variant_id,
            mv.is_verified,
            gil.global_menu_item_id,
            gvl.global_variant_id
        FROM menu_item_variants mv
        LEFT JOIN menu_item_global_links gil
            ON gil.local_menu_item_id = mv.menu_item_id
        LEFT JOIN variant_global_links gvl
            ON gvl.local_variant_id = mv.variant_id
        WHERE mv.order_item_id IN ({placeholders})
        ORDER BY mv.order_item_id
        """,
        assignment_ids,
    ).fetchall()
    rows_by_id = {str(row[0]): row for row in rows}
    missing_ids = [value for value in assignment_ids if value not in rows_by_id]
    if missing_ids:
        return {
            "status": "error",
            "message": "One or more assignment rows no longer exist. Refresh resolutions and try again.",
        }

    identity_before: dict[str, tuple[str, str]] = {}
    pending_rows: List[Dict[str, Any]] = []
    for assignment_id in assignment_ids:
        row = rows_by_id[assignment_id]
        actual_item_id = str(row[1] or "").strip()
        global_item_id = str(row[4] or "").strip()
        global_variant_id = str(row[5] or "").strip()
        if not global_item_id or not global_variant_id:
            return {
                "status": "error",
                "message": "Global identity is incomplete; repair its locator mapping first.",
                "code": "global_menu_identity_unresolved",
            }
        if (
            global_item_id != expected_global_item_id
            or global_variant_id != expected_global_variant_id
        ):
            return {
                "status": "error",
                "message": (
                    "The assignment's global identity changed while it was open. "
                    "Refresh resolutions before verifying it."
                ),
            }
        identity_before[assignment_id] = (global_item_id, global_variant_id)
        if not bool(row[3]):
            pending_rows.append(
                {
                    "order_item_id": assignment_id,
                    "menu_item_id": actual_item_id,
                    "variant_id": row[2],
                    "is_verified": 1,
                }
            )

    if not pending_rows:
        return {
            "status": "success",
            "message": "Assignment already verified.",
            "already_verified": True,
            "verified_assignment_ids": assignment_ids,
        }

    stable_mutation_id = str(mutation_id or uuid.uuid4())
    verification_events = build_menu_mapping_verification_event_payloads(
        conn, pending_rows
    )
    # A UI retry reuses mutation_id. Keep the nested event ids stable too so an
    # accepted status response can be validated and applied after an uncertain
    # network outcome instead of creating a second verification event.
    for index, event in enumerate(verification_events):
        event["remote_event_id"] = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"dn-analytics:assignment-verification:{stable_mutation_id}:{index}",
        ).hex

    plan = build_plan(
        mutation_type=MUTATION_TYPE_VERIFY,
        verification_events=verification_events,
        order_item_ids=[row["order_item_id"] for row in pending_rows],
        mutation_id=stable_mutation_id,
    )
    response = _commit_strict_plan(
        conn,
        plan,
        success_message="Assignment verified successfully.",
    )
    if response.get("status") != "success":
        return response

    verified_rows = conn.execute(
        f"""
        SELECT
            mv.order_item_id,
            mv.is_verified,
            gil.global_menu_item_id,
            gvl.global_variant_id
        FROM menu_item_variants mv
        LEFT JOIN menu_item_global_links gil
            ON gil.local_menu_item_id = mv.menu_item_id
        LEFT JOIN variant_global_links gvl
            ON gvl.local_variant_id = mv.variant_id
        WHERE mv.order_item_id IN ({placeholders})
        """,
        assignment_ids,
    ).fetchall()
    verified_by_id = {str(row[0]): row for row in verified_rows}
    locally_verified = all(
        assignment_id in verified_by_id
        and bool(verified_by_id[assignment_id][1])
        and (
            str(verified_by_id[assignment_id][2] or "").strip(),
            str(verified_by_id[assignment_id][3] or "").strip(),
        )
        == identity_before[assignment_id]
        for assignment_id in assignment_ids
    )
    if not locally_verified:
        return {"status": "error", "message": LOCAL_APPLY_FAILED_MESSAGE}

    response["verified_assignment_ids"] = assignment_ids
    response["mutation_id"] = stable_mutation_id
    return response
