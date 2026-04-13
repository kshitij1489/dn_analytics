import hashlib
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.customer_merge_sync import pull_and_apply_customer_merge_events
from src.core.customer_merge_sync_events import backfill_customer_merge_sync_events


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CustomerMergePullTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                customer_identity_key TEXT,
                name TEXT,
                name_normalized TEXT,
                phone TEXT,
                address TEXT,
                gstin TEXT,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                first_order_date TEXT,
                last_order_date TEXT,
                is_verified BOOLEAN NOT NULL DEFAULT 0,
                updated_at TEXT
            );

            CREATE TABLE customer_addresses (
                address_id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                label TEXT,
                address_line_1 TEXT,
                address_line_2 TEXT,
                city TEXT,
                state TEXT,
                postal_code TEXT,
                country TEXT,
                is_default BOOLEAN DEFAULT 0,
                updated_at TEXT
            );

            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                petpooja_order_id TEXT,
                stream_id INTEGER,
                event_id TEXT,
                aggregate_id TEXT,
                total REAL NOT NULL DEFAULT 0,
                created_on TEXT,
                updated_at TEXT
            );

            CREATE TABLE customer_merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_customer_id INTEGER NOT NULL,
                target_customer_id INTEGER NOT NULL,
                similarity_score REAL,
                model_name TEXT,
                suggestion_context TEXT,
                source_snapshot TEXT,
                target_snapshot TEXT,
                moved_order_ids TEXT,
                copied_address_count INTEGER DEFAULT 0,
                merged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                undone_at TEXT,
                undo_context TEXT
            );

            CREATE TABLE customer_merge_sync_events (
                event_id TEXT PRIMARY KEY,
                merge_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                upload_attempted_at TEXT,
                uploaded_at TEXT,
                last_error TEXT,
                UNIQUE (merge_id, event_type)
            );
            """
        )
        self.conn.executemany(
            """
            INSERT INTO customers (
                customer_id,
                customer_identity_key,
                name,
                name_normalized,
                phone,
                address,
                gstin,
                total_orders,
                total_spent,
                first_order_date,
                last_order_date,
                is_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "phone:source",
                    "Rahul Sharma",
                    "rahul sharma",
                    "9999999999",
                    "HSR Layout",
                    None,
                    1,
                    80.0,
                    "2024-02-03 10:00:00",
                    "2024-02-03 10:00:00",
                    0,
                ),
                (
                    2,
                    "addr:target",
                    "Rahul S.",
                    "rahul s.",
                    None,
                    "HSR Layout",
                    None,
                    1,
                    120.0,
                    "2024-02-04 10:00:00",
                    "2024-02-04 10:00:00",
                    0,
                ),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO customer_addresses (
                customer_id,
                label,
                address_line_1,
                city,
                state,
                postal_code,
                country,
                is_default
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Primary", "HSR Layout", "Bengaluru", "KA", "560102", "IN", 1),
                (2, "Primary", "HSR Layout", "Bengaluru", "KA", "560102", "IN", 1),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO orders (
                order_id,
                customer_id,
                petpooja_order_id,
                stream_id,
                event_id,
                aggregate_id,
                total,
                created_on
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (101, 1, "PP-101", 5001, "evt-101", "agg-101", 80.0, "2024-02-03 10:00:00"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _remote_applied_event(self) -> dict:
        return {
            "remote_event_id": "remote-merge-1",
            "schema_version": 1,
            "event_type": "customer_merge.applied",
            "occurred_at": "2024-02-05 12:00:00",
            "source_customer": {
                "snapshot": {
                    "name": "Rahul Sharma",
                    "phone": "9999999999",
                    "address": "HSR Layout",
                    "gstin": None,
                    "total_orders": 1,
                    "total_spent": 80.0,
                    "last_order_date": "2024-02-03 10:00:00",
                    "is_verified": False,
                },
                "portable_locators": {
                    "customer_identity_key": "phone:source",
                    "phone_hash": _sha("9999999999"),
                    "name_address_hash": _sha("rahul sharma|hsr layout"),
                    "name_normalized": "rahul sharma",
                    "address_normalized": "hsr layout",
                    "address_book_hashes": [_sha("hsr layout, bengaluru, ka, 560102, in")],
                },
            },
            "target_customer": {
                "snapshot": {
                    "name": "Rahul S.",
                    "phone": None,
                    "address": "HSR Layout",
                    "gstin": None,
                    "total_orders": 1,
                    "total_spent": 120.0,
                    "last_order_date": "2024-02-04 10:00:00",
                    "is_verified": False,
                },
                "portable_locators": {
                    "customer_identity_key": "addr:target",
                    "phone_hash": None,
                    "name_address_hash": _sha("rahul s.|hsr layout"),
                    "name_normalized": "rahul s.",
                    "address_normalized": "hsr layout",
                    "address_book_hashes": [_sha("hsr layout, bengaluru, ka, 560102, in")],
                },
            },
            "merge_metadata": {
                "similarity_score": 0.98,
                "model_name": "duplicate_matcher_v1",
                "reasons": ["phone exact match"],
                "copied_address_count": 0,
                "target_before_fields": {
                    "phone": None,
                    "address": "HSR Layout",
                    "gstin": None,
                    "is_verified": False,
                },
                "target_is_verified_after_merge": True,
                "mark_target_verified": True,
            },
            "moved_orders": {
                "count": 1,
                "portable_refs": [
                    {
                        "petpooja_order_id": "PP-101",
                        "stream_id": 5001,
                        "event_id": "evt-101",
                        "aggregate_id": "agg-101",
                        "created_on": "2024-02-03 10:00:00",
                        "total": 80.0,
                    }
                ],
            },
            "local_refs": {
                "merge_id": 999,
                "source_customer_id": 11,
                "target_customer_id": 22,
                "moved_order_ids": [444],
                "inserted_target_address_ids": [],
                "removed_target_address_ids": [],
            },
        }

    def _remote_undo_event(self) -> dict:
        applied = self._remote_applied_event()
        return {
            "remote_event_id": "remote-undo-1",
            "schema_version": 1,
            "event_type": "customer_merge.undone",
            "occurred_at": "2024-02-05 13:00:00",
            "reverts_remote_event_id": applied["remote_event_id"],
            "source_customer": applied["source_customer"],
            "target_customer": applied["target_customer"],
            "merge_metadata": applied["merge_metadata"],
            "undo_metadata": {
                "restored_order_count": 1,
                "restored_target_fields": ["address", "gstin", "is_verified", "phone"],
                "original_merged_at": applied["occurred_at"],
            },
            "moved_orders": applied["moved_orders"],
            "local_refs": applied["local_refs"],
        }

    @patch("requests.get")
    def test_pull_applies_remote_merge_and_does_not_create_outbound_event(self, mock_get: Mock) -> None:
        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"events": [self._remote_applied_event()], "next_cursor": "cursor-1"}),
        )

        result = pull_and_apply_customer_merge_events(
            self.conn,
            endpoint="https://cloud.example.com/desktop-analytics-sync/customer-merges",
            auth="secret-token",
        )

        self.assertEqual(result["events_fetched"], 1)
        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["undo_events_applied"], 0)
        self.assertEqual(result["events_skipped"], 0)
        self.assertEqual(result["cursor_after"], "cursor-1")

        order_row = self.conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()
        self.assertEqual(int(order_row["customer_id"]), 2)

        merge_row = self.conn.execute("SELECT * FROM customer_merge_history").fetchone()
        self.assertIsNotNone(merge_row)
        self.assertEqual(merge_row["merged_at"], "2024-02-05 12:00:00")
        suggestion_context = json.loads(merge_row["suggestion_context"])
        self.assertEqual(suggestion_context["sync_origin"], "cloud_pull")
        self.assertEqual(suggestion_context["remote_event_id"], "remote-merge-1")

        remote_row = self.conn.execute(
            "SELECT remote_event_id, local_merge_id FROM customer_merge_remote_events WHERE remote_event_id = ?",
            ("remote-merge-1",),
        ).fetchone()
        self.assertIsNotNone(remote_row)
        self.assertEqual(int(remote_row["local_merge_id"]), int(merge_row["merge_id"]))

        cursor_row = self.conn.execute(
            "SELECT value FROM system_config WHERE key = 'customer_merge_pull_cursor'"
        ).fetchone()
        self.assertEqual(cursor_row["value"], "cursor-1")

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_merge_sync_events").fetchone()[0],
            0,
        )
        self.assertEqual(backfill_customer_merge_sync_events(self.conn), {"applied": 0, "undone": 0})
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_merge_sync_events").fetchone()[0],
            0,
        )

    @patch("requests.get")
    def test_pull_applies_remote_merge_then_remote_undo_without_echoing(self, mock_get: Mock) -> None:
        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "events": [self._remote_applied_event(), self._remote_undo_event()],
                    "next_cursor": "cursor-2",
                }
            ),
        )

        result = pull_and_apply_customer_merge_events(
            self.conn,
            endpoint="https://cloud.example.com/desktop-analytics-sync/customer-merges",
        )

        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["undo_events_applied"], 1)
        self.assertEqual(result["events_skipped"], 0)
        self.assertEqual(result["cursor_after"], "cursor-2")

        order_row = self.conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()
        self.assertEqual(int(order_row["customer_id"]), 1)

        merge_row = self.conn.execute("SELECT * FROM customer_merge_history").fetchone()
        self.assertEqual(merge_row["undone_at"], "2024-02-05 13:00:00")
        undo_context = json.loads(merge_row["undo_context"])
        self.assertEqual(undo_context["sync_origin"], "cloud_pull")
        self.assertEqual(undo_context["remote_event_id"], "remote-undo-1")

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_merge_remote_events").fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_merge_sync_events").fetchone()[0],
            0,
        )
        self.assertEqual(backfill_customer_merge_sync_events(self.conn), {"applied": 0, "undone": 0})

    @patch("requests.get")
    def test_pull_skips_duplicate_remote_event(self, mock_get: Mock) -> None:
        payload = {"events": [self._remote_applied_event()], "next_cursor": "cursor-1"}
        mock_get.return_value = Mock(status_code=200, json=Mock(return_value=payload))

        first_result = pull_and_apply_customer_merge_events(
            self.conn,
            endpoint="https://cloud.example.com/desktop-analytics-sync/customer-merges",
            cursor="cursor-0",
        )
        second_result = pull_and_apply_customer_merge_events(
            self.conn,
            endpoint="https://cloud.example.com/desktop-analytics-sync/customer-merges",
            cursor="cursor-0",
        )

        self.assertEqual(first_result["merge_events_applied"], 1)
        self.assertEqual(second_result["merge_events_applied"], 0)
        self.assertEqual(second_result["events_skipped"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_merge_history").fetchone()[0],
            1,
        )

    @patch("requests.get")
    def test_pull_skips_self_origin_event_before_ambiguous_customer_resolution(self, mock_get: Mock) -> None:
        self.conn.executemany(
            """
            INSERT INTO customers (
                customer_id,
                customer_identity_key,
                name,
                name_normalized,
                phone,
                address,
                gstin,
                total_orders,
                total_spent,
                first_order_date,
                last_order_date,
                is_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (10, "anon:source-a", "Nirupam Das", "nirupam das", None, None, None, 1, 666.0, None, "2026-02-13 22:43:46", 0),
                (20, "anon:target-a", "Nirupam Das", "nirupam das", None, None, None, 1, 740.0, None, "2025-12-14 21:05:18", 0),
                (30, "anon:other-a", "Nirupam Das", "nirupam das", None, None, None, 1, 100.0, None, None, 0),
                (40, "anon:other-b", "Nirupam Das", "nirupam das", None, None, None, 1, 120.0, None, None, 0),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO customer_merge_history (
                merge_id,
                source_customer_id,
                target_customer_id,
                similarity_score,
                model_name,
                suggestion_context,
                source_snapshot,
                target_snapshot,
                moved_order_ids,
                copied_address_count,
                merged_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3,
                10,
                20,
                0.8258,
                "basic_duplicate_knn_v1",
                json.dumps({"reasons": ["Strong text similarity across name, phone, and address"]}),
                json.dumps({"name": "Nirupam Das", "address": None, "phone": None}),
                json.dumps({"name": "Nirupam Das", "address": None, "phone": None}),
                json.dumps([7007]),
                0,
                "2026-04-13 19:18:50",
            ),
        )
        remote_event = {
            "remote_event_id": "dc42ee4a6e5047339a81f29f02da94e0",
            "schema_version": 1,
            "event_type": "customer_merge.applied",
            "occurred_at": "2026-04-13 19:18:50",
            "source_customer": {
                "snapshot": {
                    "name": "Nirupam Das",
                    "phone": None,
                    "address": None,
                    "gstin": None,
                    "total_orders": 1,
                    "total_spent": 666.0,
                    "last_order_date": "2026-02-13 22:43:46",
                    "is_verified": False,
                },
                "portable_locators": {
                    "customer_identity_key": None,
                    "phone_hash": None,
                    "name_address_hash": None,
                    "name_normalized": "nirupam das",
                    "address_normalized": None,
                    "address_book_hashes": [],
                },
            },
            "target_customer": {
                "snapshot": {
                    "name": "Nirupam Das",
                    "phone": None,
                    "address": None,
                    "gstin": None,
                    "total_orders": 1,
                    "total_spent": 740.0,
                    "last_order_date": "2025-12-14 21:05:18",
                    "is_verified": False,
                },
                "portable_locators": {
                    "customer_identity_key": None,
                    "phone_hash": None,
                    "name_address_hash": None,
                    "name_normalized": "nirupam das",
                    "address_normalized": None,
                    "address_book_hashes": [],
                },
            },
            "merge_metadata": {
                "similarity_score": 0.8258,
                "model_name": "basic_duplicate_knn_v1",
                "reasons": ["Strong text similarity across name, phone, and address"],
                "copied_address_count": 0,
                "target_before_fields": {"phone": None, "address": None, "gstin": None, "is_verified": False},
                "target_is_verified_after_merge": False,
                "mark_target_verified": False,
            },
            "moved_orders": {"count": 1, "portable_refs": []},
            "local_refs": {
                "merge_id": 3,
                "source_customer_id": 10,
                "target_customer_id": 20,
                "moved_order_ids": [7007],
                "inserted_target_address_ids": [],
                "removed_target_address_ids": [],
            },
        }
        self.conn.execute(
            """
            INSERT INTO customer_merge_sync_events (
                event_id,
                merge_id,
                event_type,
                payload,
                occurred_at,
                uploaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                remote_event["remote_event_id"],
                3,
                "customer_merge.applied",
                json.dumps(remote_event),
                remote_event["occurred_at"],
                "2026-04-13T19:54:11.279867+00:00",
            ),
        )
        self.conn.commit()

        payload = {"events": [remote_event], "next_cursor": "cursor-self-origin"}
        mock_get.return_value = Mock(status_code=200, json=Mock(return_value=payload))

        result = pull_and_apply_customer_merge_events(
            self.conn,
            endpoint="https://cloud.example.com/desktop-analytics-sync/customer-merges",
        )

        self.assertIsNone(result["error"])
        self.assertEqual(result["merge_events_applied"], 0)
        self.assertEqual(result["undo_events_applied"], 0)
        self.assertEqual(result["events_skipped"], 1)
        self.assertEqual(result["cursor_after"], "cursor-self-origin")
        remote_row = self.conn.execute(
            "SELECT remote_event_id, local_merge_id FROM customer_merge_remote_events WHERE remote_event_id = ?",
            (remote_event["remote_event_id"],),
        ).fetchone()
        self.assertIsNotNone(remote_row)
        self.assertEqual(int(remote_row["local_merge_id"]), 3)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_merge_history WHERE merge_id = 3").fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
