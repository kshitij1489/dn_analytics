import sqlite3
import unittest

from src.core.queries.customer_merge_queries import merge_customers
from src.core.queries.customer_similarity_queries import (
    fetch_customer_merge_preview,
    fetch_customer_similarity_candidates,
)


class CustomerMergeRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                name TEXT,
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
            """
        )
        self.conn.executemany(
            """
            INSERT INTO customers (
                customer_id, name, phone, address, total_orders, total_spent, first_order_date, last_order_date, is_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Rahul Sharma", "9999999999", "HSR Layout", 5, 500.0, "2024-01-01", "2024-02-01", 1),
                (2, "Rahul Sharma", None, "HSR Layout", 1, 80.0, "2024-02-03", "2024-02-03", 0),
                (3, "Rahul Sharma", None, "HSR Layout", 2, 120.0, "2024-02-04", "2024-02-04", 0),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO orders (order_id, customer_id, petpooja_order_id, total, created_on)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (101, 2, "PP-101", 80.0, "2024-02-03 10:00:00"),
                (102, 3, "PP-102", 120.0, "2024-02-04 10:00:00"),
                (201, 1, "PP-201", 180.0, "2024-01-15 13:00:00"),
                (202, 1, "PP-202", 220.0, "2024-02-05 18:30:00"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name) VALUES (?, ?)",
            [
                ("m_apple_pie", "Apple Pie"),
                ("m_burger", "Burger"),
                ("m_pasta", "Pasta"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO order_items (order_id, menu_item_id, name_raw, quantity)
            VALUES (?, ?, ?, ?)
            """,
            [
                (101, "m_burger", "Burger", 1),
                (101, "m_apple_pie", "Apple Pie", 2),
                (101, None, "Cold Coffee", 1),
                (102, "m_pasta", "Pasta", 1),
                (201, "m_burger", "Burger", 1),
                (201, "m_apple_pie", "Apple Pie", 1),
                (202, "m_burger", "Burger", 2),
                (202, "m_pasta", "Pasta", 1),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_merge_preview_rejects_unverified_target(self) -> None:
        preview = fetch_customer_merge_preview(self.conn, "1", "2")

        self.assertEqual(preview["status"], "error")
        self.assertEqual(
            preview["message"],
            "Customers can only be merged into a verified target customer.",
        )

    def test_merge_rejects_unverified_target(self) -> None:
        result = merge_customers(self.conn, "1", "2")

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["message"],
            "Customers can only be merged into a verified target customer.",
        )

    def test_merge_preview_includes_order_snapshots_with_sorted_items_and_top_items(self) -> None:
        preview = fetch_customer_merge_preview(self.conn, "2", "1")

        self.assertEqual(preview["source_order_snapshot"]["recent_orders"][0]["order_number"], "PP-101")
        self.assertEqual(
            [item["item_name"] for item in preview["source_order_snapshot"]["recent_orders"][0]["items"]],
            ["Apple Pie", "Burger", "Cold Coffee"],
        )
        self.assertEqual(
            preview["source_order_snapshot"]["recent_orders"][0]["items_summary"],
            "Apple Pie x2, Burger, Cold Coffee",
        )
        self.assertEqual(preview["target_order_snapshot"]["top_items"][0]["item_name"], "Burger")
        self.assertEqual(preview["target_order_snapshot"]["top_items"][0]["total_quantity"], 3)
        self.assertEqual(preview["target_order_snapshot"]["top_items"][0]["order_count"], 2)

    def test_similarity_candidates_only_keep_verified_targets(self) -> None:
        suggestions = fetch_customer_similarity_candidates(self.conn, limit=10, min_score=0.5)

        self.assertGreaterEqual(len(suggestions), 1)
        self.assertTrue(all(item["target_customer"]["is_verified"] for item in suggestions))
        self.assertTrue(
            any(
                item["source_customer"]["customer_id"] in {"2", "3"}
                and item["target_customer"]["customer_id"] == "1"
                for item in suggestions
            )
        )
        self.assertFalse(
            any(
                {item["source_customer"]["customer_id"], item["target_customer"]["customer_id"]} == {"2", "3"}
                for item in suggestions
            )
        )

    def test_similarity_search_filters_pairs_by_source_or_target_name(self) -> None:
        suggestions = fetch_customer_similarity_candidates(
            self.conn,
            limit=10,
            min_score=0.5,
            search_query="  RAHUL ",
        )

        self.assertGreaterEqual(len(suggestions), 1)
        self.assertEqual(
            [item["score"] for item in suggestions],
            sorted((item["score"] for item in suggestions), reverse=True),
        )
        self.assertTrue(
            all(
                "rahul" in item["source_customer"]["name"].lower()
                or "rahul" in item["target_customer"]["name"].lower()
                for item in suggestions
            )
        )
        self.assertTrue(all(item["target_customer"]["is_verified"] for item in suggestions))
        self.assertEqual(
            fetch_customer_similarity_candidates(
                self.conn,
                limit=10,
                min_score=0.5,
                search_query="nazia",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
