import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.menu_mapping_verification_shipper import upload_pending
from src.core.menu_mapping_verification_sync import pull_and_apply_menu_mapping_verification_events
from src.core.menu_mapping_verification_sync_events import (
    record_menu_mapping_verification_events,
    record_menu_mapping_verification_events_chunked,
)
from src.core.menu_sync_quarantine import quarantine_event


class MenuMappingVerificationSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                is_verified BOOLEAN DEFAULT 0,
                updated_at TEXT
            );
            """
        )
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES ('m1', 'Coffee', 'Bev', 0)"
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES ('oid-1', 'm1', 'v1', 0)
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_record_and_pull_applies_verified_by_order_item_id(self) -> None:
        record_menu_mapping_verification_events(
            self.conn,
            [{"order_item_id": "oid-1", "menu_item_id": "m1", "variant_id": "v1", "is_verified": 1}],
        )
        self.conn.commit()

        event = json.loads(
            self.conn.execute(
                "SELECT payload FROM menu_mapping_verification_sync_events LIMIT 1"
            ).fetchone()[0]
        )
        self.assertEqual(event["event_type"], "mapping.verified")

        def fake_get(url, headers=None, params=None, timeout=60):
            m = Mock()
            m.status_code = 200
            m.json.return_value = {"events": [event], "next_cursor": "c1"}
            return m

        with patch("requests.get", side_effect=fake_get):
            stats = pull_and_apply_menu_mapping_verification_events(
                self.conn, "http://example.test/mv", auth="k", limit=10
            )
        self.assertIsNone(stats.get("error"))
        self.assertEqual(stats["events_applied"], 1)
        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id = ?",
            ("oid-1",),
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)

    def test_pull_idempotent(self) -> None:
        record_menu_mapping_verification_events(
            self.conn,
            [{"order_item_id": "oid-1", "menu_item_id": "m1", "variant_id": "v1", "is_verified": 1}],
        )
        self.conn.commit()
        event = json.loads(
            self.conn.execute(
                "SELECT payload FROM menu_mapping_verification_sync_events LIMIT 1"
            ).fetchone()[0]
        )

        def fake_get(url, headers=None, params=None, timeout=60):
            m = Mock()
            m.status_code = 200
            m.json.return_value = {"events": [event], "next_cursor": "c1"}
            return m

        with patch("requests.get", side_effect=fake_get):
            pull_and_apply_menu_mapping_verification_events(self.conn, "http://example.test/mv", auth="k")
            pull_and_apply_menu_mapping_verification_events(self.conn, "http://example.test/mv", auth="k")

        applied = self.conn.execute("SELECT COUNT(*) FROM menu_mapping_verification_remote_events").fetchone()[0]
        self.assertEqual(applied, 1)

    def test_deferred_then_flush_after_row_exists(self) -> None:
        event = {
            "remote_event_id": "r1",
            "schema_version": 1,
            "event_type": "mapping.verified",
            "occurred_at": "2026-01-01T00:00:00Z",
            "order_item_id": "oid-future",
            "menu_item_id": "m1",
            "variant_id": "v1",
            "is_verified": 1,
        }

        def fake_get(url, headers=None, params=None, timeout=60):
            m = Mock()
            m.status_code = 200
            m.json.return_value = {"events": [event], "next_cursor": "c2"}
            return m

        with patch("requests.get", side_effect=fake_get):
            pull_and_apply_menu_mapping_verification_events(self.conn, "http://example.test/mv", auth="k")

        dcount = self.conn.execute("SELECT COUNT(*) FROM menu_mapping_verification_deferred").fetchone()[0]
        self.assertEqual(dcount, 1)

        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES ('oid-future', 'm1', 'v1', 0)
            """
        )
        self.conn.commit()

        from src.core.menu_mapping_verification_sync import flush_deferred_menu_mapping_verifications

        flush_deferred_menu_mapping_verifications(self.conn)
        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id = ?",
            ("oid-future",),
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)

    def test_upload_pending_marks_uploaded(self) -> None:
        record_menu_mapping_verification_events_chunked(
            self.conn,
            [{"order_item_id": "oid-1", "menu_item_id": "m1", "variant_id": "v1", "is_verified": 1}],
        )
        self.conn.commit()

        mock_resp = Mock()
        mock_resp.status_code = 200

        with patch("requests.post", return_value=mock_resp) as post:
            result = upload_pending(self.conn, endpoint="http://example.test/ingest", auth="secret")

        self.assertEqual(result["events_sent"], 1)
        self.assertIsNone(result["error"])
        post.assert_called_once()
        uploaded = self.conn.execute(
            "SELECT uploaded_at FROM menu_mapping_verification_sync_events LIMIT 1"
        ).fetchone()[0]
        self.assertIsNotNone(uploaded)
    def test_poison_event_is_quarantined_and_cursor_advances(self) -> None:
        # One bad event must not abort the pull without advancing the cursor
        # (the old failure mode); it is quarantined and the stream keeps moving.
        poison_event = {
            "remote_event_id": "poison-1",
            "schema_version": 1,
            "event_type": "mapping.bogus",
            "occurred_at": "2026-01-01T00:00:00Z",
        }
        good_event = {
            "remote_event_id": "good-1",
            "schema_version": 1,
            "event_type": "mapping.verified",
            "occurred_at": "2026-01-01T00:01:00Z",
            "order_item_id": "oid-1",
            "menu_item_id": "m1",
            "variant_id": "v1",
            "is_verified": 1,
        }

        def fake_get(url, headers=None, params=None, timeout=60):
            m = Mock()
            m.status_code = 200
            m.json.return_value = {"events": [poison_event, good_event], "next_cursor": "c9"}
            return m

        with patch("requests.get", side_effect=fake_get):
            stats = pull_and_apply_menu_mapping_verification_events(
                self.conn, "http://example.test/mv", auth="k", limit=10
            )

        self.assertIsNone(stats.get("error"))
        self.assertEqual(stats["events_applied"], 1)
        self.assertEqual(stats["events_failed"], 1)
        self.assertEqual(stats["events_quarantined"], 1)
        self.assertEqual(stats["cursor_after"], "c9")

        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id = ?",
            ("oid-1",),
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)

        quarantine_row = self.conn.execute(
            "SELECT stream, error, resolved_at FROM menu_sync_event_quarantine"
            " WHERE remote_event_id = 'poison-1'"
        ).fetchone()
        self.assertIsNotNone(quarantine_row)
        self.assertEqual(quarantine_row["stream"], "mapping_verification")
        self.assertIsNone(quarantine_row["resolved_at"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_mapping_verification_remote_events"
                " WHERE remote_event_id = 'poison-1'"
            ).fetchone()[0],
            0,
        )

    def test_retry_drains_quarantine_on_next_pull(self) -> None:
        event = {
            "remote_event_id": "quarantined-1",
            "schema_version": 1,
            "event_type": "mapping.verified",
            "occurred_at": "2026-01-01T00:00:00Z",
            "order_item_id": "oid-1",
            "menu_item_id": "m1",
            "variant_id": "v1",
            "is_verified": 1,
        }
        quarantine_event(self.conn, "mapping_verification", "quarantined-1", event, "was failing")
        self.conn.commit()

        def fake_get(url, headers=None, params=None, timeout=60):
            m = Mock()
            m.status_code = 200
            m.json.return_value = {"events": [], "next_cursor": None}
            return m

        with patch("requests.get", side_effect=fake_get):
            stats = pull_and_apply_menu_mapping_verification_events(
                self.conn, "http://example.test/mv", auth="k"
            )

        self.assertEqual(stats["quarantine_retried"], 1)
        self.assertEqual(stats["quarantine_resolved"], 1)
        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id = ?",
            ("oid-1",),
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)
        quarantine_row = self.conn.execute(
            "SELECT resolved_at FROM menu_sync_event_quarantine WHERE remote_event_id = 'quarantined-1'"
        ).fetchone()
        self.assertIsNotNone(quarantine_row["resolved_at"])

    def test_upload_pending_mixed_accepted_rejected_batch(self) -> None:
        record_menu_mapping_verification_events(
            self.conn,
            [{"order_item_id": "oid-1", "menu_item_id": "m1", "variant_id": "v1", "is_verified": 1}],
        )
        record_menu_mapping_verification_events(
            self.conn,
            [{"order_item_id": "oid-2", "menu_item_id": "m1", "variant_id": "v1", "is_verified": 1}],
        )
        self.conn.commit()
        event_ids = [
            str(row[0])
            for row in self.conn.execute(
                "SELECT event_id FROM menu_mapping_verification_sync_events ORDER BY created_at ASC, event_id ASC"
            ).fetchall()
        ]
        accepted_id, rejected_id = event_ids

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "accepted": [accepted_id],
            "rejected": [{"remote_event_id": rejected_id, "error": "boom"}],
        }

        with patch("requests.post", return_value=mock_resp):
            result = upload_pending(self.conn, endpoint="http://example.test/ingest", auth="secret")

        self.assertIsNone(result["error"])
        self.assertEqual(result["events_sent"], 1)
        self.assertEqual(result["events_rejected"], 1)

        rejected_row = self.conn.execute(
            "SELECT uploaded_at, last_error FROM menu_mapping_verification_sync_events WHERE event_id = ?",
            (rejected_id,),
        ).fetchone()
        self.assertIsNotNone(rejected_row["uploaded_at"])
        self.assertEqual(rejected_row["last_error"], "boom")
        quarantine_row = self.conn.execute(
            "SELECT stream FROM menu_sync_event_quarantine WHERE remote_event_id = ?",
            (rejected_id,),
        ).fetchone()
        self.assertEqual(quarantine_row["stream"], "mapping_verification_push")

    def test_bulk_verified_partial_apply(self) -> None:
        """Bulk verified events should apply existing rows immediately and defer the event
        for missing rows, rather than blocking all rows."""
        event = {
            "remote_event_id": "bulk-partial-1",
            "schema_version": 1,
            "event_type": "mapping.bulk_verified",
            "occurred_at": "2026-01-01T00:00:00Z",
            "mappings": [
                {"order_item_id": "oid-1", "menu_item_id": "m1", "variant_id": "v1", "is_verified": 1},
                {"order_item_id": "oid-missing", "menu_item_id": "m1", "variant_id": "v1", "is_verified": 1},
            ],
        }

        def fake_get(url, headers=None, params=None, timeout=60):
            m = Mock()
            m.status_code = 200
            m.json.return_value = {"events": [event], "next_cursor": "c3"}
            return m

        with patch("requests.get", side_effect=fake_get):
            stats = pull_and_apply_menu_mapping_verification_events(
                self.conn, "http://example.test/mv", auth="k", limit=10
            )

        self.assertIsNone(stats.get("error"))
        self.assertEqual(stats["events_applied"], 1)
        self.assertEqual(stats["deferred"], 1)

        # oid-1 should already be verified (partial apply)
        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id = ?",
            ("oid-1",),
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)

        # deferred table should have the event for retry
        dcount = self.conn.execute(
            "SELECT COUNT(*) FROM menu_mapping_verification_deferred"
        ).fetchone()[0]
        self.assertEqual(dcount, 1)

        # Now add the missing row and flush
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES ('oid-missing', 'm1', 'v1', 0)
            """
        )
        self.conn.commit()

        from src.core.menu_mapping_verification_sync import flush_deferred_menu_mapping_verifications

        flush_deferred_menu_mapping_verifications(self.conn)
        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id = ?",
            ("oid-missing",),
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)


if __name__ == "__main__":
    unittest.main()
