"""In-memory menu catalog seed and bootstrap payload helpers."""

from __future__ import annotations

from typing import Any, Dict

from utils.id_generator import generate_deterministic_id
from utils.menu_item_variant_enforcement import backfill_menu_items_missing_variant_mappings
from utils.variant_metadata import infer_variant_metadata


def seed_catalog(
    conn,
    id_maps: Dict[str, Any],
    cluster_state: Dict[str, Any],
    *,
    seed_mappings: bool = False,
) -> Dict[str, int]:
    """
    Seed menu_items/variants from in-memory bootstrap payload dictionaries.

    The transaction behavior intentionally matches the old file-backed
    perform_seeding helper: commit on success, rollback on error.
    """
    cursor = conn.cursor()
    try:
        menu_items_count = 0
        skipped_unmapped_count = 0
        type_id_to_str = id_maps.get("type_id_to_str", {})
        item_type_by_menu_id = {}
        for key in cluster_state.keys():
            parts = str(key).split(":")
            if parts[0] not in item_type_by_menu_id:
                type_id = parts[1] if len(parts) > 1 else None
                item_type_by_menu_id[parts[0]] = type_id_to_str.get(type_id, "Dessert")

        for menu_item_id, clean_name in id_maps.get("menu_id_to_str", {}).items():
            item_type = item_type_by_menu_id.get(menu_item_id)
            if item_type is None:
                # Every real menu item carries at least a stub mapping (variant
                # enforcement), so an id_maps entry with no cluster_state key is
                # stale/legacy and should not fabricate a wrongly typed item.
                skipped_unmapped_count += 1
                continue

            cursor.execute(
                """
                INSERT INTO menu_items (menu_item_id, name, type, is_verified)
                VALUES (?, ?, ?, 1)
                ON CONFLICT (menu_item_id) DO UPDATE SET
                    name = excluded.name,
                    type = excluded.type,
                    is_verified = 1
                """,
                (menu_item_id, clean_name, item_type),
            )
            menu_items_count += 1

        variant_meta = id_maps.get("variant_id_to_meta", {})
        variants_count = 0
        for variant_id, variant_name in id_maps.get("variant_id_to_str", {}).items():
            metadata = infer_variant_metadata(variant_name, variant_meta.get(variant_id))
            cursor.execute(
                """
                INSERT INTO variants (variant_id, variant_name, unit, value, is_verified)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT (variant_id) DO UPDATE SET
                    variant_name = excluded.variant_name,
                    unit = excluded.unit,
                    value = excluded.value,
                    is_verified = 1
                """,
                (variant_id, variant_name, metadata["unit"], metadata["value"]),
            )
            variants_count += 1

        mappings_count = 0
        if seed_mappings:
            for key, orders in cluster_state.items():
                menu_item_id = str(key).split(":", 1)[0]
                if not isinstance(orders, dict):
                    continue
                for order_item_id, items in orders.items():
                    if not isinstance(items, list):
                        continue
                    seen_variants = set()
                    for row in items:
                        if not isinstance(row, list) or len(row) < 2:
                            continue
                        variant_id = row[1]
                        if variant_id in seen_variants:
                            continue
                        cursor.execute(
                            """
                            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
                            VALUES (?, ?, ?, 1)
                            ON CONFLICT (order_item_id) DO UPDATE SET
                                menu_item_id = excluded.menu_item_id,
                                variant_id = excluded.variant_id,
                                is_verified = 1
                            """,
                            (str(order_item_id), menu_item_id, variant_id),
                        )
                        seen_variants.add(variant_id)
                        mappings_count += 1

        stub_count = backfill_menu_items_missing_variant_mappings(conn, cursor=cursor)
        conn.commit()
        return {
            "items_seeded": menu_items_count,
            "variants_seeded": variants_count,
            "mappings_seeded": mappings_count,
            "stub_count": stub_count,
            "skipped_unmapped": skipped_unmapped_count,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def build_id_maps(conn) -> Dict[str, Dict[str, Any]]:
    """Build the bootstrap id_maps payload directly from SQLite."""
    cursor = conn.cursor()
    try:
        id_maps: Dict[str, Dict[str, Any]] = {
            "menu_id_to_str": {},
            "variant_id_to_str": {},
            "variant_id_to_meta": {},
            "type_id_to_str": {},
        }

        cursor.execute("SELECT menu_item_id, name, type FROM menu_items ORDER BY menu_item_id")
        type_to_id = {}
        for menu_item_id, name, menu_type in cursor.fetchall():
            id_maps["menu_id_to_str"][str(menu_item_id)] = name
            if menu_type not in type_to_id:
                type_id = generate_deterministic_id(menu_type)
                type_to_id[menu_type] = type_id
                id_maps["type_id_to_str"][type_id] = menu_type

        cursor.execute(
            "SELECT variant_id, variant_name, unit, value FROM variants ORDER BY variant_id"
        )
        for variant_id, variant_name, unit, value in cursor.fetchall():
            id_maps["variant_id_to_str"][str(variant_id)] = variant_name
            metadata = infer_variant_metadata(
                variant_name,
                {"unit": unit, "value": value},
            )
            if metadata["unit"] is not None or metadata["value"] is not None:
                id_maps["variant_id_to_meta"][str(variant_id)] = metadata

        return id_maps
    finally:
        cursor.close()


def build_cluster_state(conn) -> Dict[str, Dict[str, list]]:
    """Build the bootstrap cluster_state payload directly from SQLite."""
    cursor = conn.cursor()
    try:
        cluster_state: Dict[str, Dict[str, list]] = {}
        cursor.execute(
            """
            SELECT mv.menu_item_id, mi.type, mv.order_item_id, mv.variant_id
            FROM menu_item_variants mv
            JOIN menu_items mi ON mv.menu_item_id = mi.menu_item_id
            ORDER BY mv.menu_item_id, mv.order_item_id, mv.variant_id
            """
        )
        for menu_item_id, menu_type, order_item_id, variant_id in cursor.fetchall():
            type_id = generate_deterministic_id(menu_type) if menu_type else "unknown"
            key = f"{menu_item_id}:{type_id}"
            order_key = str(order_item_id)
            cluster_state.setdefault(key, {}).setdefault(order_key, []).append(
                [order_key, str(variant_id)]
            )
        return cluster_state
    finally:
        cursor.close()
