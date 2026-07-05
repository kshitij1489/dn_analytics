"""
Hard-reset this install's menu clustering to the server's current ground truth
(cutover piece 3 of docs/MENU_MERGE_CONFLICT_SYNC_PLAN.md).

Pulls the materialized assignment snapshot from Dachnona and overwrites local
menu_item_variants / order_items to match, ignoring the normal "already
bootstrapped" short-circuit. Use during a coordinated cutover (after the golden
baseline has been imported server-side) or to recover a single diverged install.

WARNING: this overwrites local cluster state, including any unsynced pending
local edits. Back up the local DB first. Do not run it on a machine whose local
work you still want to keep.

Usage:
    python scripts/force_reseed_menu_assignments.py [--yes]
"""

import argparse
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.db.connection import get_db_connection
from src.core.menu_assignment_bootstrap import (
    force_reseed_menu_assignments,
    get_menu_assignments_snapshot_endpoint,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (for scripted cutovers).",
    )
    args = parser.parse_args()

    conn, err = get_db_connection()
    if conn is None:
        print(f"Error: DB connection failed: {err}", file=sys.stderr)
        return 1

    try:
        endpoint = get_menu_assignments_snapshot_endpoint(conn)
        if not endpoint:
            print(
                "Error: no cloud sync URL configured; cannot reach the assignment snapshot.",
                file=sys.stderr,
            )
            return 1
        _, auth = get_cloud_sync_config(conn)

        if not args.yes:
            print(
                "This OVERWRITES local menu clustering from the server snapshot,\n"
                f"  endpoint: {endpoint}\n"
                "including any unsynced local edits. Back up the DB first."
            )
            if input("Type 'reseed' to continue: ").strip().lower() != "reseed":
                print("Aborted.")
                return 1

        result = force_reseed_menu_assignments(conn, endpoint, auth=auth)
    finally:
        conn.close()

    if result.get("status") == "error":
        print(f"Force re-seed failed: {result.get('error')}", file=sys.stderr)
        return 1

    print(
        "Force re-seed complete: "
        f"{result.get('rows_applied', 0)} rows applied, "
        f"{result.get('rows_stale', 0)} already current, "
        f"{result.get('rows_missing', 0)} without a local order item, "
        f"watermark_seq={result.get('watermark_seq')}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
