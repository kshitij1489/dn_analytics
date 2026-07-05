"""
Phase C3 conflict matrix: two simulated installs (two SQLite DBs) resolving the
same order items differently against a fake event server must converge to the
higher server_seq, with a supersede notice for the loser.

Also covers: v1 legacy derivation parity via contracts/menu_merge_event_fixtures.json,
echo/ack, stale-event no-op, undo-after-conflict, and orphan GC + stats.
"""

import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.menu_assignment_apply import (
    apply_assignments,
    extract_assignments,
    list_supersede_notices,
)
from src.core.menu_mapping_verification_sync_events import extract_verification_entries
from src.core.menu_merge_sync import pull_and_apply_menu_merge_events
from src.core.menu_merge_sync_events import ensure_menu_merge_sync_tables
from utils import menu_utils


FIXTURES_PATH = Path(__file__).resolve().parent.parent / "contracts" / "menu_merge_event_fixtures.json"
VERIFICATION_FIXTURES_PATH = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "menu_mapping_verification_event_fixtures.json"
)

BASE_SCHEMA = """
    CREATE TABLE orders (
        order_id INTEGER PRIMARY KEY,
        order_status TEXT NOT NULL
    );

    CREATE TABLE menu_items (
        menu_item_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        is_verified BOOLEAN DEFAULT 0,
        total_sold INTEGER DEFAULT 0,
        total_revenue REAL DEFAULT 0,
        sold_as_item INTEGER DEFAULT 0,
        sold_as_addon INTEGER DEFAULT 0,
        updated_at TEXT
    );

    CREATE TABLE variants (
        variant_id TEXT PRIMARY KEY,
        variant_name TEXT NOT NULL,
        unit TEXT,
        value REAL,
        is_verified BOOLEAN DEFAULT 0,
        updated_at TEXT
    );

    CREATE TABLE menu_item_variants (
        order_item_id TEXT PRIMARY KEY,
        menu_item_id TEXT NOT NULL,
        variant_id TEXT,
        price REAL DEFAULT 0,
        is_active BOOLEAN DEFAULT 1,
        addon_eligible BOOLEAN DEFAULT 0,
        delivery_eligible BOOLEAN DEFAULT 1,
        is_verified BOOLEAN DEFAULT 1,
        updated_at TEXT
    );

    CREATE TABLE order_items (
        order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        menu_item_id TEXT,
        variant_id TEXT,
        quantity INTEGER DEFAULT 1,
        total_price REAL DEFAULT 0,
        name_raw TEXT,
        updated_at TEXT
    );

    CREATE TABLE order_item_addons (
        order_item_addon_id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_item_id INTEGER,
        menu_item_id TEXT,
        variant_id TEXT,
        quantity INTEGER DEFAULT 0,
        price REAL DEFAULT 0
    );

    CREATE TABLE merge_history (
        merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_type TEXT NOT NULL,
        affected_order_items TEXT NOT NULL,
        merged_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
"""


