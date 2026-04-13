import hashlib
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.customer_merge_shipper import upload_pending
from src.core.customer_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    EVENT_TYPE_UNDONE,
    SCHEMA_VERSION,
)
from src.core.queries.customer_merge_queries import merge_customers, undo_customer_merge


class CustomerMergeSyncTests(unittest.TestCase):
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

            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                name TEXT
            );

            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                menu_item_id TEXT,
                name_raw TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1
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
        self.conn.executemany(
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
            [
                (101, 1, "PP-101", 5001, "evt-101", "agg-101", 80.0, "2024-02-03 10:00:00"),
                (102, 2, "PP-102", 5002, "evt-102", "agg-102", 120.0, "2024-02-04 10:00:00"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name) VALUES (?, ?)",
            [
                ("m_burger", "Burger"),
                ("m_fries", "Fries"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO order_items (order_id, menu_item_id, name_raw, quantity)
            VALUES (?, ?, ?, ?)
            """,
            [
                (101, "m_burger", "Burger", 1),
                (102, "m_fries", "Fries", 2),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_merge_records_customer_merge_applied_event(self) -> None:
        result = merge_customers(
            self.conn,
            "1",
            "2",
            similarity_score=0.98,
            model_name="duplicate_matcher_v1",
            reasons=["phone exact match"],
            mark_target_verified=True,
        )

        self.assertEqual(result["status"], "success")
        row = self.conn.execute(
            "SELECT event_id, event_type, payload FROM customer_merge_sync_events WHERE merge_id = ?",
            (result["merge_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["event_type"], EVENT_TYPE_APPLIED)

        payload = json.loads(row["payload"])
        self.assertEqual(payload["remote_event_id"], row["event_id"])
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["event_type"], EVENT_TYPE_APPLIED)
        self.assertTrue(payload["merge_metadata"]["mark_target_verified"])
        self.assertEqual(payload["merge_metadata"]["reasons"], ["phone exact match"])
        self.assertEqual(payload["moved_orders"]["count"], 1)
        self.assertEqual(payload["moved_orders"]["portable_refs"][0]["petpooja_order_id"], "PP-101")
        expected_phone_hash = hashlib.sha256("9999999999".encode("utf-8")).hexdigest()
        self.assertEqual(
            payload["source_customer"]["portable_locators"]["phone_hash"],
            expected_phone_hash,
        )

    def test_undo_records_customer_merge_undone_event_linked_to_applied_event(self) -> None:
        merge_result = merge_customers(
            self.conn,
            "1",
            "2",
            similarity_score=0.9,
            model_name="duplicate_matcher_v1",
            reasons=["same address"],
        )
        undo_result = undo_customer_merge(self.conn, merge_result["merge_id"])

        self.assertEqual(undo_result["status"], "success")
        rows = self.conn.execute(
            """
            SELECT event_id, event_type, payload
            FROM customer_merge_sync_events
            WHERE merge_id = ?
            ORDER BY event_type ASC
            """,
            (merge_result["merge_id"],),
        ).fetchall()
        self.assertEqual(len(rows), 2)

        applied_row = next(row for row in rows if row["event_type"] == EVENT_TYPE_APPLIED)
        undone_row = next(row for row in rows if row["event_type"] == EVENT_TYPE_UNDONE)
        undone_payload = json.loads(undone_row["payload"])

        self.assertEqual(undone_payload["event_type"], EVENT_TYPE_UNDONE)
        self.assertEqual(undone_payload["reverts_remote_event_id"], applied_row["event_id"])
        self.assertEqual(undone_payload["moved_orders"]["count"], 1)

    @patch("requests.post")
    def test_upload_pending_posts_events_and_marks_rows_uploaded(self, mock_post: Mock) -> None:
        merge_result = merge_customers(
            self.conn,
            "1",
            "2",
            similarity_score=0.95,
            model_name="duplicate_matcher_v1",
            reasons=["same phone"],
        )
        self.conn.execute(
            "DELETE FROM customer_merge_sync_events WHERE merge_id = ?",
            (merge_result["merge_id"],),
        )
        self.conn.commit()

        mock_post.return_value = Mock(status_code=200)
        uploaded_by = {"employee_id": "0001", "name": "Owner"}
        result = upload_pending(
            self.conn,
            endpoint="https://cloud.example.com/desktop-analytics-sync/customer-merges/ingest",
            auth="secret-token",
            uploaded_by=uploaded_by,
        )

        self.assertEqual(result["events_sent"], 1)
        self.assertEqual(result["backfilled_applied"], 1)
        self.assertEqual(result["backfilled_undone"], 0)
        mock_post.assert_called_once()

        call_kwargs = mock_post.call_args.kwargs
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(call_kwargs["json"]["uploaded_by"], uploaded_by)
        self.assertEqual(call_kwargs["json"]["schema_version"], SCHEMA_VERSION)
        self.assertEqual(len(call_kwargs["json"]["events"]), 1)

        row = self.conn.execute(
            "SELECT uploaded_at, last_error FROM customer_merge_sync_events WHERE merge_id = ?",
            (merge_result["merge_id"],),
        ).fetchone()
        self.assertIsNotNone(row["uploaded_at"])
        self.assertIsNone(row["last_error"])


if __name__ == "__main__":
    unittest.main()
