import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.menu_merge_shipper import upload_pending
from src.core.menu_merge_sync import (
    get_menu_merge_pull_cursor,
    pull_and_apply_menu_merge_events,
    set_menu_merge_pull_cursor,
)
from src.core.menu_merge_sync_events import ensure_menu_merge_sync_tables
from src.core.menu_sync_quarantine import (
    dismiss_sync_conflict,
    list_sync_conflicts,
    quarantine_event,
)
from src.core.sync_cursor_migration import (
    SYNC_CURSOR_SCHEMA_VERSION,
    SYNC_CURSOR_SCHEMA_VERSION_KEY,
)
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
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES ('variant_1_piece', '1_PIECE', 1)
            """
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
            VALUES ('1', 'item_source', NULL, 1)
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


    @patch("utils.menu_utils.export_to_backups", return_value=True)
    def test_pull_skips_unappliable_event_and_keeps_going(self, _mock_export) -> None:
        # First event is un-appliable (merging an item into itself); the pull must
        # not halt on it. The second, valid event should still apply and the cursor
        # should advance past the whole page.
        remote_events = [
            {
                "remote_event_id": "remote-bad-1",
                "schema_version": 1,
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:00:00Z",
                "source_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {"kind": "basic_merge_v1"},
            },
            {
                "remote_event_id": "remote-good-1",
                "schema_version": 1,
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:01:00Z",
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
                "merge_payload": {"kind": "basic_merge_v1"},
            },
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": remote_events,
            "next_cursor": "cursor-9",
        }

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        # A per-event failure is not a transport error, so error stays None and the
        # cursor advances so future pulls do not re-fetch the un-appliable event.
        self.assertIsNone(result["error"])
        self.assertEqual(result["events_fetched"], 2)
        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["events_failed"], 1)
        self.assertEqual(result["events_quarantined"], 1)
        self.assertIsNotNone(result["last_event_error"])
        self.assertEqual(result["cursor_after"], "cursor-9")

        # The failed event is quarantined (not silently dropped) for retry/review.
        quarantine_row = self.conn.execute(
            "SELECT stream, error, fail_count, resolved_at FROM menu_sync_event_quarantine"
            " WHERE remote_event_id = 'remote-bad-1'"
        ).fetchone()
        self.assertIsNotNone(quarantine_row)
        self.assertEqual(quarantine_row["stream"], "menu_merge")
        self.assertEqual(int(quarantine_row["fail_count"]), 1)
        self.assertIsNone(quarantine_row["resolved_at"])
        self.assertIn("itself", quarantine_row["error"])

        # The good merge landed; the bad one left no partial state behind.
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-good-1'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-bad-1'"
            ).fetchone()[0],
            0,
        )

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    def test_retry_drains_quarantine_on_next_pull(self, _mock_export) -> None:
        # A quarantined event that has since become appliable is retried at the
        # start of the next pull, applied, and marked resolved.
        event = {
            "remote_event_id": "remote-quarantined-1",
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
            "merge_payload": {"kind": "basic_merge_v1"},
        }
        ensure_menu_merge_sync_tables(self.conn)
        quarantine_event(self.conn, "menu_merge", "remote-quarantined-1", event, "was failing")
        self.conn.commit()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [], "next_cursor": None}

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertEqual(result["quarantine_retried"], 1)
        self.assertEqual(result["quarantine_resolved"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-quarantined-1'"
            ).fetchone()[0],
            1,
        )
        quarantine_row = self.conn.execute(
            "SELECT resolved_at FROM menu_sync_event_quarantine WHERE remote_event_id = 'remote-quarantined-1'"
        ).fetchone()
        self.assertIsNotNone(quarantine_row["resolved_at"])
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()["menu_item_id"],
            "item_target",
        )

    def test_cursor_reset_happens_exactly_once(self) -> None:
        # Pre-migration state: v1 cursors exist and no schema version is recorded.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO system_config (key, value) VALUES (?, ?)",
            [
                ("menu_merge_pull_cursor", "v1-cursor-a"),
                ("menu_mapping_verification_pull_cursor", "v1-cursor-b"),
                ("customer_merge_pull_cursor", "v1-cursor-c"),
            ],
        )
        self.conn.commit()

        # First access migrates: all three cursors are dropped, version recorded.
        self.assertIsNone(get_menu_merge_pull_cursor(self.conn))
        for key in (
            "menu_merge_pull_cursor",
            "menu_mapping_verification_pull_cursor",
            "customer_merge_pull_cursor",
        ):
            row = self.conn.execute(
                "SELECT value FROM system_config WHERE key = ?", (key,)
            ).fetchone()
            self.assertIsNone(row, key)
        version_row = self.conn.execute(
            "SELECT value FROM system_config WHERE key = ?",
            (SYNC_CURSOR_SCHEMA_VERSION_KEY,),
        ).fetchone()
        self.assertEqual(version_row["value"], SYNC_CURSOR_SCHEMA_VERSION)

        # Once migrated, newly stored cursors survive subsequent accesses.
        set_menu_merge_pull_cursor(self.conn, "v2-cursor")
        self.conn.commit()
        self.assertEqual(get_menu_merge_pull_cursor(self.conn), "v2-cursor")

    def test_sync_conflicts_listing_and_dismiss(self) -> None:
        ensure_menu_merge_sync_tables(self.conn)
        quarantine_event(
            self.conn,
            "menu_merge",
            "remote-conflict-1",
            {
                "remote_event_id": "remote-conflict-1",
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:00:00Z",
                "source_item": {"menu_item_id": "item_source", "name": "Iced Coffee"},
                "target_item": {"menu_item_id": "item_target", "name": "Cold Coffee"},
            },
            "Source variant was not found",
        )
        self.conn.commit()

        conflicts = list_sync_conflicts(self.conn)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["remote_event_id"], "remote-conflict-1")
        self.assertEqual(conflicts[0]["summary"]["source_name"], "Iced Coffee")
        self.assertEqual(conflicts[0]["summary"]["target_name"], "Cold Coffee")

        self.assertTrue(dismiss_sync_conflict(self.conn, "remote-conflict-1"))
        self.conn.commit()
        self.assertEqual(list_sync_conflicts(self.conn), [])
        # Dismissing an unknown/already-resolved conflict reports failure.
        self.assertFalse(dismiss_sync_conflict(self.conn, "remote-conflict-1"))

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    def test_pull_backfills_missing_source_item(self, _mock_export) -> None:
        # Source item is absent locally (already merged away on this device); the
        # pull should recreate it from the event snapshot and apply the merge.
        self.conn.execute("DELETE FROM order_items WHERE menu_item_id = 'item_source'")
        self.conn.execute("DELETE FROM menu_item_variants WHERE menu_item_id = 'item_source'")
        self.conn.execute("DELETE FROM menu_items WHERE menu_item_id = 'item_source'")
        self.conn.commit()

        remote_events = [
            {
                "remote_event_id": "remote-backfill-1",
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
                "merge_payload": {"kind": "basic_merge_v1"},
            }
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": remote_events, "next_cursor": "cursor-3"}

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertIsNone(result["error"])
        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["events_failed"], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-backfill-1'"
            ).fetchone()[0],
            1,
        )


class MenuMergeShipperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        # upload_pending backfills from merge_history before selecting events.
        self.conn.execute(
            """
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
        ensure_menu_merge_sync_tables(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _insert_event(self, event_id: str, payload: str, occurred_at: str) -> None:
        self.conn.execute(
            """
            INSERT INTO menu_merge_sync_events (event_id, merge_id, event_type, payload, occurred_at)
            VALUES (?, NULL, 'menu_merge.applied', ?, ?)
            """,
            (event_id, payload, occurred_at),
        )
        self.conn.commit()

    def _event_row(self, event_id: str):
        return self.conn.execute(
            "SELECT uploaded_at, last_error FROM menu_merge_sync_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()

    def test_upload_pending_mixed_accepted_rejected_batch(self) -> None:
        self._insert_event(
            "event-ok",
            json.dumps({"remote_event_id": "event-ok", "event_type": "menu_merge.applied"}),
            "2026-04-14T10:00:00Z",
        )
        self._insert_event(
            "event-bad",
            json.dumps({"remote_event_id": "event-bad", "event_type": "menu_merge.applied"}),
            "2026-04-14T10:01:00Z",
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "ingested_count": 1,
            "duplicate_count": 0,
            "accepted": ["event-ok"],
            "rejected": [{"remote_event_id": "event-bad", "error": "Use a valid datetime value."}],
        }

        with patch("requests.post", return_value=mock_response):
            result = upload_pending(self.conn, endpoint="https://cloud.example.com/ingest")

        self.assertIsNone(result["error"])
        self.assertEqual(result["events_sent"], 1)
        self.assertEqual(result["events_rejected"], 1)

        accepted_row = self._event_row("event-ok")
        self.assertIsNotNone(accepted_row["uploaded_at"])
        self.assertIsNone(accepted_row["last_error"])

        # The rejected event leaves the push queue (uploaded_at set) but keeps
        # the server error and lands in quarantine for surfacing.
        rejected_row = self._event_row("event-bad")
        self.assertIsNotNone(rejected_row["uploaded_at"])
        self.assertEqual(rejected_row["last_error"], "Use a valid datetime value.")
        quarantine_row = self.conn.execute(
            "SELECT stream, error, resolved_at FROM menu_sync_event_quarantine WHERE remote_event_id = 'event-bad'"
        ).fetchone()
        self.assertIsNotNone(quarantine_row)
        self.assertEqual(quarantine_row["stream"], "menu_merge_push")
        self.assertEqual(quarantine_row["error"], "Use a valid datetime value.")
        self.assertIsNone(quarantine_row["resolved_at"])

    def test_upload_pending_old_server_response_marks_all_uploaded(self) -> None:
        self._insert_event(
            "event-1",
            json.dumps({"remote_event_id": "event-1", "event_type": "menu_merge.applied"}),
            "2026-04-14T10:00:00Z",
        )
        self._insert_event(
            "event-2",
            json.dumps({"remote_event_id": "event-2", "event_type": "menu_merge.applied"}),
            "2026-04-14T10:01:00Z",
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "ingested_count": 2, "duplicate_count": 0}

        with patch("requests.post", return_value=mock_response):
            result = upload_pending(self.conn, endpoint="https://cloud.example.com/ingest")

        self.assertIsNone(result["error"])
        self.assertEqual(result["events_sent"], 2)
        for event_id in ("event-1", "event-2"):
            row = self._event_row(event_id)
            self.assertIsNotNone(row["uploaded_at"])
            self.assertIsNone(row["last_error"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM menu_sync_event_quarantine").fetchone()[0],
            0,
        )

    def test_upload_pending_marks_unparseable_local_payload_errored(self) -> None:
        self._insert_event("event-corrupt", "{not-valid-json", "2026-04-14T10:00:00Z")

        with patch("requests.post") as mock_post:
            result = upload_pending(self.conn, endpoint="https://cloud.example.com/ingest")

        # Nothing shippable, so no request goes out — but the corrupt row is
        # marked errored so it stops blocking the queue.
        mock_post.assert_not_called()
        self.assertIsNone(result["error"])
        self.assertEqual(result["events_sent"], 0)
        row = self._event_row("event-corrupt")
        self.assertIsNotNone(row["uploaded_at"])
        self.assertIn("not valid JSON", row["last_error"])


if __name__ == "__main__":
    unittest.main()
