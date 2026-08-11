"""
Phase 3 lifecycle-convergence tests for the itemcode projection (plan §8).

Verifies that assignment-changing lifecycle points rebuild the derived
itemcode -> parent projection: local (non-strict/replay) merge, variant merge,
resolution, undo, the shared assignment batch epilogue, and that a strict-mode
capture rollback cannot leak projection changes. The projection itself is
covered by tests/test_itemcode_mapping.py; ingest routing by
tests/test_itemcode_clustering.py.
"""

import sqlite3
import unittest
from unittest.mock import patch

from src.core.itemcode_mapping import (
    get_active_itemcode_mapping,
    rebuild_itemcode_mappings,
    rebuild_itemcode_mappings_best_effort,
)
from src.core.menu_merge_sync import _run_assignment_batch_epilogue
from utils import menu_utils


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE restaurants (restaurant_id INTEGER PRIMARY KEY, name TEXT);

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            restaurant_id INTEGER REFERENCES restaurants(restaurant_id),
            order_status TEXT NOT NULL DEFAULT 'Success'
        );

        CREATE TABLE menu_items (
            menu_item_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            is_verified BOOLEAN DEFAULT 0,
            suggestion_id TEXT,
            total_sold INTEGER DEFAULT 0,
            total_revenue REAL DEFAULT 0,
            sold_as_item INTEGER DEFAULT 0,
            sold_as_addon INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        );

        CREATE TABLE variants (
            variant_id TEXT PRIMARY KEY,
            variant_name TEXT NOT NULL,
            unit TEXT,
            value REAL,
            description TEXT,
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
            is_verified BOOLEAN DEFAULT 0,
            pending_local BOOLEAN DEFAULT 0,
            assignment_seq INTEGER,
            updated_at TEXT
        );

        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY,
            order_id INTEGER,
            menu_item_id TEXT,
            variant_id TEXT,
            petpooja_itemid INTEGER,
            itemcode TEXT,
            quantity INTEGER DEFAULT 1,
            total_price REAL DEFAULT 0,
            name_raw TEXT,
            updated_at TEXT
        );

        CREATE TABLE order_item_addons (
            order_item_addon_id INTEGER PRIMARY KEY,
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

        CREATE TABLE system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE item_forecast_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL
        );
        CREATE TABLE item_backtest_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL
        );
        CREATE TABLE volume_forecast_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL
        );
        CREATE TABLE volume_backtest_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL
        );
        """
    )
    return conn


def _seed(conn):
    """Two parents, each owning one itemcode through one assigned POS itemid."""
    conn.execute("INSERT INTO restaurants (restaurant_id, name) VALUES (1, 'R1')")
    conn.execute("INSERT INTO orders (order_id, restaurant_id, order_status) VALUES (1, 1, 'Success')")
    conn.executemany(
        "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES (?, ?, 'Beverage', 1)",
        [("m_source", "Iced Coffee"), ("m_target", "Cold Coffee")],
    )
    conn.executemany(
        "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES (?, ?, 1)",
        [("variant_small", "SMALL"), ("variant_large", "LARGE")],
    )
    conn.executemany(
        """
        INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified, assignment_seq)
        VALUES (?, ?, ?, 1, 1)
        """,
        [("101", "m_source", "variant_small"), ("201", "m_target", "variant_large")],
    )
    conn.executemany(
        """
        INSERT INTO order_items (
            order_item_id, order_id, menu_item_id, variant_id,
            petpooja_itemid, itemcode, quantity, total_price, name_raw
        ) VALUES (?, 1, ?, ?, ?, ?, 1, 100.0, ?)
        """,
        [
            (1, "m_source", "variant_small", 101, "C1", "Iced Coffee Small"),
            (2, "m_target", "variant_large", 201, "C2", "Cold Coffee Large"),
        ],
    )
    rebuild_itemcode_mappings(conn)
    conn.commit()


def _mapping(conn, code, rid=1):
    return conn.execute(
        """
        SELECT status, menu_item_id, conflict_menu_item_ids
        FROM itemcode_mappings WHERE restaurant_id = ? AND itemcode = ?
        """,
        (rid, code),
    ).fetchone()


@patch("utils.menu_utils._clear_impacted_models", return_value=None)
class ItemcodeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()
        _seed(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_seed_projection_is_active_per_code(self, *_mocks):
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C1"), "m_source")
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C2"), "m_target")

    def test_local_merge_leaves_both_codes_active_on_target(self, *_mocks):
        result = menu_utils.merge_menu_items(
            self.conn, "m_source", "m_target", emit_sync_event=False
        )
        self.assertEqual(result["status"], "success", result.get("message"))
        # Both aliases converge on the surviving parent (business consolidation).
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C1"), "m_target")
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C2"), "m_target")
        # Source deletion was not blocked by the projection FK.
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM menu_items WHERE menu_item_id = 'm_source'"
            ).fetchone()
        )

    def test_merge_resolves_split_conflict(self, *_mocks):
        # A second itemid shares C1 but is assigned to the other parent: split.
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES ('102', 'm_target', 'variant_large', 1)
            """
        )
        self.conn.execute(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id,
                petpooja_itemid, itemcode, quantity, total_price, name_raw
            ) VALUES (3, 1, 'm_target', 'variant_large', 102, 'C1', 1, 100.0, 'Iced Coffee Large')
            """
        )
        rebuild_itemcode_mappings(self.conn)
        self.conn.commit()
        row = _mapping(self.conn, "C1")
        self.assertEqual(row["status"], "conflict")

        result = menu_utils.merge_menu_items(
            self.conn, "m_source", "m_target", emit_sync_event=False
        )
        self.assertEqual(result["status"], "success", result.get("message"))
        row = _mapping(self.conn, "C1")
        self.assertEqual((row["status"], row["menu_item_id"]), ("active", "m_target"))

    def test_undo_restores_aliases_from_assignments(self, *_mocks):
        result = menu_utils.merge_menu_items(
            self.conn, "m_source", "m_target", emit_sync_event=False
        )
        self.assertEqual(result["status"], "success", result.get("message"))
        undo = menu_utils.undo_merge(self.conn, result["merge_id"], emit_sync_event=False)
        self.assertEqual(undo["status"], "success", undo.get("message"))
        # Undo rebuilds the projection identity from the restored assignments.
        # The restored source row is a pending local edit (assignment_seq NULL),
        # so C1 is active-but-not-route-eligible until a sync re-acks it (Phase
        # 9.2); assert the mapping identity directly rather than via routing.
        c1, c2 = _mapping(self.conn, "C1"), _mapping(self.conn, "C2")
        self.assertEqual((c1["status"], c1["menu_item_id"]), ("active", "m_source"))
        self.assertEqual((c2["status"], c2["menu_item_id"]), ("active", "m_target"))
        self.assertIsNone(get_active_itemcode_mapping(self.conn, 1, "C1"))

    def test_variant_merge_rebuilds_projection(self, *_mocks):
        result = menu_utils.merge_menu_items_with_variant_mappings(
            self.conn,
            "m_source",
            "m_target",
            [{"source_variant_id": "variant_small", "target_variant_id": "variant_large"}],
            emit_sync_event=False,
        )
        self.assertEqual(result["status"], "success", result.get("message"))
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C1"), "m_target")

    def test_resolution_rebuilds_projection(self, *_mocks):
        result = menu_utils.resolve_menu_item_variant(
            self.conn,
            "m_source",
            "variant_small",
            target_menu_item_id="m_target",
            target_variant_id="variant_large",
            emit_sync_event=False,
        )
        self.assertEqual(result["status"], "success", result.get("message"))
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C1"), "m_target")

    def test_assignment_batch_epilogue_rebuilds_projection(self, *_mocks):
        # Simulate a remote assignment apply moving itemid 101 to m_target
        # (what a peer's merge produces), then run the shared epilogue.
        self.conn.execute(
            "UPDATE menu_item_variants SET menu_item_id = 'm_target' WHERE order_item_id = '101'"
        )
        self.conn.execute(
            "UPDATE order_items SET menu_item_id = 'm_target' WHERE order_item_id = 1"
        )
        with patch("src.core.menu_merge_sync.menu_utils._clear_impacted_models", return_value=None):
            _run_assignment_batch_epilogue(self.conn, {"m_source", "m_target"})
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C1"), "m_target")

    def test_strict_capture_rollback_leaks_nothing(self, *_mocks):
        # Strict-ready install whose server rejects the mutation: the capture
        # transaction rolls back and the projection must be untouched.
        self.conn.executemany(
            "INSERT INTO system_config (key, value) VALUES (?, ?)",
            [
                ("cloud_sync_url", "https://server.example"),
                ("cloud_sync_api_key", "k"),
                ("menu_state_revision", "7"),
            ],
        )
        self.conn.commit()

        from src.core.menu_mutation_commit import CommitResult

        rejected = CommitResult(status="conflict", message="menu revision conflict")
        with patch(
            "src.core.menu_mutation_commit.commit_mutation", return_value=rejected
        ) as commit_mock:
            result = menu_utils.merge_menu_items(
                self.conn, "m_source", "m_target", emit_sync_event=True
            )
        self.assertEqual(commit_mock.call_count, 1)
        self.assertNotEqual(result.get("status"), "success")
        # Merge rolled back: source still exists, projection unchanged.
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM menu_items WHERE menu_item_id = 'm_source'"
            ).fetchone()
        )
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C1"), "m_source")
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C2"), "m_target")

    def test_best_effort_rebuild_survives_partial_schema(self, *_mocks):
        conn = sqlite3.connect(":memory:")
        try:
            # No POS order tables at all: lifecycle hook must no-op, not raise.
            self.assertIsNone(rebuild_itemcode_mappings_best_effort(conn))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