def make_install_db() -> sqlite3.Connection:
    """One simulated install: same POS-sourced orders as every other install."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(BASE_SCHEMA)
    conn.execute("INSERT INTO orders (order_id, order_status) VALUES (1, 'Success')")
    conn.executemany(
        """
        INSERT INTO menu_items (menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("item_a", "Iced Coffee", "Beverage", 0, 2, 240.0, 2),
            ("item_b", "Cold Coffee", "Beverage", 1, 3, 390.0, 3),
            ("item_c", "Coffee Frappe", "Beverage", 1, 1, 180.0, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES (?, ?, 1)",
        [("variant_x", "X"), ("variant_y", "Y")],
    )
    conn.executemany(
        """
        INSERT INTO order_items (order_item_id, order_id, menu_item_id, variant_id, quantity, total_price, name_raw)
        VALUES (?, 1, ?, NULL, ?, ?, ?)
        """,
        [
            (1, "item_a", 2, 240.0, "Iced Coffee"),
            (2, "item_b", 3, 390.0, "Cold Coffee"),
            (3, "item_c", 1, 180.0, "Coffee Frappe"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
        VALUES (?, ?, NULL, ?)
        """,
        [("1", "item_a", 0), ("2", "item_b", 1), ("3", "item_c", 1)],
    )
    conn.commit()
    ensure_menu_merge_sync_tables(conn)
    conn.commit()
    return conn


class FakeEventServer:
    """Append-only log assigning server_seq in ingestion order (S1 semantics)."""

    def __init__(self) -> None:
        self.rows = []  # list of (server_seq, event_dict)
        self.seen_ids = set()

    def ingest(self, event: dict) -> int:
        remote_event_id = str(event.get("remote_event_id"))
        if remote_event_id in self.seen_ids:
            for seq, row in self.rows:
                if str(row.get("remote_event_id")) == remote_event_id:
                    return seq
        seq = len(self.rows) + 1
        self.rows.append((seq, json.loads(json.dumps(event))))
        self.seen_ids.add(remote_event_id)
        return seq

    def ingest_outbox(self, conn) -> int:
        """Push this install's unsent events, in emit order."""
        rows = conn.execute(
            """
            SELECT event_id, payload
            FROM menu_merge_sync_events
            WHERE uploaded_at IS NULL
            ORDER BY occurred_at ASC, created_at ASC
            """
        ).fetchall()
        count = 0
        for row in rows:
            self.ingest(json.loads(row["payload"]))
            conn.execute(
                "UPDATE menu_merge_sync_events SET uploaded_at = CURRENT_TIMESTAMP WHERE event_id = ?",
                (row["event_id"],),
            )
            count += 1
        conn.commit()
        return count

    def fetch(self, cursor, limit) -> dict:
        start = int(cursor) if cursor else 0
        page = self.rows[start : start + int(limit or 100)]
        events = [
            {**event, "server_seq": seq, "server_ingested_at": f"2026-07-05T00:00:{seq:02d}+00:00"}
            for seq, event in page
        ]
        next_cursor = str(start + len(events)) if events else (cursor or None)
        return {"events": events, "next_cursor": next_cursor, "error": None}


def pull_install(conn, server: FakeEventServer) -> dict:
    """Pull to the end of the fake stream; returns the merged stats."""
    combined = {"merge_events_applied": 0, "undo_events_applied": 0, "events_skipped": 0, "events_failed": 0}
    with patch(
        "src.core.menu_merge_sync._fetch_remote_events",
        side_effect=lambda endpoint, auth, cursor, limit: server.fetch(cursor, limit),
    ):
        while True:
            stats = pull_and_apply_menu_merge_events(conn, "http://fake", auth=None)
            for key in combined:
                combined[key] += stats.get(key) or 0
            if not stats.get("events_fetched"):
                break
    return combined


def mapping_state(conn, order_item_id: str) -> tuple:
    row = conn.execute(
        """
        SELECT menu_item_id, variant_id, is_verified
        FROM menu_item_variants
        WHERE order_item_id = ?
        """,
        (order_item_id,),
    ).fetchone()
    return (row["menu_item_id"], row["variant_id"], int(row["is_verified"])) if row else None


class MenuAssignmentApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            patch("utils.menu_utils.export_to_backups", return_value=True),
            patch("utils.menu_utils._clear_impacted_models", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        self.install1 = make_install_db()
        self.install2 = make_install_db()
        self.addCleanup(self.install1.close)
        self.addCleanup(self.install2.close)
        self.server = FakeEventServer()

    # --- fixture parity -----------------------------------------------------

    def test_fixture_extraction_parity(self) -> None:
        fixtures = json.loads(FIXTURES_PATH.read_text())["fixtures"]
        self.assertGreaterEqual(len(fixtures), 10)
        for fixture in fixtures:
            with self.subTest(fixture=fixture["name"]):
                self.assertEqual(
                    extract_assignments(fixture["event"]),
                    fixture["expected_assignments"],
                )

    def test_verification_fixture_extraction_parity(self) -> None:
        fixtures = json.loads(VERIFICATION_FIXTURES_PATH.read_text())["fixtures"]
        self.assertGreaterEqual(len(fixtures), 10)
        for fixture in fixtures:
            with self.subTest(fixture=fixture["name"]):
                self.assertEqual(
                    extract_verification_entries(fixture["event"]),
                    fixture["expected_entries"],
                )

    # --- conflict matrix ----------------------------------------------------

    def test_conflicting_resolutions_converge_to_higher_seq_with_loser_notice(self) -> None:
        r1 = menu_utils.resolve_menu_item_variant(
            self.install1,
            source_menu_item_id="item_a",
            source_variant_id=menu_utils.NULL_VARIANT_SENTINEL,
            target_menu_item_id="item_b",
            target_variant_id="variant_x",
        )
        self.assertEqual(r1["status"], "success", r1.get("message"))
        r2 = menu_utils.resolve_menu_item_variant(
            self.install2,
            source_menu_item_id="item_a",
            source_variant_id=menu_utils.NULL_VARIANT_SENTINEL,
            target_menu_item_id="item_c",
            target_variant_id="variant_y",
        )
        self.assertEqual(r2["status"], "success", r2.get("message"))

        # Local rows are provisional until the echo acks them.
        row = self.install1.execute(
            "SELECT pending_local, assignment_seq FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(int(row["pending_local"]), 1)
        self.assertIsNone(row["assignment_seq"])

        self.server.ingest_outbox(self.install1)  # seq 1
        self.server.ingest_outbox(self.install2)  # seq 2 (wins)
        pull_install(self.install1, self.server)
        pull_install(self.install2, self.server)

        expected = ("item_c", "variant_y", 1)
        self.assertEqual(mapping_state(self.install1, "1"), expected)
        self.assertEqual(mapping_state(self.install2, "1"), expected)

        row1 = self.install1.execute(
            "SELECT assignment_seq, pending_local FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(int(row1["assignment_seq"]), 2)
        self.assertEqual(int(row1["pending_local"]), 0)

        # Loser (install1) is notified; winner is not.
        notices1 = list_supersede_notices(self.install1)
        notices2 = list_supersede_notices(self.install2)
        self.assertEqual(len(notices1), 1)
        self.assertEqual(notices1[0]["order_item_id"], "1")
        winning_event_id = str(self.server.rows[1][1]["remote_event_id"])
        self.assertEqual(notices1[0]["superseded_by_event_id"], winning_event_id)
        self.assertEqual(notices2, [])

    def test_conflicting_basic_merges_converge_and_gc_source(self) -> None:
        m1 = menu_utils.merge_menu_items(self.install1, "item_a", "item_b")
        self.assertEqual(m1["status"], "success", m1.get("message"))
        m2 = menu_utils.merge_menu_items(self.install2, "item_a", "item_c")
        self.assertEqual(m2["status"], "success", m2.get("message"))

        self.server.ingest_outbox(self.install1)  # seq 1
        self.server.ingest_outbox(self.install2)  # seq 2 (wins)
        pull_install(self.install1, self.server)
        pull_install(self.install2, self.server)

        for conn in (self.install1, self.install2):
            state = mapping_state(conn, "1")
            self.assertEqual(state[0], "item_c")
            order_row = conn.execute(
                "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()
            self.assertEqual(order_row["menu_item_id"], "item_c")
            # Orphan GC: item_a has no mappings and no usage anywhere.
            self.assertIsNone(
                conn.execute("SELECT 1 FROM menu_items WHERE menu_item_id = 'item_a'").fetchone()
            )
            # Stats epilogue: item_c's totals recomputed from actual rows
            # (order item 1: qty 2 / 240.0 + its own order item 3: qty 1 / 180.0).
            stats_row = conn.execute(
                "SELECT total_sold, total_revenue FROM menu_items WHERE menu_item_id = 'item_c'"
            ).fetchone()
            self.assertEqual(int(stats_row["total_sold"]), 3)
            self.assertAlmostEqual(float(stats_row["total_revenue"]), 420.0)

        self.assertEqual(len(list_supersede_notices(self.install1)), 1)
        self.assertEqual(list_supersede_notices(self.install2), [])

    def test_undo_after_conflicting_merge_restores_prior_assignments(self) -> None:
        m1 = menu_utils.merge_menu_items(self.install1, "item_a", "item_b")
        self.assertEqual(m1["status"], "success")
        self.server.ingest_outbox(self.install1)  # seq 1
        pull_install(self.install2, self.server)
        self.assertEqual(mapping_state(self.install2, "1")[0], "item_b")

        remote_history = self.install2.execute(
            "SELECT merge_id, origin FROM merge_history WHERE source_id = 'item_a'"
        ).fetchone()
        self.assertIsNotNone(remote_history)
        self.assertEqual(remote_history["origin"], "remote")

        undo = menu_utils.undo_merge(self.install1, int(m1["merge_id"]))
        self.assertEqual(undo["status"], "success", undo.get("message"))
        self.server.ingest_outbox(self.install1)  # seq 2 = undo event

        pull_install(self.install1, self.server)
        pull_install(self.install2, self.server)

        for conn in (self.install1, self.install2):
            self.assertEqual(mapping_state(conn, "1")[0], "item_a")
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM menu_items WHERE menu_item_id = 'item_a'").fetchone()
            )
        # The reverted merge left Resolution History on the peer too.
        self.assertIsNone(
            self.install2.execute(
                "SELECT 1 FROM merge_history WHERE source_id = 'item_a' AND target_id = 'item_b'"
            ).fetchone()
        )

    # --- echo / ack ---------------------------------------------------------

    def test_echo_ack_stamps_seq_clears_pending_without_duplicate_history(self) -> None:
        m1 = menu_utils.merge_menu_items(self.install1, "item_a", "item_b")
        self.assertEqual(m1["status"], "success")
        self.server.ingest_outbox(self.install1)

        stats = pull_install(self.install1, self.server)
        self.assertEqual(stats["events_skipped"], 1)
        self.assertEqual(stats["merge_events_applied"], 0)

        row = self.install1.execute(
            "SELECT assignment_seq, pending_local, menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(int(row["assignment_seq"]), 1)
        self.assertEqual(int(row["pending_local"]), 0)
        self.assertEqual(row["menu_item_id"], "item_b")

        history_count = self.install1.execute(
            "SELECT COUNT(*) FROM merge_history"
        ).fetchone()[0]
        self.assertEqual(int(history_count), 1)
        self.assertEqual(list_supersede_notices(self.install1), [])

    # --- stale events -------------------------------------------------------

    def test_stale_event_is_a_noop(self) -> None:
        event_new = {
            "remote_event_id": "ev-new",
            "event_type": "menu_merge.applied",
            "target_item": {"menu_item_id": "item_b", "name": "Cold Coffee", "type": "Beverage"},
            "source_item": {"menu_item_id": "item_a", "name": "Iced Coffee", "type": "Beverage"},
            "merge_payload": {"kind": "basic_merge_v1"},
        }
        result = apply_assignments(
            self.install1,
            [{"order_item_id": "1", "menu_item_id": "item_b", "variant_id": "variant_x", "is_verified": 1}],
            5,
            event_new,
        )
        self.assertEqual(result["rows_applied"], 1)
        # Existing row: the merge stream reassigns the mapping but leaves the
        # is_verified flag to the verification stream, so it keeps its prior 0.
        self.assertEqual(mapping_state(self.install1, "1"), ("item_b", "variant_x", 0))

        event_old = dict(event_new, remote_event_id="ev-old")
        result = apply_assignments(
            self.install1,
            [{"order_item_id": "1", "menu_item_id": "item_c", "variant_id": "variant_y", "is_verified": 1}],
            3,
            event_old,
        )
        self.assertEqual(result["rows_applied"], 0)
        self.assertEqual(len(result["stale_rows"]), 1)
        self.assertEqual(mapping_state(self.install1, "1"), ("item_b", "variant_x", 0))

    # --- is_verified single-owner convergence (I5) --------------------------

    def test_is_verified_converges_regardless_of_merge_verification_order(self) -> None:
        # The reproduced divergence: a merge event carrying is_verified and a
        # later reopen, applied in opposite relative orders on two installs.
        # Because the merge stream reassigns the mapping but never rewrites
        # is_verified on an existing row, both installs converge to the
        # verification stream's latest word (reopen -> 0).
        from src.core.menu_mapping_verification_sync import (
            _apply_verification_by_order_item_id,
        )

        merge_event = {
            "remote_event_id": "ev-resolution",
            "event_type": "menu_merge.applied",
            "source_item": {"menu_item_id": "item_a", "name": "Iced Coffee", "type": "Beverage"},
            "target_item": {"menu_item_id": "item_b", "name": "Cold Coffee", "type": "Beverage"},
            "merge_payload": {"kind": "resolution_variant_v1"},
        }
        # order item "2" starts mapped to item_b, is_verified=1 on both installs.
        merge_assignment = [
            {"order_item_id": "2", "menu_item_id": "item_b", "variant_id": "variant_x", "is_verified": 1}
        ]

        # install1: merge (assignment_seq 10) THEN reopen (verification_seq 5).
        apply_assignments(self.install1, merge_assignment, 10, merge_event, detect_supersede=False)
        _apply_verification_by_order_item_id(self.install1, "2", 0, server_seq=5)
        self.install1.commit()

        # install2: reopen (verification_seq 5) THEN merge (assignment_seq 10).
        _apply_verification_by_order_item_id(self.install2, "2", 0, server_seq=5)
        apply_assignments(self.install2, merge_assignment, 10, merge_event, detect_supersede=False)
        self.install2.commit()

        self.assertEqual(mapping_state(self.install1, "2"), ("item_b", "variant_x", 0))
        self.assertEqual(mapping_state(self.install2, "2"), ("item_b", "variant_x", 0))

    # --- v1 legacy events on the wire ----------------------------------------

    def test_v1_event_applies_via_derived_assignments(self) -> None:
        v1_event = {
            "remote_event_id": "peer-v1-basic",
            "schema_version": 1,
            "event_type": "menu_merge.applied",
            "occurred_at": "2026-07-01 09:00:00",
            "source_item": {"menu_item_id": "item_a", "name": "Iced Coffee", "type": "Beverage", "is_verified": False},
            "target_item": {"menu_item_id": "item_b", "name": "Cold Coffee", "type": "Beverage", "is_verified": True},
            "merge_payload": {
                "kind": "basic_merge_v1",
                "history_payload": {"kind": "basic_merge_v1", "affected_order_item_ids": ["1"], "suggestion_refs": []},
            },
        }
        self.server.ingest(v1_event)
        pull_install(self.install2, self.server)

        state = mapping_state(self.install2, "1")
        self.assertEqual(state[0], "item_b")
        # variant unspecified in v1 basic merges: unchanged (NULL here)
        self.assertIsNone(state[1])
        row = self.install2.execute(
            "SELECT assignment_seq FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        # The assignment applier (not the legacy cluster replay) stamped the seq.
        self.assertEqual(int(row["assignment_seq"]), 1)


if __name__ == "__main__":
    unittest.main()
