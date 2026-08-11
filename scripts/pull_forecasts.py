#!/usr/bin/env python3
"""Manual forecast pull from deployed central server (Phase 5 dev helper)."""

from __future__ import annotations

import argparse
import json
import sys

from src.core.db.connection import get_profile_connection
from src.core.forecast_sync import pull_and_apply_forecast_deltas
from src.core.profiles import get_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull central forecast deltas into local SQLite cache.")
    parser.add_argument(
        "--families",
        default="revenue,items,volume,weather",
        help="Comma-separated forecast families to pull",
    )
    parser.add_argument("--bootstrap", action="store_true", help="Force bootstrap instead of delta")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--restaurant-id", required=True, help="Physical restaurant profile to update")
    args = parser.parse_args()

    try:
        conn, _ = get_profile_connection(get_profile(args.restaurant_id))
    except Exception as exc:
        print(f"Failed to open restaurant profile: {exc}", file=sys.stderr)
        return 1
    try:
        summary = pull_and_apply_forecast_deltas(
            conn,
            families=args.families,
            limit=args.limit,
            force_bootstrap=args.bootstrap,
        )
        print(json.dumps(summary, indent=2))
        return 0 if summary.get("status") == "ok" or summary.get("skipped") else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
