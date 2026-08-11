import sqlite3
import unittest
from unittest.mock import patch

from src.core.derived_assignment_flush import flush_pending_derived_assignments
from src.core.menu_mutation_commit import (
    CommitResult,
    MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC,
)
from utils.menu_item_variant_enforcement import (
    addon_seeded_mapping_order_item_id,
    catalog_stub_order_item_id,
)


class DerivedAssignmentFlushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0
            );
            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL,
                unit TEXT,
                value REAL,
                is_verified BOOLEAN DEFAULT 0
            );
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                is_verified BOOLEAN DEFAULT 0,
                assignment_seq INTEGER,
                pending_local INTEGER DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                restaurant_id INTEGER
            );
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                order_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT,
                petpooja_itemid INTEGER,
                name_raw TEXT,
                updated_at TEXT
            );
            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES (?, ?, ?, ?)",
            [
                ("item_a", "Orange Ice Cream", "Ice Cream", 0),
                ("item_b", "Mango Ice Cream", "Ice Cream", 0),
            ],
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, unit, value, is_verified) VALUES ('variant_a', 'SCOOP', NULL, NULL, 0)"
        )
        self.conn.execute("INSERT INTO orders (order_id, restaurant_id) VALUES (1, 1)")
        self.conn.executemany(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id, petpooja_itemid, name_raw
            )
            VALUES (?, 1, ?, ?, ?, ?)
            """,
            [
                (1, "item_a", "variant_a", 101, "Orange Ice Cream Scoop"),
                (2, "item_b", "variant_a", 202, "Mango Ice Cream Scoop"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified, assignment_seq, pending_local
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("101", "item_a", "variant_a", 1, None, 0),
                ("202", "item_b", "variant_a", 0, 44, 0),
                ("303", "item_a", "variant_a", 0, None, 0),
                ("404", "item_a", "variant_a", 0, None, 1),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @patch("src.core.derived_assignment_flush.strict_mode_active", return_value=True)
    @patch("src.core.derived_assignment_flush.commit_mutation")
    def test_flush_sends_only_pos_backed_unacked_rows_as_unverified(self, commit, _strict) -> None:
        commit.return_value = CommitResult(status="ok")

        summary = flush_pending_derived_assignments(self.conn, max_batches=1)

        self.assertEqual(summary["sent"], 1)
        self.assertEqual(commit.call_count, 1)
        plan = commit.call_args.args[1]
        self.assertEqual(plan.mutation_type, MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC)
        assignments = plan.event["merge_payload"]["assignments"]
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["order_item_id"], "101")
        self.assertEqual(assignments[0]["is_verified"], 0)
        self.assertEqual(plan.catalog_delta["items"][0]["menu_item_id"], "item_a")
        self.assertEqual(plan.catalog_delta["variants"][0]["variant_id"], "variant_a")

    @patch("src.core.derived_assignment_flush.strict_mode_active", return_value=False)
    def test_flush_noops_when_strict_not_ready(self, _strict) -> None:
        summary = flush_pending_derived_assignments(self.conn)

        self.assertFalse(summary["attempted"])
        self.assertEqual(summary["reason"], "strict mode not ready")

    @patch("src.core.derived_assignment_flush.strict_mode_active", return_value=True)
    @patch("src.core.derived_assignment_flush.commit_mutation")
    def test_synthetic_rows_cannot_starve_backed_rows(self, commit, _strict) -> None:
        # Never-flushable synthetic rows (catalog stubs, addon backfills) sorted
        # ahead of a POS-backed row must not exhaust the candidate window.
        commit.return_value = CommitResult(status="ok")
        stub_items = [(f"item_stub_{i}", f"Stub {i}", "Ice Cream", 0) for i in range(5)]
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES (?, ?, ?, ?)",
            stub_items,
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            )
            VALUES (?, ?, 'variant_a', 0, NULL, 0)
            """,
            [
                (catalog_stub_order_item_id(mid), mid)
                for mid, _, _, _ in stub_items
            ],
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            )
            VALUES (?, 'item_b', 'variant_a', 0, NULL, 0)
            """,
            (addon_seeded_mapping_order_item_id("item_b", "variant_a"),),
        )
        # NULL updated_at sorts first: all synthetics precede the backed row.
        self.conn.execute(
            "UPDATE menu_item_variants SET updated_at = '2026-01-01' WHERE order_item_id = '101'"
        )
        self.conn.commit()

        # batch_size=1 gives a window of 4; six synthetics sit ahead of '101'.
        summary = flush_pending_derived_assignments(self.conn, max_batches=1, batch_size=1)

        self.assertEqual(summary["sent"], 1)
        plan = commit.call_args.args[1]
        assignments = plan.event["merge_payload"]["assignments"]
        self.assertEqual([a["order_item_id"] for a in assignments], ["101"])

    @patch("src.core.derived_assignment_flush.strict_mode_active", return_value=True)
    @patch("src.core.derived_assignment_flush.commit_mutation")
    def test_skipped_existing_not_counted_as_accepted(self, commit, _strict) -> None:
        commit.return_value = CommitResult(status="ok", skipped_existing=["101"])

        summary = flush_pending_derived_assignments(self.conn, max_batches=1)

        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["accepted"], 0)
        self.assertEqual(summary["skipped_existing"], 1)


if __name__ == "__main__":
    unittest.main()
