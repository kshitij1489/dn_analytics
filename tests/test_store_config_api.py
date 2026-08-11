"""Phase 1 control-plane, binding, path, and profile-isolation tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.core.analytics_scope import restaurant_scope
from src.core.central_api import error_from_response, scoped_headers
from src.core.db.connection import apply_analytics_schema, get_profile_connection
from src.core.profiles import (
    MixedRestaurantDatabase,
    ProfileBindingConfirmationRequired,
    ProfileMismatch,
    bind_and_select_profile,
    get_profile,
    list_profiles,
    profile_path_for,
    selected_profile,
    upsert_allowed_restaurants,
)


class RestaurantProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.analytics_path = self.root / "analytics.db"
        self.env_patch = patch.dict(
            os.environ,
            {
                "ANALYTICS_APP_DATA_ROOT": str(self.root),
                "ANALYTICS_DB_PATH": str(self.analytics_path),
                "ANALYTICS_CONTROL_DB_PATH": str(self.root / "analytics-control.db"),
                "DB_URL": str(self.analytics_path),
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _create_existing(self, *restaurant_ids: str) -> None:
        import sqlite3

        conn = sqlite3.connect(str(self.analytics_path))
        apply_analytics_schema(conn)
        for index, restaurant_id in enumerate(restaurant_ids, start=1):
            conn.execute(
                "INSERT INTO restaurants (petpooja_restid, name) VALUES (?, ?)",
                (restaurant_id, f"Store {index}"),
            )
        conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type) VALUES ('keep-me', 'Keep Me', 'Dessert')"
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _allowed(*ids: str):
        return [
            {
                "restaurant_id": restaurant_id,
                "display_name": "Same display name",
                "timezone": "Asia/Kolkata",
            }
            for restaurant_id in ids
        ]

    def test_existing_database_binding_preserves_business_rows(self) -> None:
        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A", "rest-B"))
        profile = get_profile("rest-A")
        self.assertEqual(Path(profile.database_path), self.analytics_path.resolve())

        with self.assertRaises(ProfileBindingConfirmationRequired):
            bind_and_select_profile("rest-A")

        before = self.analytics_path.stat().st_size
        bound = bind_and_select_profile("rest-A", confirm_existing_binding=True)
        conn, _ = get_profile_connection(bound)
        try:
            self.assertEqual(
                conn.execute("SELECT menu_item_id FROM menu_items").fetchone()[0],
                "keep-me",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
                ).fetchone()[0],
                "rest-A",
            )
        finally:
            conn.close()
        self.assertGreaterEqual(self.analytics_path.stat().st_size, before)
        self.assertEqual(selected_profile().restaurant_id, "rest-A")

    def test_second_profile_is_separate_and_duplicate_names_are_allowed(self) -> None:
        self._create_existing("rest-A")
        profiles = upsert_allowed_restaurants(self._allowed("rest-A", "rest-B"))
        self.assertEqual(len(profiles), 2)
        self.assertEqual({p.display_name for p in profiles}, {"Same display name"})

        bind_and_select_profile("rest-A", confirm_existing_binding=True)
        second = bind_and_select_profile("rest-B")
        self.assertNotEqual(Path(second.database_path), self.analytics_path)
        self.assertTrue(Path(second.database_path).exists())

        second_conn, _ = get_profile_connection(second)
        try:
            second_conn.execute(
                "INSERT INTO menu_items (menu_item_id, name, type) VALUES ('shared-id', 'B only', 'Dessert')"
            )
            second_conn.commit()
        finally:
            second_conn.close()

        first_conn, _ = get_profile_connection(get_profile("rest-A"))
        try:
            self.assertIsNone(
                first_conn.execute(
                    "SELECT 1 FROM menu_items WHERE menu_item_id='shared-id'"
                ).fetchone()
            )
        finally:
            first_conn.close()

    def test_mixed_existing_database_is_refused(self) -> None:
        self._create_existing("rest-A", "rest-B")
        upsert_allowed_restaurants(self._allowed("rest-A", "rest-B", "rest-C"))
        with self.assertRaises(MixedRestaurantDatabase):
            bind_and_select_profile("rest-A", confirm_existing_binding=True)
        with self.assertRaises(MixedRestaurantDatabase):
            bind_and_select_profile("rest-C")
        self.assertFalse(profile_path_for("rest-C").exists())

    def test_unidentified_existing_business_data_is_refused(self) -> None:
        import sqlite3

        self._create_existing()
        upsert_allowed_restaurants(self._allowed("rest-A"))

        with self.assertRaisesRegex(ProfileMismatch, "no recoverable restaurant identity"):
            bind_and_select_profile("rest-A", confirm_existing_binding=True)
        conn = sqlite3.connect(str(self.analytics_path))
        try:
            self.assertIsNone(conn.execute(
                "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
            ).fetchone())
        finally:
            conn.close()

    def test_binding_preflight_scans_all_legacy_raw_events(self) -> None:
        import sqlite3

        conn = sqlite3.connect(str(self.analytics_path))
        conn.executescript(
            """
            CREATE TABLE restaurants (petpooja_restid TEXT, name TEXT);
            CREATE TABLE orders (raw_event TEXT);
            INSERT INTO restaurants VALUES ('rest-A', 'Store A');
            """
        )
        raw_a = json.dumps(
            {"raw_payload": {"properties": {"Restaurant": {"restID": "rest-A"}}}}
        )
        raw_b = json.dumps(
            {"raw_payload": {"properties": {"Restaurant": {"restID": "rest-B"}}}}
        )
        conn.executemany("INSERT INTO orders (raw_event) VALUES (?)", [(raw_a,)] * 1000)
        conn.execute("INSERT INTO orders (raw_event) VALUES (?)", (raw_b,))
        conn.commit()
        conn.close()

        upsert_allowed_restaurants(self._allowed("rest-A", "rest-B"))
        with self.assertRaises(MixedRestaurantDatabase):
            bind_and_select_profile("rest-A", confirm_existing_binding=True)

    def test_identity_mismatch_is_refused(self) -> None:
        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A", "rest-B"))
        bind_and_select_profile("rest-A", confirm_existing_binding=True)
        wrong = get_profile("rest-B")
        object.__setattr__(wrong, "database_path", str(self.analytics_path))
        with self.assertRaises(ProfileMismatch):
            get_profile_connection(wrong)

    def test_raw_restaurant_id_never_appears_in_profile_filename(self) -> None:
        path = profile_path_for("tenant/with/slash")
        self.assertNotIn("tenant", path.name)
        self.assertNotIn("/", path.name)
        self.assertEqual(path.parent, (self.root / "profiles").resolve())

    def test_scoped_headers_fail_closed_and_use_bound_identity(self) -> None:
        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A"))
        profile = bind_and_select_profile("rest-A", confirm_existing_binding=True)
        conn, _ = get_profile_connection(profile)
        try:
            headers = scoped_headers(conn, auth_kind="analytics", credential="raw-key")
            self.assertEqual(headers["X-Restaurant-ID"], "rest-A")
            self.assertEqual(headers["X-API-Key"], "raw-key")
            self.assertNotIn("Authorization", headers)
        finally:
            conn.close()

    def test_unauthorized_bound_profile_remains_selectable_offline(self) -> None:
        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A", "rest-B"))
        bind_and_select_profile("rest-A", confirm_existing_binding=True)

        # A later allowed-list refresh omits A. Its bound database is retained
        # and may still be selected for local read-only analytics.
        upsert_allowed_restaurants(self._allowed("rest-B"))
        selected = bind_and_select_profile("rest-A")
        self.assertEqual(selected.authorization_state, "unauthorized")
        self.assertTrue(selected.is_bound)

    def test_central_forbidden_revokes_profile_but_preserves_offline_reads(self) -> None:
        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A"))
        profile = bind_and_select_profile("rest-A", confirm_existing_binding=True)
        conn, _ = get_profile_connection(profile)
        try:
            response = Mock(status_code=403)
            response.json.return_value = {
                "error": "Restaurant is not allowed",
                "code": "restaurant_forbidden",
            }
            error = error_from_response(response, conn=conn)
            self.assertEqual(error.code, "restaurant_forbidden")
        finally:
            conn.close()

        revoked = get_profile("rest-A")
        self.assertEqual(revoked.authorization_state, "unauthorized")
        self.assertTrue(revoked.is_bound)
        offline, _ = get_profile_connection(revoked)
        try:
            with self.assertRaisesRegex(Exception, "not authorized"):
                scoped_headers(offline, auth_kind="sync", credential="token")
        finally:
            offline.close()

    def test_profile_mutation_routes_require_authorization(self) -> None:
        from src.api.dependencies import (
            get_authorized_db,
            get_authorized_restaurant_profile,
        )
        from src.api.main import app

        checked = []
        for route in app.routes:
            methods = set(route.methods or ())
            path = route.path
            is_menu_write = path.startswith("/api/menu/") and bool(
                methods.intersection({"POST", "PUT", "PATCH", "DELETE"})
            )
            is_customer_write = path.startswith("/api/orders/customers/merge") and "POST" in methods
            is_weather_write = path.endswith("/weather/sync") and "POST" in methods
            if not (is_menu_write or is_customer_write or is_weather_write):
                continue
            dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
            self.assertTrue(
                get_authorized_db in dependency_calls
                or get_authorized_restaurant_profile in dependency_calls,
                f"{path} can write an unauthorized cached profile",
            )
            checked.append(path)

        self.assertGreaterEqual(len(checked), 10)

    def test_unauthorized_forecast_read_does_not_schedule_weather_write(self) -> None:
        from src.api.dependencies import ScopedReader
        from src.api.routers import forecast

        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A"))
        profile = bind_and_select_profile("rest-A", confirm_existing_binding=True)
        upsert_allowed_restaurants([])
        profile = get_profile(profile.restaurant_id)
        background_tasks = Mock()
        with patch.object(forecast, "build_revenue_history_rows", return_value=[]), patch.object(
            forecast, "build_revenue_forecast_response", return_value={"forecasts": []}
        ):
            forecast.get_sales_forecast(
                background_tasks, reader=ScopedReader(restaurant_scope(profile))
            )

        background_tasks.add_task.assert_not_called()

    def test_unauthorized_unbound_profile_cannot_be_initialized(self) -> None:
        upsert_allowed_restaurants(self._allowed("rest-A", "rest-B"))
        upsert_allowed_restaurants(self._allowed("rest-A"))
        with self.assertRaisesRegex(Exception, "not authorized"):
            bind_and_select_profile("rest-B")

    def test_existing_ungranted_database_can_be_bound_for_offline_reads(self) -> None:
        self._create_existing("rest-legacy")
        profiles = upsert_allowed_restaurants(self._allowed("rest-current"))
        legacy = next(profile for profile in profiles if profile.restaurant_id == "rest-legacy")
        self.assertEqual(legacy.authorization_state, "unauthorized")
        self.assertEqual(Path(legacy.database_path), self.analytics_path.resolve())

        with self.assertRaises(ProfileBindingConfirmationRequired):
            bind_and_select_profile("rest-legacy")
        bound = bind_and_select_profile(
            "rest-legacy", confirm_existing_binding=True
        )
        self.assertTrue(bound.is_bound)
        self.assertEqual(bound.authorization_state, "unauthorized")
        conn, _ = get_profile_connection(bound)
        try:
            self.assertEqual(
                conn.execute("SELECT menu_item_id FROM menu_items").fetchone()[0],
                "keep-me",
            )
            with self.assertRaisesRegex(Exception, "not authorized"):
                scoped_headers(conn, auth_kind="analytics", credential="key")
        finally:
            conn.close()

    def test_colliding_petpooja_order_ids_remain_profile_local(self) -> None:
        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A", "rest-B"))
        first = bind_and_select_profile("rest-A", confirm_existing_binding=True)
        second = bind_and_select_profile("rest-B")

        for index, profile in enumerate((first, second), start=1):
            conn, _ = get_profile_connection(profile)
            try:
                restaurant = conn.execute(
                    "SELECT restaurant_id FROM restaurants WHERE petpooja_restid=?",
                    (profile.restaurant_id,),
                ).fetchone()
                restaurant_pk = restaurant[0] if restaurant else conn.execute(
                    "INSERT INTO restaurants (petpooja_restid, name) VALUES (?, ?) RETURNING restaurant_id",
                    (profile.restaurant_id, profile.display_name),
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO orders (
                        petpooja_order_id, stream_id, event_id, aggregate_id,
                        restaurant_id, occurred_at, created_on, order_type,
                        order_from, order_status, total
                    ) VALUES (19470, ?, ?, '19470', ?, '2026-08-08T00:00:00Z',
                              '2026-08-08 05:30:00', 'Delivery', ?, 'Success', ?)
                    """,
                    (index, f"event-{index}", restaurant_pk, f"Store {index}", index * 100),
                )
                conn.commit()
            finally:
                conn.close()

        for expected_total, profile in ((100, first), (200, second), (100, first)):
            conn, _ = get_profile_connection(profile)
            try:
                row = conn.execute(
                    "SELECT total FROM orders WHERE petpooja_order_id=19470"
                ).fetchone()
                self.assertEqual(row[0], expected_total)
            finally:
                conn.close()

    def test_canonical_schema_helper_upgrades_old_columns_and_seeds_owner(self) -> None:
        import sqlite3

        legacy_path = self.root / "legacy.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.executescript(
            """
            CREATE TABLE ai_logs (query_id TEXT PRIMARY KEY);
            CREATE TABLE ai_feedback (feedback_id INTEGER PRIMARY KEY);
            CREATE TABLE app_users (user_id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            INSERT INTO app_users (user_id, name) VALUES (42, 'Legacy Owner');
            """
        )
        apply_analytics_schema(conn)
        try:
            ai_columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_logs)")}
            self.assertIn("uploaded_at", ai_columns)
            self.assertIn("total_prompt_tokens", ai_columns)
            user = conn.execute("SELECT employee_id, name FROM app_users").fetchone()
            self.assertEqual(user, ("42", "Legacy Owner"))
        finally:
            conn.close()

    def test_control_config_applies_only_to_registered_profile_files(self) -> None:
        import sqlite3

        from src.core.db.control import resolve_config_values, set_global_config

        set_global_config({"cloud_sync_url": "https://control.example"})
        standalone = sqlite3.connect(":memory:")
        standalone.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT)")
        standalone.execute(
            "INSERT INTO system_config VALUES ('cloud_sync_url', 'https://standalone.example')"
        )
        try:
            self.assertEqual(
                resolve_config_values(standalone, ("cloud_sync_url",))["cloud_sync_url"],
                "https://standalone.example",
            )
        finally:
            standalone.close()

        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A"))
        profile = bind_and_select_profile("rest-A", confirm_existing_binding=True)
        conn, _ = get_profile_connection(profile)
        try:
            conn.execute(
                """
                INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://legacy.example')
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """
            )
            conn.commit()
            self.assertEqual(
                resolve_config_values(conn, ("cloud_sync_url",))["cloud_sync_url"],
                "https://control.example",
            )
        finally:
            conn.close()

    def test_legacy_config_copy_excludes_all_profile_local_metadata(self) -> None:
        from src.core.db.control import copy_legacy_global_config_once, get_global_config

        self._create_existing("rest-A")
        import sqlite3

        legacy = sqlite3.connect(str(self.analytics_path))
        legacy.executemany(
            """
            INSERT INTO system_config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            [
                ("cloud_sync_url", "https://control.example"),
                ("sync_cursor_schema_version", "2"),
                ("menu_bootstrap_apply_mode", "seed_and_relink_orders"),
                ("menu_merge_pull_cursor", "cursor-A"),
                ("central_forecast_status", "ready-A"),
            ],
        )
        legacy.commit()
        legacy.close()

        copy_legacy_global_config_once(self.analytics_path)
        copied = get_global_config()
        self.assertEqual(copied.get("cloud_sync_url"), "https://control.example")
        for local_key in (
            "sync_cursor_schema_version",
            "menu_bootstrap_apply_mode",
            "menu_merge_pull_cursor",
            "central_forecast_status",
        ):
            self.assertNotIn(local_key, copied)

    def test_sync_job_keeps_captured_profile_after_selection_switch(self) -> None:
        from src.api.routers import operations
        from src.core.central_api import restaurant_id_from_connection

        self._create_existing("rest-A")
        upsert_allowed_restaurants(self._allowed("rest-A", "rest-B"))
        first = bind_and_select_profile("rest-A", confirm_existing_binding=True)
        second = bind_and_select_profile("rest-B")
        bind_and_select_profile("rest-A")

        captured = []
        seen_restaurants = []

        def capture_job(factory):
            captured.append(factory)
            return "job-A"

        def fake_statuses(conn):
            seen_restaurants.append(restaurant_id_from_connection(conn))
            yield operations.SyncStatus("done", "captured")

        with patch.object(operations.JobManager, "start_job", side_effect=capture_job), patch.object(
            operations, "iter_sync_statuses", side_effect=fake_statuses
        ):
            response = operations.run_sync(
                operations.SyncRunRequest(restaurant_id="rest-A"),
                scope=restaurant_scope(first),
            )
            self.assertEqual(response["job_id"], "job-A")
            bind_and_select_profile(second.restaurant_id)
            list(captured[0]())

        self.assertEqual(seen_restaurants, ["rest-A"])

    def test_manual_crud_routes_are_absent(self) -> None:
        from src.api.main import app

        methods_by_path = {route.path: set(route.methods or ()) for route in app.routes}
        self.assertNotIn("POST", methods_by_path.get("/api/config/stores", set()))
        self.assertFalse(
            any(path.startswith("/api/config/stores/{") for path in methods_by_path)
        )


if __name__ == "__main__":
    unittest.main()
