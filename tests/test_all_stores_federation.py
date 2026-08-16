"""Phase 2 All Stores read federation and reducer tests (plan §8.6)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from src.api.dependencies import ScopedReader, get_restaurant_profile
from src.core.analytics_scope import all_stores_scope, restaurant_scope
from src.core.profiles import (
    ALL_STORES_TOKEN,
    select_all_stores,
    upsert_allowed_restaurants,
)
from src.core.queries.multi_store import read_only_connection
from tests.all_stores_test_helpers import TwoStoreFixture, allowed


GLOBAL_MENU_CAPABILITIES = [
    "global_menu_v1",
    "global_menu_resolution_v1",
    "global_menu_aggregation_v1",
    "global_menu_mutations_v1",
]


def enroll_fixture_in_global_menu(fixture: TwoStoreFixture) -> None:
    upsert_allowed_restaurants(
        [
            {
                "restaurant_id": profile.restaurant_id,
                "display_name": profile.display_name,
                "timezone": profile.timezone,
                "menu_group_id": "group-1",
                "menu_capabilities": GLOBAL_MENU_CAPABILITIES,
            }
            for profile in fixture.profiles
        ]
    )
    for profile in fixture.profiles:
        conn = fixture.connection(profile.restaurant_id)
        try:
            conn.execute(
                """
                UPDATE global_menu_state
                SET mode='global_menu_v1', menu_group_id='group-1',
                    bootstrap_status='complete', coverage_linked=1, coverage_total=1
                WHERE singleton_id=1
                """
            )
            conn.commit()
        finally:
            conn.close()
    select_all_stores()


def link_global_item(
    fixture: TwoStoreFixture,
    restaurant_id: str,
    *,
    local_id: str,
    global_id: str,
    canonical_name: str,
    canonical_type: str = "Dessert",
) -> None:
    conn = fixture.connection(restaurant_id)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO global_menu_items (
                global_menu_item_id, menu_group_id, canonical_name, canonical_type,
                is_verified, lifecycle_state, server_revision
            ) VALUES (?, 'group-1', ?, ?, 1, 'active', 1)
            """,
            (global_id, canonical_name, canonical_type),
        )
        conn.execute(
            """
            INSERT INTO menu_item_global_links (
                local_menu_item_id, global_menu_item_id, provenance,
                server_revision, is_projection_owner
            ) VALUES (?, ?, 'server-link', 1, 1)
            """,
            (local_id, global_id),
        )
        conn.commit()
    finally:
        conn.close()


