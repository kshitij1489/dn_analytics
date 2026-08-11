import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.analytics_stream_contract import (
    parse_allowed_restaurants,
    parse_error_envelope,
    parse_stream_page,
)
from src.core.central_api import CentralAPIError, error_from_response, scoped_headers
from src.core.derived_assignment_flush import _build_event
from src.core.forecast_sync import get_forecast_bootstrap_endpoint
from src.core.profiles import ProfileSelectionRequired
from utils.api_client import fetch_stream_raw


EXPECTED_FIXTURES = {
    "orders_stream_page",
    "orders_stream_page_empty",
    "order_items_stream_page",
    "addons_stream_page",
    "discounts_stream_page",
    "allowed_restaurants",
    "allowed_restaurants_ungranted",
    "error_selector_missing",
    "error_selector_invalid",
    "error_restaurant_forbidden",
    "error_unknown_restaurant",
    "error_invalid_page_parameter",
    "error_invalid_api_key",
    "error_sync_scope_forbidden",
    "error_selector_in_query_string",
    "error_retired_query_parameter",
}


class AnalyticsStreamContractV12Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = Path(__file__).parents[1] / "contracts" / "fixtures" / "1" / "analytics_stream_fixtures.json"
        document = json.loads(fixture_path.read_text(encoding="utf-8"))
        cls.fixtures = {fixture["name"]: fixture for fixture in document["fixtures"]}

    def test_fixture_names_are_pinned_in_both_directions(self) -> None:
        self.assertEqual(set(self.fixtures), EXPECTED_FIXTURES)

    def test_every_success_fixture_parses(self) -> None:
        streams = {
            "orders_stream_page": "orders",
            "orders_stream_page_empty": "orders",
            "order_items_stream_page": "order-items",
            "addons_stream_page": "addons",
            "discounts_stream_page": "discounts",
        }
        for name, stream in streams.items():
            with self.subTest(name=name):
                page = parse_stream_page(self.fixtures[name]["payload"], stream)
                self.assertIsInstance(page.data, list)
        for name in ("allowed_restaurants", "allowed_restaurants_ungranted"):
            with self.subTest(name=name):
                self.assertIsInstance(
                    parse_allowed_restaurants(self.fixtures[name]["payload"]), list
                )
        for name in EXPECTED_FIXTURES - set(streams) - {
            "allowed_restaurants",
            "allowed_restaurants_ungranted",
        }:
            with self.subTest(name=name):
                envelope = parse_error_envelope(self.fixtures[name]["payload"])
                self.assertTrue(envelope["code"])

    def test_empty_page_keeps_null_cursor(self) -> None:
        page = parse_stream_page(
            self.fixtures["orders_stream_page_empty"]["payload"], "orders"
        )
        self.assertIsNone(page.next_cursor)

    def test_selector_errors_are_non_retryable_and_keep_code(self) -> None:
        response = unittest.mock.Mock(status_code=403)
        response.json.return_value = self.fixtures["error_restaurant_forbidden"]["payload"]
        error = error_from_response(response)
        self.assertEqual(error.code, "restaurant_forbidden")
        self.assertFalse(error.retryable)

    @staticmethod
    def _configured_bound_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO system_config (key, value) VALUES (?, ?)",
            [
                ("integration_orders_url", "https://central.example/analytics"),
                ("integration_orders_key", "analytics-key"),
            ],
        )
        conn.execute(
            "CREATE TABLE restaurant_profile_identity (singleton_id INTEGER PRIMARY KEY, restaurant_id TEXT)"
        )
        conn.execute("INSERT INTO restaurant_profile_identity VALUES (1, 'rest-A')")
        conn.commit()
        return conn

    def test_raw_stream_retries_retryable_5xx(self) -> None:
        conn = self._configured_bound_connection()
        self.addCleanup(conn.close)
        unavailable = unittest.mock.Mock(status_code=503)
        unavailable.json.return_value = {"error": "temporarily unavailable", "code": "http_error"}
        success = unittest.mock.Mock(status_code=200)
        success.json.return_value = {"data": [], "cursor": {"cursor": None}, "total": 0}

        with patch("requests.get", side_effect=[unavailable, success]) as request, patch(
            "utils.api_client.time.sleep"
        ):
            rows, total = fetch_stream_raw(conn)

        self.assertEqual((rows, total), ([], 0))
        self.assertEqual(request.call_count, 2)

    def test_raw_stream_does_not_retry_forbidden_scope(self) -> None:
        conn = self._configured_bound_connection()
        self.addCleanup(conn.close)
        forbidden = unittest.mock.Mock(status_code=403)
        forbidden.json.return_value = {
            "error": "Restaurant is not allowed",
            "code": "restaurant_forbidden",
        }

        with patch("requests.get", return_value=forbidden) as request, patch(
            "utils.api_client.time.sleep"
        ) as sleep:
            with self.assertRaises(CentralAPIError) as raised:
                fetch_stream_raw(conn)

        self.assertEqual(raised.exception.code, "restaurant_forbidden")
        request.assert_called_once()
        sleep.assert_not_called()

    def test_anonymous_database_blocks_before_http(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO system_config (key, value) VALUES (?, ?)",
            [
                ("integration_orders_url", "https://central.example/analytics"),
                ("integration_orders_key", "key"),
            ],
        )
        with patch("requests.get") as request:
            with self.assertRaises(ProfileSelectionRequired):
                fetch_stream_raw(conn)
            request.assert_not_called()
        conn.close()

    def test_all_token_is_never_a_scoped_header(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE restaurant_profile_identity (singleton_id INTEGER PRIMARY KEY, restaurant_id TEXT)"
        )
        conn.execute(
            "INSERT INTO restaurant_profile_identity VALUES (1, '__all__')"
        )
        with self.assertRaises(ProfileSelectionRequired):
            scoped_headers(conn, auth_kind="sync", credential="key")
        conn.close()

    def test_derived_assignment_payload_omits_retired_locator_metadata(self) -> None:
        event = _build_event(
            [
                {
                    "order_item_id": "line-1",
                    "menu_item_id": "item-1",
                    "variant_id": None,
                }
            ],
            {"items": [], "variants": []},
        )
        assignment = event["merge_payload"]["assignments"][0]
        for retired in ("order_line_key", "petpooja_order_id", "petpooja_itemid"):
            self.assertNotIn(retired, assignment)

    def test_forecast_bootstrap_uses_only_canonical_route(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO system_config (key, value) VALUES (?, ?)",
            [
                ("cloud_sync_url", "https://central.example"),
                ("cloud_sync_api_key", "key"),
            ],
        )
        self.assertTrue(
            get_forecast_bootstrap_endpoint(conn).endswith(
                "/forecasts/central-bootstrap"
            )
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
