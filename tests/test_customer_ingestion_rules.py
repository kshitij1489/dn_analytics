import sqlite3
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from services.load_orders import get_or_create_customer


class CustomerIngestionRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_identity_key TEXT UNIQUE,
                name TEXT,
                name_normalized TEXT,
                phone TEXT,
                address TEXT,
                gstin TEXT,
                first_order_date TEXT,
                last_order_date TEXT,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                is_verified BOOLEAN DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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

            CREATE TABLE customer_merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_customer_id INTEGER NOT NULL,
                target_customer_id INTEGER NOT NULL,
                undone_at TEXT
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    @patch("services.load_orders.compute_customer_identity_key", return_value="anon:test-customer")
    def test_existing_verified_anonymous_customer_is_not_demoted_on_update(self, _mock_identity_key) -> None:
        customer_data = {"name": "Walk In"}

        customer_id = get_or_create_customer(
            self.conn,
            customer_data,
            datetime.fromisoformat("2024-02-01T10:00:00"),
            Decimal("10"),
        )
        self.conn.execute(
            "UPDATE customers SET is_verified = 1 WHERE customer_id = ?",
            (customer_id,),
        )
        self.conn.commit()

        updated_customer_id = get_or_create_customer(
            self.conn,
            customer_data,
            datetime.fromisoformat("2024-02-02T12:00:00"),
            Decimal("15"),
        )

        self.assertEqual(updated_customer_id, customer_id)
        row = self.conn.execute(
            "SELECT is_verified, total_orders, total_spent FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)
        self.assertEqual(int(row["total_orders"]), 2)
        self.assertAlmostEqual(float(row["total_spent"]), 25.0)


if __name__ == "__main__":
    unittest.main()
