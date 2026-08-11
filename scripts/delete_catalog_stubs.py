"""
List and delete synthetic catalog-default stub rows in menu_item_variants.

Catalog stubs are created by ensure_menu_item_has_variant_mapping() when a
menu item has no mappings. They use a deterministic order_item_id from the
namespace catalog_default_variant_stub + menu_item_id (see
utils/menu_item_variant_enforcement.py).

These rows are not PetPooja order lines. Deleting them does not remove real
order_items or order_item_addons history.

Usage:
    # List stubs (default; dry run)
    python3 scripts/delete_catalog_stubs.py
    python3 scripts/delete_catalog_stubs.py --db "/path/to/analytics.db"

    # Delete stub mapping rows only
    python3 scripts/delete_catalog_stubs.py --apply

    # Also delete menu_items that become unused (no orders, no addons, no mappings)
    python3 scripts/delete_catalog_stubs.py --apply --delete-orphan-menu-items

    # Refresh explicit JSON backups after delete
    python3 scripts/delete_catalog_stubs.py --apply --export-backups --export-out ~/dn-analytics-backups
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.db.connection import get_db_connection
from utils.menu_item_variant_enforcement import (
    addon_seeded_mapping_order_item_id,
    catalog_stub_order_item_id,
    is_catalog_stub_mapping,
)


def _fetch_stub_rows(conn) -> List[Dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            miv.order_item_id,
            miv.menu_item_id,
            mi.name,
            mi.type,
            v.variant_name,
            miv.price,
            miv.addon_eligible,
            miv.is_verified,
            (
                SELECT COUNT(*)
                FROM order_items oi
                WHERE oi.menu_item_id = mi.menu_item_id
            ) AS order_item_rows,
            (
                SELECT COUNT(*)
                FROM order_item_addons oa
                WHERE oa.menu_item_id = mi.menu_item_id
            ) AS addon_rows,
            (
                SELECT COUNT(*)
                FROM menu_item_variants m2
                WHERE m2.menu_item_id = mi.menu_item_id
            ) AS mapping_rows
        FROM menu_item_variants miv
        JOIN menu_items mi ON miv.menu_item_id = mi.menu_item_id
        JOIN variants v ON miv.variant_id = v.variant_id
        ORDER BY mi.type, mi.name, v.variant_name
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]
    cursor.close()

    stubs: List[Dict[str, Any]] = []
    for row in rows:
        if is_catalog_stub_mapping(row["menu_item_id"], row["order_item_id"]):
            row["orphan_menu_item"] = (
                row["order_item_rows"] == 0
                and row["addon_rows"] == 0
                and row["mapping_rows"] == 1
            )
            stubs.append(row)
    return stubs


def _print_stub_table(stubs: List[Dict[str, Any]]) -> None:
    if not stubs:
        print("No catalog stub rows found.")
        return

    print(f"Found {len(stubs)} catalog stub row(s):\n")
    header = (
        f"{'name':<50} {'type':<10} {'variant':<22} "
        f"{'orphan':<7} {'orders':<7} {'addons':<7} {'maps':<5} menu_item_id"
    )
    print(header)
    print("-" * len(header))
    for row in stubs:
        print(
            f"{row['name']:<50} {row['type']:<10} {row['variant_name']:<22} "
            f"{str(row['orphan_menu_item']):<7} {row['order_item_rows']:<7} "
            f"{row['addon_rows']:<7} {row['mapping_rows']:<5} {row['menu_item_id']}"
        )
        print(f"  stub order_item_id: {row['order_item_id']}")
        expected = catalog_stub_order_item_id(row["menu_item_id"])
        if row["order_item_id"] != expected:
            print(f"  WARNING: expected stub id {expected}")


def _delete_stubs(
    conn,
    stubs: List[Dict[str, Any]],
    *,
    delete_orphan_menu_items: bool,
) -> Dict[str, int]:
    cursor = conn.cursor()
    deleted_mappings = 0
    deleted_menu_items = 0
    skipped_menu_items = 0

    for row in stubs:
        cursor.execute(
            """
            DELETE FROM menu_item_variants
            WHERE order_item_id = ? AND menu_item_id = ?
            """,
            (row["order_item_id"], row["menu_item_id"]),
        )
        deleted_mappings += cursor.rowcount

    if delete_orphan_menu_items:
        orphan_ids = sorted(
            {
                row["menu_item_id"]
                for row in stubs
                if row["orphan_menu_item"]
            }
        )
        for menu_item_id in orphan_ids:
            cursor.execute(
                "SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = ?",
                (menu_item_id,),
            )
            remaining_mappings = int(cursor.fetchone()[0] or 0)
            cursor.execute(
                "SELECT COUNT(*) FROM order_items WHERE menu_item_id = ?",
                (menu_item_id,),
            )
            order_rows = int(cursor.fetchone()[0] or 0)
            cursor.execute(
                "SELECT COUNT(*) FROM order_item_addons WHERE menu_item_id = ?",
                (menu_item_id,),
            )
            addon_rows = int(cursor.fetchone()[0] or 0)

            if remaining_mappings or order_rows or addon_rows:
                skipped_menu_items += 1
                continue

            cursor.execute(
                "DELETE FROM menu_items WHERE menu_item_id = ?",
                (menu_item_id,),
            )
            deleted_menu_items += cursor.rowcount

    cursor.close()
    return {
        "deleted_mappings": deleted_mappings,
        "deleted_menu_items": deleted_menu_items,
        "skipped_menu_items": skipped_menu_items,
    }


def _backfill_addon_mappings_for_menu_items(conn, menu_item_ids: Optional[List[str]] = None) -> int:
    """Create synthetic addon-only mapping rows before addon-only items can be re-stubbed."""
    cursor = conn.cursor()
    params: List[str] = []
    menu_scope_sql = ""
    if menu_item_ids:
        placeholders = ",".join("?" for _ in menu_item_ids)
        menu_scope_sql = f"AND a.menu_item_id IN ({placeholders})"
        params.extend(menu_item_ids)

    cursor.execute(
        f"""
        SELECT DISTINCT a.menu_item_id, a.variant_id
        FROM order_item_addons a
        JOIN menu_items mi ON a.menu_item_id = mi.menu_item_id
        JOIN variants v ON a.variant_id = v.variant_id
        WHERE a.menu_item_id IS NOT NULL
          AND a.variant_id IS NOT NULL
          {menu_scope_sql}
          AND NOT EXISTS (
              SELECT 1
              FROM menu_item_variants m
              WHERE m.menu_item_id = a.menu_item_id
                AND m.variant_id = a.variant_id
          )
        """,
        params,
    )
    pairs = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]

    inserted = 0
    for menu_item_id, variant_id in pairs:
        cursor.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id,
                price, is_active, addon_eligible, delivery_eligible, is_verified
            )
            VALUES (?, ?, ?, 0, 1, 1, 1, 0)
            ON CONFLICT (order_item_id) DO NOTHING
            """,
            (
                addon_seeded_mapping_order_item_id(menu_item_id, variant_id),
                menu_item_id,
                variant_id,
            ),
        )
        inserted += cursor.rowcount

    cursor.close()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List or delete synthetic catalog-default stub mappings"
    )
    parser.add_argument(
        "--db",
        help="Path to analytics.db (default: DB_URL env or project analytics.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete stub rows (default is list-only / dry run)",
    )
    parser.add_argument(
        "--delete-orphan-menu-items",
        action="store_true",
        help=(
            "After deleting stubs, also delete menu_items that had only a stub "
            "and have no order_items or order_item_addons"
        ),
    )
    parser.add_argument(
        "--skip-addon-backfill",
        action="store_true",
        help=(
            "Do not create addon-derived mappings for affected menu items after "
            "stub deletion. Normally leave this off; otherwise addon-only items "
            "with zero mappings can receive a new catalog stub later."
        ),
    )
    parser.add_argument(
        "--export-backups",
        action="store_true",
        help="Run export_to_backups after --apply",
    )
    parser.add_argument(
        "--export-out",
        help="Directory for --export-backups. Defaults to ~/dn-analytics-backups.",
    )
    args = parser.parse_args()

    conn, msg = get_db_connection(args.db)
    if not conn:
        print(f"Connection failed: {msg}")
        return 1
    print(msg)

    stubs = _fetch_stub_rows(conn)
    _print_stub_table(stubs)

    if not args.apply:
        print("\nDry run only. Re-run with --apply to delete stub mapping rows.")
        conn.close()
        return 0

    if not stubs:
        if args.apply and not args.skip_addon_backfill:
            try:
                addon_backfills = _backfill_addon_mappings_for_menu_items(conn)
                conn.commit()
                print(f"Backfilled {addon_backfills} addon-derived mapping row(s).")
            except Exception as exc:
                conn.rollback()
                print(f"Addon backfill failed: {exc}")
                return 1
        conn.close()
        return 0

    try:
        stats = _delete_stubs(
            conn,
            stubs,
            delete_orphan_menu_items=args.delete_orphan_menu_items,
        )
        addon_backfills = 0
        if not args.skip_addon_backfill:
            addon_backfills = _backfill_addon_mappings_for_menu_items(conn)
        conn.commit()
        print(
            f"\nDeleted {stats['deleted_mappings']} stub mapping row(s)."
        )
        if not args.skip_addon_backfill:
            print(f"Backfilled {addon_backfills} addon-derived mapping row(s).")
        if args.delete_orphan_menu_items:
            print(
                f"Deleted {stats['deleted_menu_items']} orphan menu item(s); "
                f"skipped {stats['skipped_menu_items']}."
            )
        if args.export_backups:
            from scripts.seed_from_backups import export_to_backups

            if export_to_backups(conn, out_dir=args.export_out):
                print("Exported cluster_state_backup.json and id_maps_backup.json")
            else:
                print("Warning: export_to_backups failed")
    except Exception as exc:
        conn.rollback()
        print(f"Delete failed: {exc}")
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
