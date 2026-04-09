"""
Menu Management Utilities (SQLite Version)
"""

import csv
import os
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
import json
from scripts.seed_from_backups import export_to_backups
from utils.id_generator import generate_deterministic_id

NULL_VARIANT_SENTINEL = "__NULL_VARIANT__"
NULL_VARIANT_LABEL = "UNASSIGNED"


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


def _sync_menu_item_resolution_state(cursor, menu_item_id: str) -> None:
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


def _insert_merge_history(cursor, source_id: str, target_id: str, source_name: str, source_type: str, payload: Any) -> None:
    cursor.execute("""
        INSERT INTO merge_history (source_id, target_id, source_name, source_type, affected_order_items)
        VALUES (?, ?, ?, ?, ?)
    """, (source_id, target_id, source_name, source_type, json.dumps(payload)))


def _ensure_variant(conn, variant_name: str) -> str:
    """Create or reuse a verified variant row."""
    normalized_name = variant_name.strip()
    if not normalized_name:
        raise ValueError("Variant name cannot be empty")

    variant_id = generate_deterministic_id(normalized_name)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES (?, ?, 1)
            ON CONFLICT (variant_id) DO UPDATE SET
                variant_name = excluded.variant_name,
                is_verified = 1
        """, (variant_id, normalized_name))
        return variant_id
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


def _update_rows_for_variant_mapping(
    cursor,
    table_name: str,
    set_menu_item_id: str,
    set_variant_id: str,
    source_menu_item_id: str,
    source_variant_key: str,
) -> None:
    decoded_variant_id = _decode_variant_key(source_variant_key)
    if decoded_variant_id is None:
        cursor.execute(f"""
            UPDATE {table_name}
            SET menu_item_id = ?, variant_id = ?
            WHERE menu_item_id = ? AND variant_id IS NULL
        """, (set_menu_item_id, set_variant_id, source_menu_item_id))
    else:
        cursor.execute(f"""
            UPDATE {table_name}
            SET menu_item_id = ?, variant_id = ?
            WHERE menu_item_id = ? AND variant_id = ?
        """, (set_menu_item_id, set_variant_id, source_menu_item_id, decoded_variant_id))


def preview_merge_menu_items(
    conn,
    source_id: str,
    target_id: str,
    source_variant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Preview the impact of merging one menu item into another."""
    if source_id == target_id:
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
) -> Dict[str, Any]:
    """Merge a source item into a target item while remapping source variants."""
    if source_id == target_id:
        return {"status": "error", "message": "Cannot merge item into itself"}

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

        history_payload = {
            "kind": "variant_merge_v1",
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
        _insert_merge_history(cursor, source_id, target_id, source_name, source_type, history_payload)

        _update_merge_target_stats(cursor, target_id, source_metrics)

        for source_variant_id, resolved_variant_id in resolved_variant_ids.items():
            _update_rows_for_variant_mapping(cursor, "order_items", target_id, resolved_variant_id, source_id, source_variant_id)
            _update_rows_for_variant_mapping(cursor, "order_item_addons", target_id, resolved_variant_id, source_id, source_variant_id)
            _update_rows_for_variant_mapping(cursor, "menu_item_variants", target_id, resolved_variant_id, source_id, source_variant_id)

        cursor.execute("DELETE FROM menu_items WHERE menu_item_id = ?", (source_id,))

        conn.commit()
        export_to_backups(conn)

        return {
            "status": "success",
            "message": f"Merged '{source_name}' into '{target[1]}' with variant mapping",
            "stats": {
                "variant_mappings": len(resolved_variant_ids),
                "source_total_sold": int(source[4] or 0),
                "source_total_revenue": float(source[5] or 0),
            },
        }
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()


def merge_menu_items(conn, source_id: str, target_id: str, adopt_source_prices: bool = False) -> Dict[str, Any]:
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
        _insert_merge_history(cursor, source_id, target_id, source_name, source_type, affected_ids)
        
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
        
        # 5. Relink Mappings
        cursor.execute("""
            UPDATE menu_item_variants 
            SET menu_item_id = ? 
            WHERE menu_item_id = ?
        """, (target_id, source_id))
        mappings_updated = cursor.rowcount

        # 6. Delete Source Item
        cursor.execute("DELETE FROM menu_items WHERE menu_item_id = ?", (source_id,))
        
        conn.commit()
        
        # 7. Update Backups
        export_to_backups(conn)

        return {
            "status": "success", 
            "message": f"Merged '{source_name}' into '{target_name}'",
            "stats": {
                "orders_relinked": relinked_count,
                "mappings_updated": mappings_updated,
                "revenue_added": float(source_revenue or 0)
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
    """
    cursor = conn.cursor()
    try:
        # 1. Update Mapping (SQLite UPSERT)
        cursor.execute("""
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (?, ?, ?, 1)
            ON CONFLICT (order_item_id) DO UPDATE SET
                menu_item_id = excluded.menu_item_id,
                variant_id = excluded.variant_id,
                is_verified = 1
        """, (order_item_id, new_menu_item_id, new_variant_id))
        
        conn.commit()
        
        # 2. Update Backups
        export_to_backups(conn)
        
        return {"status": "success", "message": "Order item remapped successfully"}
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
        export_to_backups(conn)

        try:
            from src.core.learning.revenue_forecasting.volume_demand_ml.model_io import delete_models

            delete_models()
        except Exception as e:
            model_cleanup_error = str(e)

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

    if not target_menu_item_id and (not normalized_new_name or not normalized_new_type):
        return {"status": "error", "message": "Choose an existing target item or provide a new name and type"}

    if not target_variant_id and not normalized_new_variant_name:
        return {"status": "error", "message": "Choose an existing target variant or provide a new variant name"}

    cursor = conn.cursor()
    try:
        source_item = _fetch_menu_item_record(cursor, source_menu_item_id)
        if not source_item:
            return {"status": "error", "message": "Source item was not found"}

        source_variant_summary = _fetch_menu_item_variant_summary(conn, source_menu_item_id, source_variant_key)
        if not source_variant_summary:
            return {"status": "error", "message": "Source variant was not found"}

        source_variant = source_variant_summary[0]
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
            return {"status": "error", "message": "No unresolved mapping rows were found for this source variant"}

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

        should_record_history = (
            resolved_target_id != source_menu_item_id or
            resolved_target_variant_id != source_variant_db_id
        )
        if should_record_history:
            history_payload = {
                "kind": "resolution_variant_v1",
                "source_variant_id": source_variant_key,
                "target_variant_id": resolved_target_variant_id,
                "source_variant_name": source_variant["variant_name"],
                "target_variant_name": target_variant_name,
                "mapping_rows": mapping_rows,
                "order_items": order_item_rows,
                "order_item_addons": addon_rows,
            }
            _insert_merge_history(
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

        conn.commit()
        export_to_backups(conn)

        if resolved_target_id == source_menu_item_id and resolved_target_variant_id == source_variant_db_id:
            message = f"Verified '{source_item[1]}' ({source_variant['variant_name']}) as a resolved menu item + variant pair"
        else:
            message = (
                f"Resolved '{source_item[1]}' ({source_variant['variant_name']}) "
                f"into '{target_name}' ({target_variant_name})"
            )

        return {
            "status": "success",
            "message": message,
            "stats": {
                "mapping_rows_updated": len(mapping_rows),
                "order_items_updated": len(order_item_rows),
                "order_item_addons_updated": len(addon_rows),
            },
        }
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Variant resolution failed: {e}"}
    finally:
        cursor.close()


def resolve_item_rename(conn, source_id: str, new_name: str, new_type: str) -> Dict[str, Any]:
    """
    Handle resolution where an item is renamed.
    This effectively means:
    1. Generate ID for new name/type
    2. Check if that target item exists
    3. If not, create it (verified)
    4. Merge source into target
    """
    cursor = conn.cursor()
    try:
        target_id = generate_deterministic_id(new_name, new_type)
        
        # Check existence
        cursor.execute("SELECT menu_item_id FROM menu_items WHERE menu_item_id = ?", (target_id,))
        exists = cursor.fetchone()
        
        if not exists:
            # Create Target
            cursor.execute("""
                INSERT INTO menu_items (menu_item_id, name, type, is_verified)
                VALUES (?, ?, ?, 1)
            """, (target_id, new_name, new_type))
            conn.commit() # Commit creation effectively
            
        # Merge Source -> Target
        # Note: This handles the full merge logic including deletions
        return merge_menu_items(conn, source_id, target_id)
        
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Resolution failed: {e}"}
    finally:
        cursor.close()

def undo_merge(conn, merge_id: int) -> Dict[str, Any]:
    """
    Reverse a merge operation.
    1. Re-insert source menu item
    2. Point affected mappings back to source item
    3. Point affected order_items/addons back to source item
    4. Recalculate stats for both items
    5. Delete history record
    """
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
        
        # 2. Re-insert Source Item (SQLite UPSERT with INSERT OR IGNORE)
        cursor.execute("""
            INSERT OR IGNORE INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES (?, ?, ?, 1)
        """, (source_id, history_dict['source_name'], history_dict['source_type']))
        
        history_kind = history_payload.get("kind") if isinstance(history_payload, dict) else None

        if history_kind == "variant_merge_v1":
            for mapping_row in history_payload.get("mapping_rows", []):
                cursor.execute("""
                    UPDATE menu_item_variants
                    SET menu_item_id = ?, variant_id = ?
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
        elif history_kind == "resolution_variant_v1":
            for mapping_row in history_payload.get("mapping_rows", []):
                cursor.execute("""
                    UPDATE menu_item_variants
                    SET menu_item_id = ?, variant_id = ?, is_verified = ?, updated_at = CURRENT_TIMESTAMP
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
            # 3. Relink Mappings (menu_item_variants)
            if affected_ids:
                in_clause, params = _build_in_clause(affected_ids)
                cursor.execute(f"""
                    UPDATE menu_item_variants 
                    SET menu_item_id = ? 
                    WHERE order_item_id {in_clause}
                """, [source_id] + params)
                
                # 4. Relink Order Items (affected_ids are menu_item_variants.order_item_id; match order_items.order_item_id)
                cursor.execute(f"""
                    UPDATE order_items 
                    SET menu_item_id = ? 
                    WHERE order_item_id {in_clause}
                """, [source_id] + params)

                # 5. Relink Order Item Addons (same order_item_id set)
                cursor.execute(f"""
                    UPDATE order_item_addons 
                    SET menu_item_id = ? 
                    WHERE order_item_id {in_clause}
                """, [source_id] + params)
        
        # 6. Recalculate Stats (Filtered by Success status)
        for mid in [target_id, source_id]:
            _recalculate_menu_item_stats(cursor, mid)

        if history_kind == "resolution_variant_v1":
            for mid in [target_id, source_id]:
                _sync_menu_item_resolution_state(cursor, mid)

        
        # 7. Delete History
        cursor.execute("DELETE FROM merge_history WHERE merge_id = ?", (merge_id,))
        
        conn.commit()
        
        # 8. Update Backups
        export_to_backups(conn)
        
        return {"status": "success", "message": f"Successfully reversed merge of '{history_dict['source_name']}'"}
        
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
) -> Dict[str, Any]:
    """Mark an item as verified, optionally updating name/type"""
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

        if new_variant_id:
            _reassign_menu_item_variant(cursor, item_id, new_variant_id)
        
        conn.commit()
        export_to_backups(conn)
        return {"status": "success", "message": "Item verified successfully"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Verification failed: {e}"}
    finally:
        cursor.close()
