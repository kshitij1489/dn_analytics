"""
Rebuild the itemcode -> parent projection from current order rows + assignments.

Thin operational wrapper around src.core.itemcode_mapping.rebuild_itemcode_mappings
(see that module for the projection semantics). Defaults to --dry-run: the
rebuild runs inside a transaction, prints the summary and every conflict, then
rolls back. Nothing is written unless --apply is passed explicitly.

    python3 scripts/rebuild_itemcode_mappings.py                    # dry run, all restaurants
    python3 scripts/rebuild_itemcode_mappings.py --restaurant-id 1  # dry run, one restaurant
    python3 scripts/rebuild_itemcode_mappings.py --apply            # actually write
    python3 scripts/rebuild_itemcode_mappings.py --db /path/to/copy.db --apply
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.db.connection import get_db_connection
from src.core.itemcode_mapping import rebuild_itemcode_mappings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the per-restaurant itemcode -> menu item projection."
    )
    parser.add_argument(
        "--db",
        help="Path to the SQLite database (default: the configured analytics.db)",
    )
    parser.add_argument(
        "--restaurant-id",
        type=int,
        help="Rebuild only this restaurant's scope (default: all restaurants)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the rebuilt projection. Without this flag the rebuild is "
        "computed, reported, and rolled back (dry run).",
    )
    args = parser.parse_args()

    conn, message = get_db_connection(args.db)
    if conn is None:
        print(f"ERROR: could not connect: {message}")
        return 1

    try:
        result = rebuild_itemcode_mappings(conn, restaurant_id=args.restaurant_id)

        mode = "APPLY" if args.apply else "DRY RUN"
        scope = f"restaurant {args.restaurant_id}" if args.restaurant_id else "all restaurants"
        print(f"[{mode}] itemcode projection rebuild ({scope})")
        print(f"  restaurants scanned: {result.restaurants_scanned}")
        print(f"  itemcodes scanned:   {result.codes_scanned}")
        print(f"  active mappings:     {result.active_written}")
        print(f"  conflicts:           {result.conflicts_written}")
        print(f"  stale rows removed:  {result.stale_removed}")
        if result.conflicts:
            print("  conflict details (resolve via desktop merge/remap UI):")
            for detail in result.conflicts:
                print(
                    f"    restaurant {detail['restaurant_id']} itemcode "
                    f"{detail['itemcode']!r}: {detail['menu_item_ids']}"
                )

        if args.apply:
            conn.commit()
            print("Committed.")
        else:
            conn.rollback()
            print("Rolled back (dry run). Re-run with --apply to write.")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: rebuild failed and was rolled back: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
