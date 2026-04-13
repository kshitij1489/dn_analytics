import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.menu_merge_sync import pull_and_apply_menu_merge_events
from utils import menu_utils


class MenuMergeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
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
                is_verified BOOLEAN DEFAULT 0,
                updated_at TEXT
            );

            CREATE TABLE menu_item_variants (
                order_item_id INTEGER PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
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
        )
        self.conn.execute(
            """
            INSERT INTO orders (order_id, order_status)
            VALUES (1, 'Success')
            """
        )
        self.conn.executemany(
            """
            INSERT INTO menu_items (
                menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("item_source", "Iced Coffee", "Beverage", 1, 4, 480.0, 4, 0),
                ("item_target", "Cold Coffee", "Beverage", 1, 7, 910.0, 7, 0),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO order_items (order_id, menu_item_id, quantity, total_price, name_raw)
            VALUES (1, 'item_source', 2, 240.0, 'Iced Coffee')
            """
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (1, 'item_source', NULL, 1)
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    def test_local_merge_and_undo_record_menu_sync_events(self, _mock_export) -> None:
        merge_result = menu_utils.merge_menu_items(self.conn, "item_source", "item_target")
        self.assertEqual(merge_result["status"], "success")
        self.assertIsNotNone(merge_result["merge_id"])

        applied_event = self.conn.execute(
            """
            SELECT event_type, payload
            FROM menu_merge_sync_events
            WHERE merge_id = ?
            """,
            (merge_result["merge_id"],),
        ).fetchone()
        self.assertEqual(applied_event["event_type"], "menu_merge.applied")
        applied_payload = json.loads(applied_event["payload"])
        self.assertEqual(applied_payload["source_item"]["menu_item_id"], "item_source")
        self.assertTrue(applied_payload["attribution"]["device"]["install_id"].startswith("install-"))

        undo_result = menu_utils.undo_merge(self.conn, merge_result["merge_id"])
        self.assertEqual(undo_result["status"], "success")

        rows = self.conn.execute(
            "SELECT event_type, payload FROM menu_merge_sync_events ORDER BY created_at ASC"
        ).fetchall()
        self.assertEqual([row["event_type"] for row in rows], ["menu_merge.applied", "menu_merge.undone"])
        undo_payload = json.loads(rows[1]["payload"])
        self.assertEqual(undo_payload["reverts_remote_event_id"], applied_payload["remote_event_id"])

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    def test_pull_applies_and_undoes_remote_menu_merge_events(self, _mock_export) -> None:
        remote_events = [
            {
                "remote_event_id": "remote-menu-merge-1",
                "schema_version": 1,
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:00:00Z",
                "source_item": {
                    "menu_item_id": "item_source",
                    "name": "Iced Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {
                    "kind": "basic_merge_v1",
                },
            },
            {
                "remote_event_id": "remote-menu-merge-undo-1",
                "schema_version": 1,
                "event_type": "menu_merge.undone",
                "occurred_at": "2026-04-14T10:05:00Z",
                "reverts_remote_event_id": "remote-menu-merge-1",
                "source_item": {
                    "menu_item_id": "item_source",
                    "name": "Iced Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {
                    "kind": "basic_merge_v1",
                },
            },
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": remote_events,
            "next_cursor": "cursor-2",
        }

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertIsNone(result["error"])
        self.assertEqual(result["events_fetched"], 2)
        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["undo_events_applied"], 1)
        self.assertEqual(result["events_skipped"], 0)
        self.assertEqual(result["cursor_after"], "cursor-2")

        restored_item = self.conn.execute(
            "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
        ).fetchone()
        self.assertEqual(restored_item["menu_item_id"], "item_source")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM menu_merge_remote_events").fetchone()[0],
            2,
        )


if __name__ == "__main__":
    unittest.main()
