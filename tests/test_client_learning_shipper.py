import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.core.client_learning_shipper import run_all


class ClientLearningShipperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE ai_logs (
                query_id TEXT PRIMARY KEY,
                user_query TEXT,
                intent TEXT,
                sql_generated TEXT,
                response_type TEXT,
                response_payload TEXT,
                error_message TEXT,
                execution_time_ms INTEGER,
                created_at TEXT,
                raw_user_query TEXT,
                corrected_query TEXT,
                action_sequence TEXT,
                explanation TEXT,
                uploaded_at TEXT
            );

            CREATE TABLE ai_feedback (
                feedback_id TEXT PRIMARY KEY,
                query_id TEXT,
                is_positive BOOLEAN,
                comment TEXT,
                created_at TEXT,
                uploaded_at TEXT
            );

            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                customer_identity_key TEXT,
                name TEXT,
                name_normalized TEXT,
                phone TEXT,
                address TEXT,
                gstin TEXT,
                first_order_date TEXT,
                last_order_date TEXT,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                is_verified BOOLEAN NOT NULL DEFAULT 0
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
                is_default BOOLEAN DEFAULT 0
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

            CREATE TABLE merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT
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
                first_order_date,
                last_order_date,
                total_orders,
                total_spent,
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
                    "2024-02-03 10:00:00",
                    "2024-02-03 10:00:00",
                    1,
                    80.0,
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
                    "2024-02-04 10:00:00",
                    "2024-02-04 10:00:00",
                    1,
                    120.0,
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
            INSERT INTO ai_logs (
                query_id,
                user_query,
                intent,
                sql_generated,
                response_type,
                response_payload,
                error_message,
                execution_time_ms,
                created_at,
                raw_user_query,
                corrected_query,
                action_sequence,
                explanation,
                uploaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "q-1",
                "top customers",
                "customer_analytics",
                "SELECT 1",
                "table",
                "{}",
                None,
                25,
                "2024-02-05 10:00:00",
                "top customers",
                "top customers",
                "[]",
                "summary",
                None,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO customer_merge_history (
                source_customer_id,
                target_customer_id,
                similarity_score,
                model_name,
                suggestion_context,
                source_snapshot,
                target_snapshot,
                moved_order_ids,
                copied_address_count,
                merged_at,
                undone_at,
                undo_context
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                2,
                0.98,
                "duplicate_matcher_v1",
                '{"reasons":["phone exact match"]}',
                "{}",
                "{}",
                "[]",
                0,
                "2024-02-05 11:00:00",
                None,
                None,
            ),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @patch("requests.post")
    def test_run_all_preserves_named_rows_for_following_shippers(self, mock_post: Mock) -> None:
        mock_post.return_value = Mock(status_code=200)

        with tempfile.TemporaryDirectory() as log_dir:
            result = run_all(
                self.conn,
                log_dir=log_dir,
                base_url="https://cloud.example.com",
                auth="secret-token",
            )

        self.assertIsNone(result["learning"]["error"])
        self.assertEqual(result["learning"]["ai_logs_sent"], 1)
        self.assertIsNone(result["customer_merges"]["error"])
        self.assertEqual(result["customer_merges"]["backfilled_applied"], 1)
        self.assertEqual(result["customer_merges"]["events_sent"], 1)

        history_row = self.conn.execute(
            "SELECT merge_id FROM customer_merge_history LIMIT 1"
        ).fetchone()
        self.assertEqual(history_row["merge_id"], 1)

        sync_row = self.conn.execute(
            "SELECT uploaded_at FROM customer_merge_sync_events WHERE merge_id = 1"
        ).fetchone()
        self.assertIsNotNone(sync_row["uploaded_at"])


if __name__ == "__main__":
    unittest.main()
