"""Phase 6 rollout: legacy outbox drain and non-strict client behavior."""

import sqlite3
import unittest
from unittest.mock import Mock, patch

import utils.menu_utils as menu_utils

from src.core.menu_merge_sync_events import ensure_menu_merge_sync_tables
from src.core.menu_outbox_drain import drain_menu_outbox, get_menu_outbox_status
from src.core.menu_mutation_commit import strict_mode_active
from src.core.menu_assignment_schema import ensure_assignment_sync_schema
from src.core.sync_identity import set_menu_state_revision, set_menu_strict_mode_enabled


class MenuOutboxDrainTests(unittest.TestCase):
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_menu_merge_sync_tables(conn)
        conn.executescript(
            """
            CREATE TABLE menu_mapping_verification_sync_events (
                event_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                upload_attempted_at TEXT,
                uploaded_at TEXT,
                last_error TEXT
            );
            """
        )
        conn.commit()
        return conn

    def test_status_reports_unsent_counts(self) -> None:
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO menu_merge_sync_events (event_id, event_type, payload, occurred_at)
            VALUES ('evt-1', 'menu_merge.applied', '{"remote_event_id":"evt-1"}', '2026-07-06T10:00:00Z')
            """
        )
        conn.commit()
        status = get_menu_outbox_status(conn)
        self.assertEqual(status["menu_merge_unsent"], 1)
        self.assertEqual(status["menu_mapping_verification_unsent"], 0)
        self.assertFalse(status["outbox_drained"])
        conn.close()

    @patch("src.core.menu_outbox_drain.get_cloud_sync_config", return_value=(None, None))
    def test_drain_errors_without_cloud_config(self, _cfg) -> None:
        conn = self._conn()
        result = drain_menu_outbox(conn)
        self.assertEqual(result["status"], "error")
        self.assertIn("Cloud sync URL", result["message"])
        conn.close()

    @patch("src.core.menu_outbox_drain.upload_verification_events", return_value={"events_sent": 0})
    @patch("src.core.menu_outbox_drain.upload_merge_events")
    @patch("src.core.menu_outbox_drain.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    def test_drain_uploads_until_empty(self, mock_cfg, mock_merge, mock_verification) -> None:
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO menu_merge_sync_events (event_id, event_type, payload, occurred_at)
            VALUES ('evt-1', 'menu_merge.applied', '{"remote_event_id":"evt-1"}', '2026-07-06T10:00:00Z')
            """
        )
        conn.commit()

        def _merge_upload(conn, **kwargs):
            conn.execute(
                "UPDATE menu_merge_sync_events SET uploaded_at = '2026-07-06T11:00:00Z' WHERE event_id = 'evt-1'"
            )
            conn.commit()
            return {"events_sent": 1, "backfilled_applied": 0, "error": None}

        mock_merge.side_effect = _merge_upload
        result = drain_menu_outbox(conn)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["outbox_drained"])
        self.assertEqual(result["menu_merges_sent"], 1)
        conn.close()


class NonStrictLegacyBehaviorTests(unittest.TestCase):
    """Phase 6.2: mirrored revision + cloud config but strict flag off → legacy path."""

    def _create_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, order_status TEXT NOT NULL);
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
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                is_verified BOOLEAN DEFAULT 1,
                pending_local INTEGER DEFAULT 0,
                assignment_seq INTEGER,
                updated_at TEXT
            );
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                order_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT,
                quantity INTEGER DEFAULT 1,
                total_price REAL DEFAULT 0,
                name_raw TEXT,
                updated_at TEXT
            );
            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT
            );
            CREATE TABLE merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                affected_order_items TEXT NOT NULL,
                merged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                origin TEXT
            );
            """
        )
        conn.execute("INSERT INTO orders (order_id, order_status) VALUES (1, 'Success')")
        conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon) VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
            [
                ("item_source", "Iced Coffee", "Beverage", 4, 480.0, 4, 0),
                ("item_target", "Cold Coffee", "Beverage", 7, 910.0, 7, 0),
            ],
        )
        conn.execute(
            "INSERT INTO menu_item_variants (order_item_id, menu_item_id, is_verified) VALUES ('1', 'item_source', 1)"
        )
        conn.execute(
            "INSERT INTO order_items (order_id, menu_item_id, quantity, total_price, name_raw) VALUES (1, 'item_source', 2, 240.0, 'Iced Coffee')"
        )
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example'), ('cloud_sync_api_key', 'secret')"
        )
        set_menu_state_revision(conn, 42)
        set_menu_strict_mode_enabled(conn, False)
        conn.commit()
        return conn

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    @patch("src.core.menu_merge_push_nudge.nudge_menu_merge_push_async")
    @patch("src.core.menu_mutation_commit.commit_mutation")
    def test_merge_uses_legacy_path_when_strict_flag_off(
        self, mock_commit, mock_nudge, _models, _export
    ) -> None:
        conn = self._create_db()
        ensure_menu_merge_sync_tables(conn)
        ensure_assignment_sync_schema(conn)
        try:
            self.assertFalse(strict_mode_active(conn))
            result = menu_utils.merge_menu_items(conn, "item_source", "item_target")
            self.assertEqual(result["status"], "success")
            mock_commit.assert_not_called()
            mock_nudge.assert_called_once()
            outbox_count = conn.execute("SELECT COUNT(*) FROM menu_merge_sync_events").fetchone()[0]
            self.assertEqual(outbox_count, 1)
            row = conn.execute(
                "SELECT menu_item_id, pending_local FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(row["menu_item_id"], "item_target")
            self.assertEqual(int(row["pending_local"] or 0), 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
