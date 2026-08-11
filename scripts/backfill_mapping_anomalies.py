"""
Backfill the silent-reuse mapping guard from existing order history.

The live hook in services/clustering_service.py only sees orders ingested after
deploy, so it learns each id's baseline from the first post-deploy order and
cannot know that an id already changed product in the past. This one-shot
replays the full order history in chronological order through the SAME detection
function (src.core.mapping_anomalies.record_core_and_flag), so historical
silent reuses (e.g. itemids that switched from "Eggless Chocolate" to
"Just Chocolate") surface in the resolutions queue.

Run once, right after deploying the guard, before or with the first ingest:

    python3 scripts/backfill_mapping_anomalies.py            # additive
    python3 scripts/backfill_mapping_anomalies.py --reset    # rebuild from scratch

--reset clears both diagnostic tables first for a clean rebuild. Safe: they hold
no authoritative state and are never synced or backed up.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.db.connection import get_profile_connection
from src.core.profiles import get_profile
from src.core.mapping_anomalies import (
    ensure_mapping_anomaly_schema,
    record_core_and_flag,
)

_SOURCES = [
    (
        "items",
        False,
        """
        SELECT TRIM(CAST(oi.petpooja_itemid AS TEXT)) AS pid, oi.name_raw AS raw,
               mv.is_verified AS ver, mi.name AS mapped, mi.menu_item_id AS mid
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id
        JOIN menu_item_variants mv ON mv.order_item_id = TRIM(CAST(oi.petpooja_itemid AS TEXT))
        JOIN menu_items mi ON mi.menu_item_id = mv.menu_item_id
        WHERE TRIM(COALESCE(oi.petpooja_itemid, '')) <> ''
        ORDER BY o.created_on ASC, oi.order_item_id ASC
        """,
    ),
    (
        "addons",
        True,
        """
        SELECT TRIM(CAST(oa.petpooja_addonid AS TEXT)) AS pid, oa.name_raw AS raw,
               mv.is_verified AS ver, mi.name AS mapped, mi.menu_item_id AS mid
        FROM order_item_addons oa
        JOIN order_items oi ON oi.order_item_id = oa.order_item_id
        JOIN orders o ON o.order_id = oi.order_id
        JOIN menu_item_variants mv ON mv.order_item_id = TRIM(CAST(oa.petpooja_addonid AS TEXT))
        JOIN menu_items mi ON mi.menu_item_id = mv.menu_item_id
        WHERE TRIM(COALESCE(oa.petpooja_addonid, '')) <> ''
        ORDER BY o.created_on ASC, oa.order_item_addon_id ASC
        """,
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill mapping anomaly guard from history")
    parser.add_argument("--reset", action="store_true", help="Clear diagnostic tables and rebuild")
    parser.add_argument("--restaurant-id", required=True, help="Bound restaurant profile to backfill")
    args = parser.parse_args()

    try:
        conn, msg = get_profile_connection(get_profile(args.restaurant_id))
    except Exception as exc:
        print(f"Profile connection failed: {exc}")
        return
    print(msg)

    ensure_mapping_anomaly_schema(conn)
    if args.reset:
        conn.execute("DELETE FROM mapping_anomalies")
        conn.execute("DELETE FROM mapping_id_cores")
        conn.commit()
        print("Cleared mapping_anomalies + mapping_id_cores")

    cur = conn.cursor()
    flagged = []
    for label, is_addon, query in _SOURCES:
        rows = cur.execute(query).fetchall()
        n_flag = 0
        for pid, raw, ver, mapped, mid in rows:
            hit = record_core_and_flag(
                conn,
                order_item_id=pid,
                name_raw=raw,
                is_addon=is_addon,
                mapped_menu_item_id=mid,
                mapped_name=mapped,
                is_verified=bool(ver),
                detected_via="backfill",
                cursor=cur,
            )
            if hit:
                n_flag += 1
                flagged.append(hit)
        print(f"  {label}: replayed {len(rows)} rows, {n_flag} new anomalies")
    conn.commit()

    open_count = conn.execute(
        "SELECT COUNT(*) FROM mapping_anomalies WHERE status = 'open'"
    ).fetchone()[0]
    print(f"\nOpen anomalies now: {open_count}")

    # Surface the likeliest real reuses first: incoming core shares no word with
    # its baseline core (pure heuristic for ranking; not used for gating).
    def token_disjoint(a, b):
        ta = set((a or "").split("|")[-1].split())
        tb = set((b or "").split("|")[-1].split())
        return not (ta & tb)

    likely = [f for f in flagged if token_disjoint(f["baseline_core_key"], f["core_key"])]
    if likely:
        print("\nLikely real reuse (baseline and new core share no words) — review first:")
        for f in likely:
            print(f"  id={f['order_item_id']}  '{f['baseline_core_key']}' -> '{f['core_key']}'  booked as '{f['mapped_name']}'")

    conn.close()


if __name__ == "__main__":
    main()
