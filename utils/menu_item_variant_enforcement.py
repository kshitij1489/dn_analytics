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
ADDON_MAPPING_NAMESPACE = "addon_seeded_mapping"


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


def backfill_addon_only_variant_mappings(conn, *, cursor) -> int:
    """
    Seed a menu_item_variants row for every (menu_item_id, variant_id) pair that
    was sold as an addon and resolves to an existing menu_item + variant but has
    no mapping row.

    Such pairs never get a mapping through the normal ingest path: mappings are
    keyed by the PetPooja line id, and the addon path's addonid is often NULL
    (collapsing many addons onto one key) or collides with an itemid, so
    OrderItemCluster.add short-circuits before inserting the pair's own row. A
    flavor+size sold only as an addon then stays invisible to the menu matrix.

    The order_item_id is derived deterministically from (menu_item_id,
    variant_id), so it never collides with PetPooja line ids and re-running is
    idempotent. Rows are marked addon_eligible = 1 (they are addon usage by
    definition) and is_verified = 0 so they surface on the Resolutions page.

    These are local-only read-model rows, like the 1_PIECE catalog stubs:
    pending_local stays 0 so they never enter the assignment outbox (their
    synthetic order_item_id has no server-side line to apply against), and they
    are re-derived on every sync, so a server snapshot bootstrap that drops them
    self-heals on the next run.

    Does not commit. Caller must own the transaction.

    Returns: number of menu_item_variants rows inserted.
    """
    # Iterates DISTINCT (menu_item_id, variant_id) pairs, which is bounded by
    # catalog size (tens to low hundreds), not by order-history volume, so the
    # per-row insert is not a scaling concern.
    cursor.execute(
        """
        SELECT DISTINCT a.menu_item_id, a.variant_id
        FROM order_item_addons a
        JOIN menu_items mi ON a.menu_item_id = mi.menu_item_id
        JOIN variants   v  ON a.variant_id  = v.variant_id
        WHERE a.menu_item_id IS NOT NULL
          AND a.variant_id  IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM menu_item_variants m
              WHERE m.menu_item_id = a.menu_item_id
                AND m.variant_id  = a.variant_id
          )
        """
    )
    pairs = cursor.fetchall()

    inserted = 0
    for menu_item_id, variant_id in pairs:
        order_item_id = addon_seeded_mapping_order_item_id(
            str(menu_item_id), str(variant_id)
        )
        cursor.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id,
                price, is_active, addon_eligible, delivery_eligible, is_verified
            )
            VALUES (?, ?, ?, 0, 1, 1, 1, 0)
            ON CONFLICT (order_item_id) DO NOTHING
            """,
            (order_item_id, str(menu_item_id), str(variant_id)),
        )
        inserted += cursor.rowcount
    return inserted


def catalog_stub_order_item_id(menu_item_id: str) -> str:
    """Deterministic order_item_id for default 1_PIECE catalog stub rows."""
    return generate_deterministic_id(_STUB_NAMESPACE, str(menu_item_id))


def is_catalog_stub_mapping(menu_item_id: str, order_item_id: str) -> bool:
    """True when order_item_id is the synthetic catalog-default stub for menu_item_id."""
    return str(order_item_id) == catalog_stub_order_item_id(menu_item_id)


def addon_seeded_mapping_order_item_id(menu_item_id: str, variant_id: str) -> str:
    """Deterministic order_item_id for addon-only backfill rows."""
    return generate_deterministic_id(
        ADDON_MAPPING_NAMESPACE, str(menu_item_id), str(variant_id)
    )


def mark_addon_eligible_from_usage(conn, *, cursor) -> int:
    """
    Set addon_eligible = 1 for every menu_item_variants pair that has ever
    been sold as an addon (i.e. appears in order_item_addons).

    Derived from observed usage in a single batched UPDATE per call, not a
    per-row lookup. The flag is sticky: it only ever flips 0 -> 1, mirroring
    the accumulate-only semantics of menu_items.sold_as_addon, so re-running
    it every sync is idempotent and cheap.

    Does not commit. Caller must own the transaction.

    Returns: number of menu_item_variants rows newly flagged.
    """
    cursor.execute(
        """
        UPDATE menu_item_variants
        SET addon_eligible = 1
        WHERE addon_eligible = 0
          AND (menu_item_id, variant_id) IN (
              SELECT DISTINCT menu_item_id, variant_id
              FROM order_item_addons
              WHERE menu_item_id IS NOT NULL
                AND variant_id IS NOT NULL
          )
        """
    )
    return cursor.rowcount
