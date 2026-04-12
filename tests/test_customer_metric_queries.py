import sqlite3
import unittest
from unittest.mock import patch

from src.core.queries.customer_analytics_queries import fetch_customer_loyalty
from src.core.queries.customer_analytics_queries import fetch_customer_return_rate_analysis
from src.core.queries.customer_metric_helpers import (
    CustomerMetricFilters,
    calculate_customer_return_rate,
    calculate_repeat_order_rate,
    fetch_customer_metric_orders,
)
from src.core.queries.customer_reorder_rate_queries import fetch_customer_reorder_rate
from src.core.queries.insights_queries import fetch_customer_quick_view


class CustomerMetricQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                name TEXT,
                is_verified BOOLEAN NOT NULL DEFAULT 0
            );

            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER,
                created_on TEXT NOT NULL,
                total REAL NOT NULL DEFAULT 0,
                order_from TEXT NOT NULL,
                order_status TEXT NOT NULL
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO customers (customer_id, name, is_verified) VALUES (?, ?, ?)",
            [
                (1, "Aarav", 1),
                (2, "Bhavna", 1),
                (3, "Charu", 1),
                (4, "Dev", 1),
                (5, "Esha", 1),
                (6, "Ghost", 0),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO orders (order_id, customer_id, created_on, total, order_from, order_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "2024-01-05 12:00:00", 100, "Swiggy", "Success"),
                (2, 1, "2024-02-10 12:00:00", 120, "Swiggy", "Success"),
                (3, 2, "2024-02-03 12:00:00", 80, "POS", "Success"),
                (4, 2, "2024-02-20 12:00:00", 85, "POS", "Success"),
                (5, 3, "2023-12-12 12:00:00", 90, "Zomato", "Success"),
                (6, 3, "2024-01-09 12:00:00", 150, "Zomato", "Success"),
                (7, 4, "2023-12-07 12:00:00", 100, "Home Website", "Success"),
                (8, 4, "2024-02-08 12:00:00", 200, "Home Website", "Success"),
                (9, 5, "2024-01-14 12:00:00", 110, "Swiggy", "Success"),
                (10, 6, "2024-02-11 12:00:00", 999, "Swiggy", "Success"),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @patch("src.core.queries.insights_queries.get_current_business_date", return_value="2024-02-15")
    def test_fetch_customer_quick_view_uses_shared_metric_logic(self, _mock_date) -> None:
        data = fetch_customer_quick_view(self.conn)

        self.assertEqual(data["current_month"], "2024-02")
        self.assertEqual(data["returning_current_month_customers_one_month"], 2)
        self.assertEqual(data["returning_current_month_customers_two_month"], 3)
        self.assertEqual(data["total_current_month_customers"], 3)
        self.assertAlmostEqual(data["return_rate_one_month"], 66.67)
        self.assertAlmostEqual(data["return_rate_two_month"], 100.0)
        self.assertAlmostEqual(data["return_rate_lifetime"], 100.0)
        self.assertEqual(data["retained_customers_one_month"], 1)
        self.assertEqual(data["total_previous_one_month_customers"], 3)
        self.assertAlmostEqual(data["retention_rate_one_month"], 33.33)
        self.assertEqual(data["retained_customers_two_month"], 2)
        self.assertEqual(data["total_previous_two_month_customers"], 4)
        self.assertAlmostEqual(data["retention_rate_two_month"], 50.0)
        self.assertAlmostEqual(data["repeat_order_rate_current_month"], 33.33)
        self.assertAlmostEqual(data["repeat_order_rate_previous_month"], 0.0)
        self.assertAlmostEqual(data["return_rate_current_month"], 100.0)
        self.assertAlmostEqual(data["retention_rate_current_month"], 50.0)

    def test_fetch_customer_loyalty_uses_shared_monthly_rows(self) -> None:
        rows = fetch_customer_loyalty(self.conn).to_dict("records")

        self.assertEqual([row["Month"] for row in rows], ["2024-02", "2024-01", "2023-12"])

        february = rows[0]
        self.assertEqual(february["Repeat Orders"], 3)
        self.assertEqual(february["Total Orders"], 4)
        self.assertAlmostEqual(february["Order Repeat%"], 75.0)
        self.assertEqual(february["Repeat Customer Count"], 3)
        self.assertEqual(february["Total Verified Customers"], 3)
        self.assertAlmostEqual(february["Repeat Customer %"], 100.0)
        self.assertEqual(february["Repeat Revenue"], 405)
        self.assertEqual(february["Total Revenue"], 485)
        self.assertAlmostEqual(february["Revenue Repeat %"], 83.51)

        january = rows[1]
        self.assertEqual(january["Repeat Orders"], 1)
        self.assertEqual(january["Repeat Customer Count"], 1)
        self.assertEqual(january["Total Verified Customers"], 3)
        self.assertEqual(january["Repeat Revenue"], 150)
        self.assertEqual(january["Total Revenue"], 360)

    def test_fetch_customer_reorder_rate_uses_shared_monthly_rows(self) -> None:
        data = fetch_customer_reorder_rate(self.conn)

        self.assertEqual(data["total_verified_customers"], 5)
        self.assertEqual(data["total_customers"], 3)
        self.assertEqual(data["returning_customers"], 1)
        self.assertAlmostEqual(data["reorder_rate"], 50.0)

    def test_fetch_customer_return_rate_analysis_returns_summary_and_detail_rows(self) -> None:
        data = fetch_customer_return_rate_analysis(
            self.conn,
            evaluation_start_date="2024-02-01",
            evaluation_end_date="2024-02-29",
            lookback_start_date="2024-01-01",
            lookback_end_date="2024-01-31",
            min_orders_per_customer=2,
        )

        summary = data["summary"]
        self.assertEqual(summary["evaluation_start_date"], "2024-02-01")
        self.assertEqual(summary["evaluation_end_date"], "2024-02-29")
        self.assertEqual(summary["lookback_start_date"], "2024-01-01")
        self.assertEqual(summary["lookback_end_date"], "2024-01-31")
        self.assertEqual(summary["total_customers"], 3)
        self.assertEqual(summary["returning_customers"], 2)
        self.assertAlmostEqual(summary["return_rate"], 66.67)
        self.assertEqual(summary["new_customers"], 1)
        self.assertEqual(summary["returning_by_repeat_orders"], 1)
        self.assertEqual(summary["returning_from_lookback"], 1)
        self.assertEqual(summary["returning_by_both_conditions"], 0)
        self.assertEqual(summary["order_source_label"], "All")

        rows = data["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["customer_id"], 2)
        self.assertEqual(rows[0]["returning_status"], "Returning")
        self.assertEqual(rows[0]["return_reason"], "Repeat in evaluation window")
        self.assertEqual(rows[0]["evaluation_order_count"], 2)
        self.assertEqual(rows[0]["lookback_order_count"], 0)

        self.assertEqual(rows[1]["customer_id"], 1)
        self.assertEqual(rows[1]["returning_status"], "Returning")
        self.assertEqual(rows[1]["return_reason"], "Ordered in lookback window")
        self.assertEqual(rows[1]["evaluation_order_count"], 1)
        self.assertEqual(rows[1]["lookback_order_count"], 1)

        self.assertEqual(rows[2]["customer_id"], 4)
        self.assertEqual(rows[2]["returning_status"], "New")
        self.assertEqual(rows[2]["return_reason"], "First-time within selected windows")

    def test_shared_helper_supports_order_source_filters_and_thresholds(self) -> None:
        all_orders = fetch_customer_metric_orders(self.conn)
        pos_orders = fetch_customer_metric_orders(self.conn, order_sources=("POS",))
        swiggy_orders = fetch_customer_metric_orders(self.conn, order_sources=("Swiggy",))

        rolling_return_rate = calculate_customer_return_rate(
            all_orders,
            CustomerMetricFilters(
                evaluation_start_date="2024-02-01",
                evaluation_end_date="2024-02-29",
                lookback_days=31,
                min_orders_per_customer=2,
            ),
        )
        repeat_rate_two = calculate_repeat_order_rate(
            pos_orders,
            CustomerMetricFilters(
                evaluation_start_date="2024-02-01",
                evaluation_end_date="2024-02-29",
                min_orders_per_customer=2,
            ),
        )
        repeat_rate_three = calculate_repeat_order_rate(
            pos_orders,
            CustomerMetricFilters(
                evaluation_start_date="2024-02-01",
                evaluation_end_date="2024-02-29",
                min_orders_per_customer=3,
            ),
        )
        swiggy_return_rate = calculate_customer_return_rate(
            swiggy_orders,
            CustomerMetricFilters(
                evaluation_start_date="2024-02-01",
                evaluation_end_date="2024-02-29",
                lookback_start_date="2024-01-01",
                lookback_end_date="2024-01-31",
                min_orders_per_customer=2,
            ),
        )

        self.assertEqual(rolling_return_rate["returning_customers"], 2)
        self.assertAlmostEqual(rolling_return_rate["return_rate"], 66.67)
        self.assertEqual(repeat_rate_two["total_customers"], 1)
        self.assertEqual(repeat_rate_two["repeat_order_customers"], 1)
        self.assertAlmostEqual(repeat_rate_two["repeat_order_rate"], 100.0)
        self.assertEqual(repeat_rate_three["repeat_order_customers"], 0)
        self.assertAlmostEqual(repeat_rate_three["repeat_order_rate"], 0.0)
        self.assertEqual(swiggy_return_rate["total_customers"], 1)
        self.assertEqual(swiggy_return_rate["returning_customers"], 1)
        self.assertAlmostEqual(swiggy_return_rate["return_rate"], 100.0)

    def test_shared_helper_returns_zero_rate_for_empty_evaluation_windows(self) -> None:
        orders = fetch_customer_metric_orders(self.conn)
        metric = calculate_customer_return_rate(
            orders,
            CustomerMetricFilters(
                evaluation_start_date="2024-03-01",
                evaluation_end_date="2024-03-31",
                lookback_start_date="2024-02-01",
                lookback_end_date="2024-02-29",
                min_orders_per_customer=2,
            ),
        )

        self.assertEqual(metric["total_customers"], 0)
        self.assertEqual(metric["returning_customers"], 0)
        self.assertAlmostEqual(metric["return_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
