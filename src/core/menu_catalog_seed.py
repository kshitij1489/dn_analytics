"""In-memory menu catalog seed and bootstrap payload helpers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict

from utils.id_generator import generate_deterministic_id
from utils.menu_item_variant_enforcement import backfill_menu_items_missing_variant_mappings
from utils.variant_metadata import infer_variant_metadata


_DECIMAL_PLACES = Decimal("0.01")
_NO_VARIANT_IDS = frozenset(
    {
        "None",
        "__NULL_VARIANT__",
        generate_deterministic_id("UNKNOWN"),
    }
)


def _clean_required_text(value: Any, field_name: str) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        raise ValueError(f"{field_name} must not be blank")
    return cleaned


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split()) or None


def _is_no_variant_id(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip() in _NO_VARIANT_IDS


def _decimal_string(
    value: Any,
    *,
    field_name: str,
    max_digits: int,
    allow_none: bool = False,
) -> str | None:
    """Validate a wire decimal without converting it through binary float."""
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field_name} must not be null")
    try:
        text = str(value).strip()
        if not text:
            raise InvalidOperation
        decimal_value = Decimal(text)
        if not decimal_value.is_finite() or decimal_value < 0:
            raise InvalidOperation
        quantized = decimal_value.quantize(_DECIMAL_PLACES)
        if quantized != decimal_value or len(quantized.as_tuple().digits) > max_digits:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name} decimal value {value!r}") from exc
    return "0.00" if quantized == 0 else format(quantized, ".2f")


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


def build_shared_pos_catalog(
    conn, *, include_itemcode: bool = False
) -> list[Dict[str, Any]]:
    """
    Build the complete active §9 shared-POS observation from SQLite.

    ``menu_item_variants`` owns the current local assignment, while raw order
    rows prove whether its key is a Petpooja item or addon and provide the most
    recently observed catalog price. Name-derived and synthetic catalog rows
    have no POS evidence and are deliberately omitted.
    """
    cursor = conn.cursor()
    try:
        order_item_columns = {
            str(row[1])
            for row in cursor.execute("PRAGMA table_info(order_items)").fetchall()
        }
        itemcode_expression = (
            "oi.itemcode" if "itemcode" in order_item_columns else "NULL"
        )
        cursor.execute(
            f"""
            WITH item_evidence AS (
                SELECT
                    'pos_item' AS locator_type,
                    TRIM(CAST(oi.petpooja_itemid AS TEXT)) AS locator_value,
                    CASE
                        WHEN {itemcode_expression} IS NULL
                          OR TRIM(CAST({itemcode_expression} AS TEXT)) = ''
                        THEN NULL
                        ELSE TRIM(CAST({itemcode_expression} AS TEXT))
                    END AS itemcode,
                    oi.unit_price AS observed_price,
                    ROW_NUMBER() OVER (
                        PARTITION BY TRIM(CAST(oi.petpooja_itemid AS TEXT))
                        ORDER BY o.created_on DESC, o.order_id DESC, oi.order_item_id DESC
                    ) AS evidence_rank
                FROM order_items oi
                JOIN orders o ON o.order_id = oi.order_id
                WHERE oi.petpooja_itemid IS NOT NULL
                  AND TRIM(CAST(oi.petpooja_itemid AS TEXT)) <> ''
            ),
            addon_evidence AS (
                SELECT
                    'pos_addon' AS locator_type,
                    TRIM(CAST(a.petpooja_addonid AS TEXT)) AS locator_value,
                    NULL AS itemcode,
                    a.price AS observed_price,
                    ROW_NUMBER() OVER (
                        PARTITION BY TRIM(CAST(a.petpooja_addonid AS TEXT))
                        ORDER BY o.created_on DESC, o.order_id DESC,
                                 a.order_item_addon_id DESC
                    ) AS evidence_rank
                FROM order_item_addons a
                JOIN order_items oi ON oi.order_item_id = a.order_item_id
                JOIN orders o ON o.order_id = oi.order_id
                WHERE a.petpooja_addonid IS NOT NULL
                  AND TRIM(CAST(a.petpooja_addonid AS TEXT)) <> ''
            ),
            pos_evidence AS (
                SELECT locator_type, locator_value, itemcode, observed_price
                FROM item_evidence
                WHERE evidence_rank = 1
                UNION ALL
                SELECT locator_type, locator_value, itemcode, observed_price
                FROM addon_evidence
                WHERE evidence_rank = 1
            )
            SELECT
                evidence.locator_type,
                evidence.locator_value,
                evidence.itemcode,
                mapping.menu_item_id,
                mapping.variant_id,
                item.menu_item_id AS joined_menu_item_id,
                item.name AS item_name,
                item.type AS item_type,
                variant.variant_id AS joined_variant_id,
                variant.variant_name,
                variant.unit AS variant_unit,
                variant.value AS variant_value,
                evidence.observed_price
            FROM menu_item_variants mapping
            LEFT JOIN menu_items item ON item.menu_item_id = mapping.menu_item_id
            LEFT JOIN variants variant ON variant.variant_id = mapping.variant_id
            JOIN pos_evidence evidence
              ON evidence.locator_value = TRIM(CAST(mapping.order_item_id AS TEXT))
            WHERE mapping.is_active = 1
            ORDER BY evidence.locator_type, evidence.locator_value
            """
        )

        catalog: list[Dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        kind_by_value: dict[str, str] = {}
        for row in cursor.fetchall():
            locator_type = _clean_required_text(row[0], "locator_type")
            if locator_type not in {"pos_item", "pos_addon"}:
                raise ValueError(f"Unsupported locator_type {locator_type!r}")
            locator_value = _clean_required_text(row[1], "locator_value")
            locator_key = (locator_type, locator_value)
            if locator_key in seen_keys:
                raise ValueError(
                    "Duplicate shared POS locator "
                    f"({locator_type!r}, {locator_value!r})"
                )
            seen_keys.add(locator_key)
            prior_kind = kind_by_value.setdefault(locator_value, locator_type)
            if prior_kind != locator_type:
                raise ValueError(
                    f"Locator value {locator_value!r} appears as both "
                    f"{prior_kind!r} and {locator_type!r}"
                )

            raw_itemcode = row[2]
            menu_item_id = _clean_required_text(row[3], "menu_item_id")
            if row[5] is None:
                raise ValueError(
                    f"Mapping {locator_value!r} references missing menu item "
                    f"{menu_item_id!r}"
                )
            raw_variant_id = row[4]
            no_variant = _is_no_variant_id(raw_variant_id)
            variant_id = (
                None
                if no_variant
                else _clean_required_text(raw_variant_id, "variant_id")
            )
            joined_variant_id = row[8]
            if variant_id is not None and joined_variant_id is None:
                raise ValueError(
                    f"Mapping {locator_value!r} references missing variant {variant_id!r}"
                )

            normalized_row = {
                "locator_type": locator_type,
                "locator_value": locator_value,
                "menu_item_id": menu_item_id,
                "variant_id": variant_id,
                "item_name": _clean_required_text(row[6], "item_name"),
                "item_type": _clean_required_text(row[7], "item_type"),
                "variant_name": None if no_variant else _clean_optional_text(row[9]),
                "variant_unit": None if no_variant else _clean_optional_text(row[10]),
                "variant_value": (
                    None
                    if no_variant
                    else _decimal_string(
                        row[11],
                        field_name="variant_value",
                        max_digits=12,
                        allow_none=True,
                    )
                ),
                "price": _decimal_string(
                    row[12], field_name="price", max_digits=10
                ),
            }
            if include_itemcode:
                normalized_row["itemcode"] = (
                    _clean_optional_text(raw_itemcode)
                    if locator_type == "pos_item"
                    else None
                )
            catalog.append(normalized_row)
        return catalog
    finally:
        cursor.close()


def build_group_pos_alias_observation(conn) -> list[Dict[str, Any]]:
    """Build truthful sold-locator evidence with raw provider itemcodes."""
    return build_shared_pos_catalog(conn, include_itemcode=True)
