"""
Ensure every menu_items row has at least one menu_item_variants mapping.

Catalog stubs use variant 1_PIECE and a deterministic order_item_id so they
never collide with PetPooja line ids and stay stable across runs.
"""

from __future__ import annotations

import logging
from typing import Optional

from utils.id_generator import generate_deterministic_id

DEFAULT_CATALOG_VARIANT_NAME = "1_PIECE"
_STUB_NAMESPACE = "catalog_default_variant_stub"


def _default_variant_id(cursor) -> Optional[str]:
    cursor.execute(
        "SELECT variant_id FROM variants WHERE variant_name = ? LIMIT 1",
        (DEFAULT_CATALOG_VARIANT_NAME,),
    )
    row = cursor.fetchone()
    return str(row[0]) if row else None


def ensure_menu_item_has_variant_mapping(
    conn,
    menu_item_id: str,
    *,
    cursor,
) -> bool:
    """
    If the menu item has zero menu_item_variants rows, insert one 1_PIECE stub.

    Does not commit. Caller must own the transaction.

    Returns:
        True if a stub row was inserted, False if mappings already existed
        or menu item / default variant is missing.
    """
    mid = str(menu_item_id).strip()
    if not mid:
        return False

    cursor.execute(
        "SELECT 1 FROM menu_item_variants WHERE menu_item_id = ? LIMIT 1",
        (mid,),
    )
    if cursor.fetchone():
        return False

    cursor.execute("SELECT 1 FROM menu_items WHERE menu_item_id = ?", (mid,))
    if not cursor.fetchone():
        logging.warning(
            "ensure_menu_item_has_variant_mapping: menu_item_id %s not in menu_items",
            mid,
        )
        return False

    variant_id = _default_variant_id(cursor)
    if not variant_id:
        logging.error(
            "ensure_menu_item_has_variant_mapping: variant %r not found in variants table",
            DEFAULT_CATALOG_VARIANT_NAME,
        )
        return False

    stub_order_item_id = generate_deterministic_id(_STUB_NAMESPACE, mid)
    cursor.execute(
        """
        INSERT OR IGNORE INTO menu_item_variants (
            order_item_id, menu_item_id, variant_id,
            price, is_active, addon_eligible, delivery_eligible, is_verified
        )
        VALUES (?, ?, ?, 0, 1, 0, 1, 1)
        """,
        (stub_order_item_id, mid, variant_id),
    )
    if cursor.rowcount:
        return True

    cursor.execute(
        "SELECT 1 FROM menu_item_variants WHERE menu_item_id = ? LIMIT 1",
        (mid,),
    )
    return bool(cursor.fetchone())


def backfill_menu_items_missing_variant_mappings(conn, *, cursor) -> int:
    """
    Insert default 1_PIECE stubs for every menu item that has no mappings.

    Does not commit.
    """
    cursor.execute(
        """
        SELECT menu_item_id
        FROM menu_items mi
        WHERE NOT EXISTS (
            SELECT 1 FROM menu_item_variants miv
            WHERE miv.menu_item_id = mi.menu_item_id
        )
        """
    )
    missing = [str(r[0]) for r in cursor.fetchall()]
    inserted = 0
    for mid in missing:
        if ensure_menu_item_has_variant_mapping(conn, mid, cursor=cursor):
            inserted += 1
    return inserted
