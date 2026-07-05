"""
Export the golden install's FULL per-order-item cluster assignment as a baseline
file (cutover piece 1 of docs/MENU_MERGE_CONFLICT_SYNC_PLAN.md §"baseline seed").

The materialized server ground truth (OrderItemAssignment) is built from the
menu-merge event log, so it only covers order items that were *merged* or
*resolved*. To make every cluster identical fleet-wide we need the complete
state of the anointed machine — every menu_item_variants row, merged or not.

This dumps that state to a JSON file that the Dachnona
`import_menu_assignment_baseline` management command ingests as one authoritative
baseline (a batch of explicit-assignment menu-merge events), after which the
whole fleet converges to it via normal pull / the snapshot fast path.

Usage:
    python scripts/export_menu_assignments.py [--out data/menu_assignments_baseline.json]

Run this ONLY on the machine whose merges/resolutions you want to keep, after
verifying its clustering is the state you want to become ground truth.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.db.connection import get_db_connection
from src.core.sync_identity import get_sync_attribution
from src.core.utils.path_helper import get_resource_path


DEFAULT_OUT = "menu_assignments_baseline.json"


def export_assignments(conn) -> dict:
    """Read every menu_item_variants row as a normalized assignment list."""
    rows = conn.execute(
        """
        SELECT order_item_id, menu_item_id, variant_id, is_verified
        FROM menu_item_variants
        WHERE order_item_id IS NOT NULL
          AND menu_item_id IS NOT NULL
        ORDER BY order_item_id
        """
    ).fetchall()

    assignments = []
    for row in rows:
        order_item_id = str(row["order_item_id"]).strip()
        menu_item_id = str(row["menu_item_id"]).strip()
        if not order_item_id or not menu_item_id:
            continue
        variant_id = row["variant_id"]
        assignments.append(
            {
                "order_item_id": order_item_id,
                "menu_item_id": menu_item_id,
                # null = SQL NULL variant; the server/client normalize it to the
                # NULL-variant sentinel on apply.
                "variant_id": None if variant_id is None else str(variant_id),
                "is_verified": 1 if int(row["is_verified"] or 0) else 0,
            }
        )

    return {
        "kind": "menu_assignment_baseline_v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "attribution": get_sync_attribution(conn),
        "assignment_count": len(assignments),
        "assignments": assignments,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: data/%s)" % DEFAULT_OUT,
    )
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else Path(get_resource_path("data")) / DEFAULT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn, err = get_db_connection()
    if conn is None:
        print(f"Error: DB connection failed: {err}", file=sys.stderr)
        return 1

    try:
        payload = export_assignments(conn)
    finally:
        conn.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(
        f"Exported {payload['assignment_count']} assignments to {out_path}\n"
        f"Next: copy this file to the server and run\n"
        f"  python backend/manage.py import_menu_assignment_baseline "
        f"--file {out_path.name} --scope <scope_key>"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