def link_global_variant(
    fixture: TwoStoreFixture,
    restaurant_id: str,
    *,
    local_id: str,
    global_id: str,
    canonical_name: str,
    unit: str,
    value: int,
) -> None:
    conn = fixture.connection(restaurant_id)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO global_variants (
                global_variant_id, menu_group_id, canonical_name, unit, value,
                is_verified, lifecycle_state, server_revision
            ) VALUES (?, 'group-1', ?, ?, ?, 1, 'active', 1)
            """,
            (global_id, canonical_name, unit, value),
        )
        conn.execute(
            """
            INSERT INTO variant_global_links (
                local_variant_id, global_variant_id, provenance,
                server_revision, is_projection_owner
            ) VALUES (?, ?, 'server-link', 1, 1)
            """,
            (local_id, global_id),
        )
        conn.commit()
    finally:
        conn.close()


class AllStoresFederationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TwoStoreFixture().start()
        self.fixture.seed_store(
            "rest-A", order_total=100.0, customer_name="Asha", item_name="Brownie"
        )
        self.fixture.seed_store(
            "rest-B", order_total=300.0, customer_name="Bala", item_name="Brownie"
        )
        self.scope = all_stores_scope()
        self.reader = ScopedReader(self.scope)

    def tearDown(self) -> None:
        self.fixture.stop()

    # --- scope resolution -------------------------------------------------

    def test_all_stores_scope_freezes_authorized_bound_profiles(self) -> None:
        self.assertEqual(
            [profile.restaurant_id for profile in self.scope.profiles], ["rest-A", "rest-B"]
        )
        # Revoking a grant later must not mutate a scope that already exists.
        upsert_allowed_restaurants(allowed(("rest-A", "Dach & Nona", "Asia/Kolkata")))
        self.assertEqual(
            [profile.restaurant_id for profile in self.scope.profiles], ["rest-A", "rest-B"]
        )
        self.assertEqual(
            [profile.restaurant_id for profile in all_stores_scope().profiles], ["rest-A"]
        )

    def test_revoked_profile_is_reported_excluded_not_dropped_silently(self) -> None:
        upsert_allowed_restaurants(allowed(("rest-A", "Dach & Nona", "Asia/Kolkata")))
        envelope = ScopedReader(all_stores_scope()).read(
            lambda conn, _profile: 1, lambda pairs: sum(value for _p, value in pairs)
        )
        self.assertEqual(envelope["profiles_included"], 1)
        self.assertEqual(
            [entry["restaurant_id"] for entry in envelope["excluded_profiles"]], ["rest-B"]
        )

    def test_federated_connections_are_read_only(self) -> None:
        conn = read_only_connection(self.fixture.profiles[0])
        try:
            with self.assertRaises(Exception):
                conn.execute("INSERT INTO menu_items (menu_item_id, name, type) VALUES ('x','X','Y')")
        finally:
            conn.close()

    def test_unreadable_profile_is_incomplete_not_zero(self) -> None:
        def query(conn, profile):
            if profile.restaurant_id == "rest-B":
                raise RuntimeError("database is locked")
            return 100.0

        envelope = self.reader.read(
            query, lambda pairs: sum(value for _profile, value in pairs)
        )
        self.assertEqual(envelope["data"], 100.0)
        self.assertEqual(envelope["profiles_requested"], 2)
        self.assertEqual(envelope["profiles_included"], 1)
        self.assertEqual(envelope["incomplete_profiles"][0]["restaurant_id"], "rest-B")

    # --- reducers ---------------------------------------------------------

    def test_kpis_sum_counts_and_recompute_average_order_value(self) -> None:
        from src.api.routers import insights

        envelope = insights.get_kpis(reader=self.reader)
        data = envelope["data"]
        self.assertEqual(data["total_orders"], 2)
        self.assertEqual(data["total_revenue"], 400.0)
        # Portfolio AOV, not the mean of 100 and 300 store averages, which would
        # coincidentally agree here only if both stores had equal order counts.
        self.assertEqual(data["avg_order_value"], 200.0)

    def test_average_order_value_is_not_an_average_of_store_averages(self) -> None:
        from src.api.routers import insights

        self.fixture.seed_store(
            "rest-B",
            order_total=500.0,
            customer_name="Bala Two",
            item_name="Brownie",
            petpooja_order_id=19471,
        )
        data = insights.get_kpis(reader=ScopedReader(all_stores_scope()))["data"]
        self.assertEqual(data["total_orders"], 3)
        self.assertEqual(data["total_revenue"], 900.0)
        self.assertEqual(data["avg_order_value"], 300.0)
        self.assertNotEqual(data["avg_order_value"], (100.0 + 400.0) / 2)

    def test_share_uses_combined_denominator(self) -> None:
        from src.api.routers import insights

        envelope = insights.get_revenue_by_category(reader=self.reader)
        data = envelope["data"]
        self.assertEqual(data["total_system_revenue"], 400.0)
        self.assertEqual(len(data["categories"]), 1)
        self.assertEqual(data["categories"][0]["revenue"], 400.0)

    def test_menu_rows_use_global_identity_and_omit_unlinked_rows(self) -> None:
        from src.api.routers import menu

        enroll_fixture_in_global_menu(self.fixture)
        link_global_item(
            self.fixture,
            "rest-A",
            local_id="mi-1",
            global_id="global-brownie",
            canonical_name="Chocolate Brownie",
        )
        link_global_item(
            self.fixture,
            "rest-B",
            local_id="mi-1",
            global_id="global-brownie",
            canonical_name="Chocolate Brownie",
        )
        conn = self.fixture.connection("rest-B")
        try:
            conn.execute(
                "INSERT INTO menu_items (menu_item_id, name, type, is_active) VALUES ('mi-2', 'Brownie', 'Beverage', 1)"
            )
            conn.commit()
        finally:
            conn.close()

        rows = menu.get_menu_items_view(
            page=1, page_size=50, sort_by="total_revenue", sort_desc=True,
            filters=None, start_date=None, end_date=None,
            reader=ScopedReader(all_stores_scope()),
        )
        data = rows["data"]["data"]
        self.assertEqual(len(data), 1)
        combined = data[0]
        self.assertEqual(combined["global_menu_item_id"], "global-brownie")
        self.assertEqual(combined["name"], "Chocolate Brownie")
        self.assertEqual(len(combined["contributors"]), 2)
        self.assertEqual(sorted(combined["restaurant_ids"]), ["rest-A", "rest-B"])
        # Local identifiers exist only inside contributors; a grouped row must
        # never inherit one store's transient ID by iteration order.
        self.assertNotIn("menu_item_id", combined)
        self.assertTrue(rows["identity_coverage"]["global_only"])
        self.assertEqual(rows["identity_coverage"]["omitted_unlinked_rows"], 1)

    def test_all_stores_group_catalog_deduplicates_cached_global_tables(self) -> None:
        from src.api.routers import menu

        enroll_fixture_in_global_menu(self.fixture)
        for restaurant_id in ("rest-A", "rest-B"):
            link_global_item(
                self.fixture,
                restaurant_id,
                local_id="mi-1",
                global_id="global-brownie",
                canonical_name="Chocolate Brownie",
            )

        envelope = menu.get_global_menu_catalog(
            reader=ScopedReader(all_stores_scope())
        )
        catalog = envelope["data"]

        self.assertEqual(catalog["menu_group_id"], "group-1")
        self.assertEqual(catalog["menu_group_ids"], ["group-1"])
        self.assertEqual(len(catalog["items"]), 1)
        item = catalog["items"][0]
        self.assertEqual(item["global_menu_item_id"], "global-brownie")
        self.assertEqual(item["restaurant_ids"], ["rest-A", "rest-B"])
        self.assertNotIn("local_menu_item_id", item)

    def test_menu_matrix_aggregates_global_pairs_only(self) -> None:
        from src.api.routers import menu

        enroll_fixture_in_global_menu(self.fixture)
        for restaurant_id, variant_id, price in (
            ("rest-A", "v-a", 120),
            ("rest-B", "v-b", 100),
        ):
            conn = self.fixture.connection(restaurant_id)
            try:
                conn.execute(
                    "INSERT INTO variants (variant_id, variant_name, unit, value) VALUES (?, ?, 'GMS', 100)",
                    (variant_id, f"Local {variant_id}"),
                )
                conn.execute(
                    """
                    INSERT INTO menu_item_variants (
                        order_item_id, menu_item_id, variant_id, price, is_active,
                        addon_eligible, delivery_eligible, is_verified
                    ) VALUES (?, 'mi-1', ?, ?, 1, 0, 1, 1)
                    """,
                    (f"assignment-{restaurant_id}", variant_id, price),
                )
                conn.commit()
            finally:
                conn.close()
            link_global_item(
                self.fixture,
                restaurant_id,
                local_id="mi-1",
                global_id="global-brownie",
                canonical_name="Chocolate Brownie",
            )
            link_global_variant(
                self.fixture,
                restaurant_id,
                local_id=variant_id,
                global_id="global-100g",
                canonical_name="100 GMS",
                unit="GMS",
                value=100,
            )

        conn = self.fixture.connection("rest-B")
        try:
            conn.execute(
                "INSERT INTO variants (variant_id, variant_name, unit, value) VALUES ('v-unlinked', 'Mystery', 'GMS', 200)"
            )
            conn.execute(
                """
                INSERT INTO menu_item_variants (
                    order_item_id, menu_item_id, variant_id, price, is_active,
                    addon_eligible, delivery_eligible, is_verified
                ) VALUES ('assignment-unlinked', 'mi-1', 'v-unlinked', 90, 1, 0, 1, 1)
                """
            )
            conn.commit()
        finally:
            conn.close()

        envelope = menu.get_menu_matrix(reader=ScopedReader(all_stores_scope()))
        rows = envelope["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["global_menu_item_id"], "global-brownie")
        self.assertEqual(rows[0]["global_variant_id"], "global-100g")
        self.assertEqual(rows[0]["name"], "Chocolate Brownie")
        self.assertEqual(rows[0]["variant_name"], "100 GMS")
        self.assertEqual(rows[0]["price"], 100)
        self.assertEqual(len(rows[0]["contributors"]), 2)
        self.assertEqual(envelope["identity_coverage"]["omitted_unlinked_rows"], 1)

    def test_hourly_average_divides_by_union_of_business_days(self) -> None:
        from src.api.routers import insights

        # rest-B trades on a second business date; the combined per-hour average
        # must divide by two days, not by one store's day count.
        self.fixture.seed_store(
            "rest-B",
            order_total=100.0,
            customer_name="Bala Two",
            item_name="Brownie",
            business_datetime="2026-08-09 12:00:00",
            petpooja_order_id=19472,
        )
        rows = insights.get_hourly_revenue(reader=ScopedReader(all_stores_scope()))["data"]
        noon = next(row for row in rows if row["hour_num"] == 12)
        self.assertEqual(noon["revenue"], 500.0)
        self.assertEqual(noon["avg_revenue"], 250.0)

    def test_zero_fill_happens_after_stores_are_combined(self) -> None:
        from src.api.routers import insights

        self.fixture.seed_store(
            "rest-B",
            order_total=100.0,
            customer_name="Bala Two",
            item_name="Brownie",
            business_datetime="2026-08-10 12:00:00",
            petpooja_order_id=19473,
        )
        rows = insights.get_avg_revenue_by_day(
            start_date="2026-08-08", end_date="2026-08-10",
            reader=ScopedReader(all_stores_scope()),
        )["data"]
        by_day = {row["day_name"]: row["value"] for row in rows}
        # 2026-08-08 Sat: 400 across both stores. 2026-08-09 Sun: 0. 2026-08-10 Mon: 100.
        self.assertEqual(by_day["Saturday"], 400.0)
        self.assertEqual(by_day["Sunday"], 0.0)
        self.assertEqual(by_day["Monday"], 100.0)

    # --- row tables -------------------------------------------------------

    def test_colliding_local_ids_stay_distinct_rows_with_attribution(self) -> None:
        from src.api.routers import orders

        view = next(
            route.endpoint
            for route in orders.router.routes
            if route.path == "/view"
        )
        payload = view(
            page=1, page_size=50, sort_by="total", sort_desc=True,
            filters=None, search=None, reader=self.reader,
        )
        rows = payload["data"]["data"]
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["petpooja_order_id"] for row in rows], [19470, 19470])
        self.assertEqual([row["restaurant_id"] for row in rows], ["rest-B", "rest-A"])
        self.assertEqual(len({row["row_key"] for row in rows}), 2)
        # The orders table's own restaurant FK is preserved, not overwritten.
        self.assertTrue(all(row["local_restaurant_id"] == 1 for row in rows))
        self.assertEqual(payload["data"]["total"], 2)

    def test_global_pagination_is_stable_across_page_boundaries(self) -> None:
        from src.api.routers import orders

        for index in range(4):
            self.fixture.seed_store(
                "rest-A" if index % 2 == 0 else "rest-B",
                order_total=50.0,
                customer_name=f"Extra {index}",
                item_name="Brownie",
                petpooja_order_id=20000 + index,
            )
        view = next(
            route.endpoint for route in orders.router.routes if route.path == "/view"
        )
        reader = ScopedReader(all_stores_scope())
        first = view(
            page=1, page_size=3, sort_by="total", sort_desc=True,
            filters=None, search=None, reader=reader,
        )["data"]["data"]
        second = view(
            page=2, page_size=3, sort_by="total", sort_desc=True,
            filters=None, search=None, reader=ScopedReader(all_stores_scope()),
        )["data"]["data"]
        keys = [row["row_key"] for row in first] + [row["row_key"] for row in second]
        self.assertEqual(len(keys), 6)
        self.assertEqual(len(set(keys)), 6)
        # Ties (four 50.0 orders) break on restaurant ID ascending, in both sort
        # directions, so a page boundary never shuffles.
        tie_rows = [row for row in first + second if row["total"] == 50.0]
        self.assertEqual(
            [row["restaurant_id"] for row in tie_rows],
            sorted(row["restaurant_id"] for row in tie_rows),
        )
        for restaurant_id in ("rest-A", "rest-B"):
            local_keys = [
                row["row_key"]
                for row in tie_rows
                if row["restaurant_id"] == restaurant_id
            ]
            self.assertEqual(local_keys, sorted(local_keys))

    def test_deep_paging_is_bounded_instead_of_scanning_every_store(self) -> None:
        from src.api.routers import orders

        view = next(
            route.endpoint for route in orders.router.routes if route.path == "/view"
        )
        with self.assertRaises(HTTPException) as caught:
            view(
                page=1000, page_size=50, sort_by="total", sort_desc=True,
                filters=None, search=None, reader=self.reader,
            )
        self.assertEqual(caught.exception.detail["code"], "deep_page_not_supported")

    def test_menu_bound_refuses_the_request_instead_of_dropping_a_store(self) -> None:
        from src.api.routers import menu

        # Exceeding the fan-out bound must surface as its own error. Reporting
        # the store as merely "incomplete" would answer with a menu that quietly
        # omits it.
        with patch.object(menu, "MENU_FEDERATION_ROW_LIMIT", 0):
            with self.assertRaises(HTTPException) as caught:
                menu.get_menu_summary(
                    mode="quantity", as_of_date="2026-08-08", page=1, page_size=50,
                    name_search=None, sort_by="lifetime", sort_desc=True,
                    reader=ScopedReader(all_stores_scope()),
                )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["code"], "deep_page_not_supported")

    def test_menu_table_bounds_refuse_truncated_items_and_variants(self) -> None:
        from src.api.routers import menu

        for restaurant_id in ("rest-A", "rest-B"):
            conn = self.fixture.connection(restaurant_id)
            try:
                conn.execute(
                    """
                    INSERT INTO variants (variant_id, variant_name, unit, value)
                    VALUES ('v-1', 'Regular', 'PIECES', 1)
                    """
                )
                conn.commit()
            finally:
                conn.close()

        calls = (
            lambda: menu.get_menu_items_view(
                page=1, page_size=50, sort_by="total_revenue", sort_desc=True,
                filters=None, start_date=None, end_date=None,
                reader=ScopedReader(all_stores_scope()),
            ),
            lambda: menu.get_variants_view(
                page=1, page_size=50, sort_by="variant_name", sort_desc=False,
                filters=None, reader=ScopedReader(all_stores_scope()),
            ),
        )
        with patch.object(menu, "MENU_FEDERATION_ROW_LIMIT", 0):
            for call in calls:
                with self.subTest(surface=call):
                    with self.assertRaises(HTTPException) as caught:
                        call()
                    self.assertEqual(caught.exception.status_code, 400)
                    self.assertEqual(
                        caught.exception.detail["code"], "deep_page_not_supported"
                    )

    def test_null_sort_values_order_the_way_the_per_store_sql_did(self) -> None:
        from src.core.queries.multi_store_reducers import sort_key

        # SQLite treats NULL as smallest: first ascending, last descending.
        values = [None, 5, "a"]
        self.assertEqual(sorted(values, key=sort_key), [None, 5, "a"])
        self.assertEqual(sorted(values, key=sort_key, reverse=True), ["a", 5, None])

    # --- customers --------------------------------------------------------

    def test_customers_are_profile_qualified_never_merged(self) -> None:
        from src.api.routers import customer_analytics

        envelope = customer_analytics.get_top_customers(reader=self.reader)
        rows = envelope["data"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["customer_id"] for row in rows}, {41})
        self.assertEqual({row["restaurant_id"] for row in rows}, {"rest-A", "rest-B"})
        self.assertEqual(len({row["row_key"] for row in rows}), 2)

    def test_customer_metrics_recompute_from_combined_order_atoms(self) -> None:
        from src.api.routers import customer_analytics

        envelope = customer_analytics.get_repeat_order_rate_analysis(
            evaluation_start_date="2026-08-01",
            evaluation_end_date="2026-08-31",
            min_orders_per_customer=2,
            order_sources=None,
            reader=self.reader,
        )
        summary = envelope["data"]["summary"]
        # Two store-customers, neither repeating: a customer counted twice by a
        # naive merge would have looked like one repeat customer.
        self.assertEqual(summary["total_customers"], 2)
        self.assertEqual(summary["repeat_order_customers"], 0)
        self.assertEqual(summary["repeat_order_rate"], 0.0)
        rows = envelope["data"]["rows"]
        self.assertEqual({row["customer_id"] for row in rows}, {41})
        self.assertEqual({row["restaurant_id"] for row in rows}, {"rest-A", "rest-B"})

    # --- All Stores refusals ---------------------------------------------

    def test_state_changing_routes_refuse_all_mode_before_opening_a_database(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            get_restaurant_profile(x_analytics_scope=ALL_STORES_TOKEN)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "single_restaurant_required")

    def test_endpoint_without_a_reducer_fails_closed_in_all_mode(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            self.reader.read(lambda conn, _profile: 1)
        self.assertEqual(caught.exception.detail["code"], "single_restaurant_required")

    def test_all_token_never_becomes_a_profile_identity_or_filename(self) -> None:
        from src.core.db.control import get_control_connection
        from src.core.profiles import profile_path_for, validate_restaurant_id

        with self.assertRaises(Exception):
            validate_restaurant_id(ALL_STORES_TOKEN)
        with self.assertRaises(Exception):
            profile_path_for(ALL_STORES_TOKEN)

        conn = get_control_connection()
        try:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM restaurant_profiles WHERE restaurant_id=?",
                    (ALL_STORES_TOKEN,),
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM app_selection WHERE restaurant_id=?",
                    (ALL_STORES_TOKEN,),
                ).fetchone()
            )
        finally:
            conn.close()

        for profile in self.fixture.profiles:
            self.assertNotIn(ALL_STORES_TOKEN, profile.database_path)
            conn = self.fixture.connection(profile.restaurant_id)
            try:
                identity = conn.execute(
                    "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
                ).fetchone()[0]
                self.assertNotEqual(identity, ALL_STORES_TOKEN)
            finally:
                conn.close()

    def test_all_stores_selection_requires_two_initialized_profiles(self) -> None:
        from src.core.profiles import AllStoresUnavailable, select_all_stores

        select_all_stores()
        upsert_allowed_restaurants(allowed(("rest-A", "Dach & Nona", "Asia/Kolkata")))
        with self.assertRaises(AllStoresUnavailable):
            select_all_stores()


class AllStoresTimezoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TwoStoreFixture(
            timezones=("Pacific/Kiritimati", "Pacific/Midway")
        ).start()

    def tearDown(self) -> None:
        self.fixture.stop()

    def test_one_instant_yields_each_profile_local_business_date(self) -> None:
        from src.core.queries.multi_store import run_for_profiles
        from src.core.utils.business_date import get_current_business_date

        # +14:00 and -11:00: at this instant the two stores are on different
        # calendar days, so one clock reading must not label both.
        as_of = datetime(2026, 8, 8, 6, 0, tzinfo=dt_timezone.utc)
        outcome = run_for_profiles(
            self.fixture.profiles,
            lambda conn, _profile: get_current_business_date(),
            as_of=as_of,
        )
        self.assertEqual(outcome.values, ["2026-08-08", "2026-08-07"])

    def test_today_summary_labels_the_furthest_ahead_business_date(self) -> None:
        from src.api.routers import today

        # `Max()` on a business-date label must return the later date, not None:
        # a numbers-only extreme rule silently blanked the whole field.
        reader = ScopedReader(all_stores_scope())
        reader.as_of = datetime(2026, 8, 8, 6, 0, tzinfo=dt_timezone.utc)
        data = today.get_today_summary(date=None, reader=reader)["data"]
        self.assertEqual(data["date"], "2026-08-08")

    def test_menu_date_defaults_are_computed_inside_each_profile_context(self) -> None:
        from src.api.routers import menu

        reader = ScopedReader(all_stores_scope())
        reader.as_of = datetime(2026, 8, 8, 6, 0, tzinfo=dt_timezone.utc)
        summary = menu.get_menu_summary(
            mode="quantity", as_of_date=None, page=1, page_size=50,
            name_search=None, sort_by="lifetime", sort_desc=True,
            reader=reader,
        )["data"]
        self.assertEqual(
            summary["as_of_dates"],
            {"rest-A": "2026-08-08", "rest-B": "2026-08-07"},
        )

        timeseries = menu.get_menu_summary_timeseries(
            menu_item_ids="mi-1", start_date=None, end_date=None,
            reader=reader,
        )["data"]
        self.assertEqual(
            timeseries["end_dates"],
            {"rest-A": "2026-08-08", "rest-B": "2026-08-07"},
        )

    def test_request_captures_one_instant_for_every_store(self) -> None:
        reader = ScopedReader(all_stores_scope())
        reader.as_of = datetime(2026, 8, 8, 6, 0, tzinfo=dt_timezone.utc)
        seen = []

        def query(conn, _profile):
            from src.core.utils.business_date import current_business_date_context

            context = current_business_date_context()
            seen.append(context.as_of)
            return context.timezone

        envelope = reader.read(query, lambda pairs: [value for _p, value in pairs])
        self.assertEqual(envelope["data"], ["Pacific/Kiritimati", "Pacific/Midway"])
        self.assertEqual(len(set(seen)), 1)


class SingleStoreScopeTests(unittest.TestCase):
    """A restaurant scope must keep the pre-Phase-2 response shape."""

    def setUp(self) -> None:
        self.fixture = TwoStoreFixture().start()
        self.fixture.seed_store(
            "rest-A", order_total=100.0, customer_name="Asha", item_name="Brownie"
        )

    def tearDown(self) -> None:
        self.fixture.stop()

    def test_single_store_response_is_not_wrapped_in_an_envelope(self) -> None:
        from src.api.routers import insights

        reader = ScopedReader(restaurant_scope(self.fixture.profiles[0]))
        data = insights.get_kpis(reader=reader)
        self.assertNotIn("profiles_requested", data)
        self.assertEqual(data["total_orders"], 1)
        self.assertEqual(data["total_revenue"], 100.0)

    def test_single_store_table_keeps_page_metadata_at_top_level(self) -> None:
        from src.api.routers import orders

        view = next(
            route.endpoint for route in orders.router.routes if route.path == "/view"
        )
        payload = view(
            page=1, page_size=10, sort_by="total", sort_desc=True,
            filters=None, search=None,
            reader=ScopedReader(restaurant_scope(self.fixture.profiles[0])),
        )
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 10)
        self.assertEqual(payload["total"], 1)
        self.assertNotIn("restaurant_name", payload["data"][0])



class AllStoresForecastTests(unittest.TestCase):
    """Forecast combination rules (plan §7.5)."""

    def setUp(self) -> None:
        self.fixture = TwoStoreFixture().start()
        self.profiles = self.fixture.profiles

    def tearDown(self) -> None:
        self.fixture.stop()

    @staticmethod
    def _revenue_payload(revenue: float, orders: int, *, awaiting: bool = False):
        if awaiting:
            return {
                "summary": {"generated_at": "2026-08-08", "projected_7d_revenue": 0, "projected_7d_orders": 0},
                "historical": [],
                "forecasts": {"weekday_avg": [], "holt_winters": [], "prophet": [], "gp": []},
                "debug_info": {"awaiting_action": True, "message": "Forecast cache is empty."},
            }
        return {
            "summary": {
                "generated_at": "2026-08-08",
                "projected_7d_revenue": revenue,
                "projected_7d_orders": orders,
            },
            "historical": [
                {"sale_date": "2026-08-07", "revenue": revenue, "orders": orders,
                 "temp_max": 31.0, "rain_category": "Dry"},
            ],
            "forecasts": {
                "weekday_avg": [
                    {"date": "2026-08-09", "revenue": revenue, "orders": orders,
                     "temp_max": 31.0, "rain_category": "Dry"},
                ],
                "holt_winters": [],
                "prophet": [],
                "gp": [
                    {"date": "2026-08-09", "revenue": revenue, "orders": orders,
                     "gp_lower": revenue - 10, "gp_upper": revenue + 10},
                ],
            },
            "debug_info": {"served_from_central": True, "run_id": "run-1", "using_fallback": False},
        }

    def test_revenue_forecast_sums_values_and_labels_the_interval_envelope(self) -> None:
        from src.core.queries.multi_store_forecast import combine_revenue_forecast

        combined = combine_revenue_forecast(
            [
                (self.profiles[0], self._revenue_payload(1000.0, 10)),
                (self.profiles[1], self._revenue_payload(500.0, 5)),
            ]
        )
        self.assertEqual(combined["summary"]["projected_7d_revenue"], 1500.0)
        self.assertEqual(combined["summary"]["projected_7d_orders"], 15)
        weekday = combined["forecasts"]["weekday_avg"][0]
        self.assertEqual(weekday["revenue"], 1500.0)
        self.assertEqual(weekday["orders"], 15)
        # One store's weather is not the virtual store's weather.
        self.assertNotIn("temp_max", weekday)
        self.assertNotIn("rain_category", weekday)
        gp = combined["forecasts"]["gp"][0]
        self.assertEqual((gp["gp_lower"], gp["gp_upper"]), (1480.0, 1520.0))
        self.assertEqual(combined["debug_info"]["interval_basis"], "summed_store_bounds")
        self.assertFalse(combined["debug_info"]["forecast_incomplete"])

    def test_a_store_without_forecasts_makes_the_result_incomplete_not_zero(self) -> None:
        from src.core.queries.multi_store_forecast import (
            combine_revenue_forecast,
            lift_awaiting_profiles,
        )

        combined = combine_revenue_forecast(
            [
                (self.profiles[0], self._revenue_payload(1000.0, 10)),
                (self.profiles[1], self._revenue_payload(0.0, 0, awaiting=True)),
            ]
        )
        self.assertTrue(combined["debug_info"]["forecast_incomplete"])
        self.assertEqual(
            [entry["restaurant_id"] for entry in combined["debug_info"]["incomplete_stores"]],
            ["rest-B"],
        )
        # The missing store contributes nothing rather than a zero forecast.
        self.assertEqual(combined["summary"]["projected_7d_revenue"], 1000.0)

        envelope = lift_awaiting_profiles(
            {"scope": "all", "incomplete_profiles": [], "data": combined}, True
        )
        self.assertEqual(
            [entry["restaurant_id"] for entry in envelope["incomplete_profiles"]], ["rest-B"]
        )
        self.assertNotIn("awaiting_profiles", envelope["data"])

    def test_item_forecasts_group_by_durable_name_and_take_the_weakest_probability(self) -> None:
        from src.core.queries.multi_store_forecast import combine_item_forecast

        def payload(item_id: str, p50: float, probability: float):
            return {
                "items": [{"item_id": item_id, "item_name": "Brownie"}],
                "history": [{"date": "2026-08-07", "item_id": item_id, "qty": 3}],
                "forecast": [
                    {
                        "date": "2026-08-09",
                        "item_id": item_id,
                        "item_name": "Brownie",
                        "p50": p50,
                        "p90": p50 + 2,
                        "probability": probability,
                        "recommended_prep": int(p50),
                    }
                ],
                "backtest": [],
                "debug_info": {"served_from_central": True, "run_id": "run-1"},
            }

        combined = combine_item_forecast(
            [
                (self.profiles[0], payload("store-a-item-1", 10.0, 0.9)),
                (self.profiles[1], payload("store-b-item-9", 4.0, 0.6)),
            ]
        )
        row = combined["forecast"][0]
        self.assertEqual(row["item_name"], "Brownie")
        self.assertEqual(row["p50"], 14.0)
        self.assertEqual(row["recommended_prep"], 14)
        # Probability is the weakest store's, never averaged or multiplied.
        self.assertEqual(row["probability"], 0.6)
        self.assertEqual(combined["debug_info"]["probability_basis"], "min_store")
        self.assertEqual(
            sorted(entry["item_id"] for entry in combined["items"][0]["contributors"]),
            ["store-a-item-1", "store-b-item-9"],
        )
        self.assertEqual(combined["history"][0]["qty"], 6)

    def test_volume_forecasts_never_add_across_different_units(self) -> None:
        from src.core.queries.multi_store_forecast import combine_volume_forecast

        def payload(unit: str, volume: float):
            return {
                "items": [{"item_id": "item-1", "item_name": "Cold Coffee"}],
                "history": [],
                "forecast": [
                    {
                        "date": "2026-08-09",
                        "item_id": "item-1",
                        "item_name": "Cold Coffee",
                        "unit": unit,
                        "p50": volume,
                        "p90": volume,
                        "probability": 0.8,
                        "volume_value": volume,
                        "recommended_volume": volume,
                    }
                ],
                "backtest": [],
                "debug_info": {"served_from_central": True, "run_id": "run-1"},
            }

        combined = combine_volume_forecast(
            [(self.profiles[0], payload("ml", 500.0)), (self.profiles[1], payload("g", 200.0))]
        )
        by_unit = {row["unit"]: row for row in combined["forecast"]}
        self.assertEqual(sorted(by_unit), ["g", "ml"])
        self.assertEqual(by_unit["ml"]["volume_value"], 500.0)
        self.assertEqual(by_unit["g"]["volume_value"], 200.0)


class AllStoresIsolationAndBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TwoStoreFixture().start()
        self.fixture.seed_store(
            "rest-A", order_total=100.0, customer_name="Asha", item_name="Brownie"
        )
        self.fixture.seed_store(
            "rest-B", order_total=300.0, customer_name="Bala", item_name="Brownie"
        )

    def tearDown(self) -> None:
        self.fixture.stop()

    def test_replaying_an_order_in_one_store_cannot_touch_the_other_profile(self) -> None:
        before = self._store_snapshot("rest-B")
        conn = self.fixture.connection("rest-A")
        try:
            # Replay: the same Petpooja order arrives again with a new total.
            conn.execute(
                "UPDATE orders SET total = 175.0 WHERE petpooja_order_id = 19470"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(self._store_snapshot("rest-B"), before)
        conn = self.fixture.connection("rest-A")
        try:
            self.assertEqual(
                conn.execute("SELECT total FROM orders WHERE petpooja_order_id=19470").fetchone()[0],
                175.0,
            )
        finally:
            conn.close()

    def _store_snapshot(self, restaurant_id: str):
        conn = self.fixture.connection(restaurant_id)
        try:
            return conn.execute(
                "SELECT petpooja_order_id, total, customer_id FROM orders ORDER BY order_id"
            ).fetchall()
        finally:
            conn.close()

    def test_variants_use_global_identity_and_omit_unlinked_rows(self) -> None:
        from src.api.routers import menu

        enroll_fixture_in_global_menu(self.fixture)
        for restaurant_id, rows in (
            ("rest-A", [("v-1", "Regular 250ml", "ML", 250), ("v-2", "Large 500ml", "ML", 500)]),
            ("rest-B", [("v-9", "Regular 250ml", "ML", 250), ("v-8", "Regular 250g", "G", 250)]),
        ):
            conn = self.fixture.connection(restaurant_id)
            try:
                conn.executemany(
                    "INSERT INTO variants (variant_id, variant_name, unit, value) VALUES (?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

        link_global_variant(
            self.fixture,
            "rest-A",
            local_id="v-1",
            global_id="global-regular",
            canonical_name="Regular",
            unit="ML",
            value=250,
        )
        link_global_variant(
            self.fixture,
            "rest-B",
            local_id="v-9",
            global_id="global-regular",
            canonical_name="Regular",
            unit="ML",
            value=250,
        )
        link_global_variant(
            self.fixture,
            "rest-A",
            local_id="v-2",
            global_id="global-large",
            canonical_name="Large",
            unit="ML",
            value=500,
        )

        envelope = menu.get_variants_view(
            page=1, page_size=50, sort_by="variant_name", sort_desc=False,
            filters=None, reader=ScopedReader(all_stores_scope()),
        )
        payload = envelope["data"]
        keyed = {row["global_variant_id"]: row for row in payload["data"]}
        self.assertEqual(payload["total"], 2)
        self.assertEqual(keyed["global-regular"]["variant_name"], "Regular")
        self.assertEqual(keyed["global-regular"]["unit"], "ML")
        self.assertEqual(keyed["global-regular"]["value"], 250)
        self.assertEqual(len(keyed["global-regular"]["contributors"]), 2)
        self.assertEqual(len(keyed["global-large"]["contributors"]), 1)
        self.assertEqual(envelope["identity_coverage"]["omitted_unlinked_rows"], 1)

    def test_one_request_opens_each_profile_exactly_once(self) -> None:
        """Performance budget: fan-out cost is linear in stores, never quadratic."""
        from src.api.routers import insights
        from src.core.queries import multi_store

        opened = []
        original = multi_store.read_only_connection

        def counting_connection(profile):
            opened.append(profile.restaurant_id)
            return original(profile)

        with patch.object(multi_store, "read_only_connection", counting_connection):
            insights.get_kpis(reader=ScopedReader(all_stores_scope()))

        self.assertEqual(opened, ["rest-A", "rest-B"])


if __name__ == "__main__":
    unittest.main()


class AllStoresHttpSurfaceTests(unittest.TestCase):
    """Header-level checks through the real ASGI app."""

    def setUp(self) -> None:
        self.fixture = TwoStoreFixture().start()
        self.fixture.seed_store(
            "rest-A", order_total=100.0, customer_name="Asha", item_name="Brownie"
        )
        self.fixture.seed_store(
            "rest-B", order_total=300.0, customer_name="Bala", item_name="Brownie"
        )
        from fastapi.testclient import TestClient

        from src.api.main import app

        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.fixture.stop()

    def test_all_scope_header_returns_a_completeness_envelope(self) -> None:
        response = self.client.get(
            "/api/insights/kpis", headers={"X-Analytics-Scope": ALL_STORES_TOKEN}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["scope"], "all")
        self.assertEqual(body["profiles_requested"], 2)
        self.assertEqual(body["data"]["total_revenue"], 400.0)
        self.assertEqual(
            [store["restaurant_id"] for store in body["stores"]], ["rest-A", "rest-B"]
        )

    def test_all_scope_exposes_the_global_group_catalog(self) -> None:
        enroll_fixture_in_global_menu(self.fixture)
        for restaurant_id in ("rest-A", "rest-B"):
            link_global_item(
                self.fixture,
                restaurant_id,
                local_id="mi-1",
                global_id="global-brownie",
                canonical_name="Chocolate Brownie",
            )

        response = self.client.get(
            "/api/menu/global/catalog",
            headers={"X-Analytics-Scope": ALL_STORES_TOKEN},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["scope"], "all")
        self.assertEqual(body["data"]["menu_group_id"], "group-1")
        self.assertEqual(len(body["data"]["items"]), 1)
        self.assertEqual(
            body["data"]["items"][0]["global_menu_item_id"], "global-brownie"
        )

    def test_single_scope_header_keeps_the_plain_response(self) -> None:
        response = self.client.get(
            "/api/insights/kpis", headers={"X-Analytics-Scope": "rest-A"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_revenue"], 100.0)

    def test_state_changing_requests_are_refused_in_all_mode(self) -> None:
        blocked = [
            ("post", "/api/menu/retype", {"menu_item_id": "mi-1", "new_type": "Beverage"}),
            (
                "post",
                "/api/menu/global/mutations/preview",
                {"action": {"mutation_type": "merge"}},
            ),
            ("post", "/api/orders/customers/merge", {"source_customer_id": "1", "target_customer_id": "2"}),
            ("post", "/api/system/reset", None),
            ("post", "/api/sql/query", {"query": "SELECT 1"}),
            ("post", "/api/config/petpooja-sync", {"api_key": "k"}),
            # The weather router carries its own prefix on top of /api/weather.
            ("post", "/api/weather/weather/sync", None),
        ]
        for method, path, payload in blocked:
            with self.subTest(path=path):
                response = getattr(self.client, method)(
                    path, json=payload, headers={"X-Analytics-Scope": ALL_STORES_TOKEN}
                )
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(
                    response.json()["detail"]["code"], "single_restaurant_required"
                )

    def test_sync_run_in_all_mode_captures_every_store(self) -> None:
        with patch("src.api.job_manager.JobManager.start_job", return_value="job-x"):
            response = self.client.post(
                "/api/sync/run",
                json={"restaurant_id": ALL_STORES_TOKEN},
                headers={"X-Analytics-Scope": ALL_STORES_TOKEN},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["message"], "Sync started for 2 store(s)")


class ControlSchemaUpgradeTests(unittest.TestCase):
    """A revision-1 control database must accept the All Stores selection."""

    def setUp(self) -> None:
        import os
        import tempfile

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.env_patch = patch.dict(
            os.environ,
            {
                "ANALYTICS_APP_DATA_ROOT": str(self.root),
                "ANALYTICS_DB_PATH": str(self.root / "analytics.db"),
                "ANALYTICS_CONTROL_DB_PATH": str(self.root / "analytics-control.db"),
                "DB_URL": str(self.root / "analytics.db"),
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_revision_one_selection_table_is_migrated_in_place(self) -> None:
        import sqlite3

        from src.core.db.control import control_db_path, ensure_control_schema

        legacy = sqlite3.connect(str(control_db_path()))
        legacy.executescript(
            """
            CREATE TABLE restaurant_profiles (
                restaurant_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                timezone TEXT NOT NULL,
                database_path TEXT NOT NULL UNIQUE,
                local_address TEXT,
                authorization_state TEXT NOT NULL,
                last_listed_at TEXT,
                last_sync_status TEXT,
                last_sync_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE app_selection (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                selection_mode TEXT NOT NULL CHECK (selection_mode = 'restaurant'),
                restaurant_id TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (restaurant_id) REFERENCES restaurant_profiles(restaurant_id)
            );
            INSERT INTO restaurant_profiles (
                restaurant_id, display_name, timezone, database_path, authorization_state
            ) VALUES ('rest-A', 'Dach & Nona', 'Asia/Kolkata', 'legacy.db', 'authorized');
            INSERT INTO app_selection (singleton_id, selection_mode, restaurant_id)
            VALUES (1, 'restaurant', 'rest-A');
            """
        )
        legacy.commit()
        legacy.close()

        ensure_control_schema()

        conn = sqlite3.connect(str(control_db_path()))
        try:
            # The existing physical selection survives the rebuild…
            self.assertEqual(
                conn.execute(
                    "SELECT selection_mode, restaurant_id FROM app_selection WHERE singleton_id=1"
                ).fetchone(),
                ("restaurant", "rest-A"),
            )
            # …and 'all' is now storable, with no restaurant ID attached.
            conn.execute(
                """
                INSERT INTO app_selection (singleton_id, selection_mode, restaurant_id)
                VALUES (1, 'all', NULL)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    selection_mode='all', restaurant_id=NULL
                """
            )
            conn.commit()
            self.assertEqual(
                conn.execute("SELECT selection_mode, restaurant_id FROM app_selection").fetchone(),
                ("all", None),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE app_selection SET selection_mode='all', restaurant_id='__all__'"
                )
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='app_selection_legacy'"
            ).fetchone())
        finally:
            conn.close()
