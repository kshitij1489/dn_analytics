import sqlite3
import unittest
from unittest.mock import ANY, Mock, patch

from src.api.routers import operations
from src.core.services.sync_service import SyncStatus


class SyncOperationsTests(unittest.TestCase):
    def test_rebuilding_sync_job_refreshes_registry_before_opening_profile(self) -> None:
        from src.core.analytics_scope import restaurant_scope
        from src.core.profiles import RestaurantProfile

        profile = RestaurantProfile(
            restaurant_id="rest-1",
            display_name="Dach & Nona",
            timezone="Asia/Kolkata",
            database_path="/tmp/rest-1.db",
            authorization_state="authorized",
            is_bound=True,
            menu_group_id="group-1",
            menu_capabilities=(
                "global_menu_v1",
                "global_menu_shared_pos_catalog_v1",
            ),
            clean_rebuild_status="rebuilding",
        )
        captured = []
        order = []

        with patch.object(
            operations.JobManager,
            "start_job",
            side_effect=lambda factory: captured.append(factory) or "job-1",
        ), patch(
            "src.core.profiles.refresh_allowed_restaurants_from_server",
            side_effect=lambda: order.append("registry"),
        ), patch(
            "src.core.profiles.get_profile",
            return_value=profile,
        ), patch.object(
            operations,
            "get_profile_connection",
            side_effect=lambda _profile: (order.append("open") or Mock(), "ok"),
        ), patch.object(
            operations,
            "iter_sync_statuses",
            side_effect=lambda _conn: iter(
                [order.append("sync") or SyncStatus("done", "complete")]
            ),
        ):
            operations.run_sync(
                operations.SyncRunRequest(restaurant_id="rest-1"),
                scope=restaurant_scope(profile),
            )
            statuses = list(captured[0]())

        self.assertEqual(order, ["registry", "open", "sync"])
        self.assertEqual(statuses[-1].type, "done")

    def test_post_order_orchestrator_skips_duplicate_catalog_and_orders_shared_steps(self) -> None:
        from src.core.services.cloud_pull_orchestrator import (
            _run_best_effort_cloud_pulls_locked,
        )

        conn = Mock()
        order = []
        capability = Mock(
            active=True,
            shared_pos_catalog_advertised=True,
        )
        with patch(
            "src.core.config.cloud_sync_config.get_cloud_sync_config",
            return_value=("https://cloud.example", "sync-key"),
        ), patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=capability,
        ), patch(
            "src.core.global_menu_sync.pull_global_menu_state"
        ) as duplicate_catalog, patch(
            "src.core.global_menu_sync.pull_global_assignment_snapshot",
            side_effect=lambda *_args, **_kwargs: order.append("assignments")
            or {"status": "applied"},
        ), patch(
            "src.core.global_menu_history.pull_global_menu_history",
            side_effect=lambda *_args, **_kwargs: order.append("history")
            or {"status": "applied"},
        ), patch(
            "src.core.client_learning_shipper.run_scoped_uploads",
            side_effect=lambda *_args, **_kwargs: order.append("observation")
            or {"sent": True, "error": None},
        ), patch(
            "src.core.menu_bootstrap_sync.get_menu_bootstrap_pull_endpoint",
            return_value=None,
        ), patch(
            "src.core.menu_assignment_bootstrap.get_menu_assignments_snapshot_endpoint",
            return_value=None,
        ), patch(
            "src.core.menu_mapping_verification_sync.get_menu_mapping_verification_pull_endpoint",
            return_value=None,
        ), patch(
            "src.core.menu_merge_sync.get_menu_merge_pull_endpoint",
            return_value=None,
        ), patch(
            "src.core.customer_merge_sync.get_customer_merge_pull_endpoint",
            return_value=None,
        ), patch(
            "src.core.forecast_sync.get_forecast_delta_endpoint",
            return_value=None,
        ):
            summary = _run_best_effort_cloud_pulls_locked(
                conn,
                skip_global_menu_state=True,
                send_shared_pos_observation=True,
            )

        duplicate_catalog.assert_not_called()
        self.assertEqual(order, ["assignments", "history", "observation"])
        self.assertEqual(summary["global_menu"]["status"], "already_current")
        self.assertTrue(summary["shared_pos_observation"]["sent"])

    def test_clean_rebuild_runs_catalog_orders_assignments_history_observation_then_finalizes(self) -> None:
        conn = Mock()
        order = []
        active = Mock(
            active=True,
            shared_pos_catalog_advertised=True,
        )
        cloud_summary = {
            "attempted": True,
            "global_menu": {"status": "already_current"},
            "global_menu_assignments": {"status": "applied"},
            "global_menu_history": {"status": "applied"},
            "shared_pos_observation": {"sent": True, "error": None},
        }
        diagnostics = {
            "bootstrap_state": "complete",
            "catalog_revision": 7,
            "mapping_count": 2,
            "price_count": 2,
            "assignment_coverage": {"linked": 2, "total": 2, "complete": True},
            "history_count": 3,
            "history_cursor": None,
            "quarantine_count": 0,
        }

        def order_sync(_conn):
            order.append("orders")
            return iter(
                [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
            )

        def cloud_pull(_conn, **kwargs):
            order.append("assignments-history-observation")
            self.assertTrue(kwargs["skip_global_menu_state"])
            self.assertTrue(kwargs["send_shared_pos_observation"])
            return cloud_summary

        with patch(
            "src.core.profiles.profile_rebuild_status_for_connection",
            return_value="rebuilding",
        ), patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=active,
        ), patch(
            "src.core.global_menu_sync.pull_global_menu_state",
            side_effect=lambda *_args, **_kwargs: order.append("catalog-events")
            or {"status": "applied"},
        ), patch(
            "src.api.routers.operations.sync_database", side_effect=order_sync
        ), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            side_effect=cloud_pull,
        ), patch(
            "src.core.queries.global_menu_diagnostics.fetch_global_menu_diagnostics",
            return_value=diagnostics,
        ), patch(
            "src.core.profiles.complete_profile_rebuild_for_connection",
            return_value=True,
        ) as complete:
            terminal = list(operations.iter_sync_statuses(conn))[-1]

        self.assertEqual(
            order,
            ["catalog-events", "orders", "assignments-history-observation"],
        )
        self.assertEqual(terminal.type, "done")
        self.assertEqual(terminal.stats["clean_rebuild_status"], "complete")
        self.assertEqual(terminal.stats["global_menu_diagnostics"], diagnostics)
        complete.assert_called_once_with(conn)

    def test_global_menu_pull_runs_before_order_ingest(self) -> None:
        conn = Mock()
        order = []

        def order_sync(_conn):
            order.append("orders")
            return iter(
                [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 0})]
            )

        active = Mock(active=True)
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=active,
        ), patch(
            "src.core.global_menu_sync.pull_global_menu_state",
            side_effect=lambda _conn, **_kwargs: order.append("global") or {"status": "applied"},
        ), patch(
            "src.api.routers.operations.sync_database", side_effect=order_sync
        ), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value={"attempted": False},
        ):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(order, ["global", "orders"])
        self.assertEqual(statuses[-1].type, "done")

    def test_failed_mandatory_global_pull_prevents_order_ingest(self) -> None:
        conn = Mock()
        active = Mock(active=True)
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=active,
        ), patch(
            "src.core.global_menu_sync.pull_global_menu_state",
            return_value={"status": "error", "error": "bad redirect"},
        ), patch("src.api.routers.operations.sync_database") as order_sync:
            statuses = list(operations.iter_sync_statuses(conn))

        order_sync.assert_not_called()
        self.assertEqual(statuses[-1].type, "error")
        self.assertEqual(statuses[-1].code, "global_menu_sync_failed")

    def test_iter_sync_statuses_waits_for_cloud_pull_before_done(self) -> None:
        conn = Mock()
        cloud_summary = {
            "attempted": True,
            "customer_merges": {"merge_events_applied": 2, "undo_events_applied": 0, "error": None},
            "menu_bootstrap": None,
            "menu_merges": None,
        }
        sync_statuses = iter(
            [
                SyncStatus("info", "Preparing sync", progress=0.25),
                SyncStatus("done", "No new orders to sync", progress=1.0, stats={"fetched": 0}),
            ]
        )

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ) as cloud_pull, patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual([status.type for status in statuses], ["info", "info", "done"])
        self.assertEqual(statuses[-1].message, "No new orders to sync · Cloud pull finished")
        self.assertEqual(statuses[-1].stats["cloud_pull"], cloud_summary)
        cloud_pull.assert_called_once_with(
            conn, on_phase=ANY, skip_menu_bootstrap=False, already_locked=False
        )

    def test_cloud_pull_steps_are_reported_while_the_pull_is_still_running(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "No new orders to sync", progress=1.0, stats={"fetched": 0})]
        )

        def slow_pull(_conn, *, on_phase, **_kwargs):
            on_phase("Pulling menu history...")
            on_phase("Pulling customer merges...")
            return {"attempted": True, "customer_merges": None}

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            side_effect=slow_pull,
        ), patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(
            [status.message for status in statuses[:-1]],
            [
                "Order sync complete. Pulling cloud data...",
                "Pulling menu history...",
                "Pulling customer merges...",
            ],
        )
        self.assertEqual(statuses[-1].type, "done")

    def test_a_failing_cloud_pull_still_raises_through_the_phase_stream(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "No new orders to sync", progress=1.0, stats={"fetched": 0})]
        )

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            side_effect=RuntimeError("pull exploded"),
        ), patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            with self.assertRaises(RuntimeError) as caught:
                list(operations.iter_sync_statuses(conn))

        self.assertEqual(str(caught.exception), "pull exploded")

    def test_iter_sync_statuses_does_not_run_cloud_pull_after_error(self) -> None:
        conn = Mock()
        sync_statuses = iter([SyncStatus("error", "Sync failed", progress=0.4)])

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls"
        ) as cloud_pull, patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].type, "error")
        cloud_pull.assert_not_called()

    def test_iter_sync_statuses_pulls_menu_bootstrap_before_orders_when_catalog_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE menu_items (menu_item_id TEXT PRIMARY KEY)")
        conn.commit()

        sync_statuses = iter(
            [SyncStatus("done", "No new orders to sync", progress=1.0, stats={"fetched": 0})]
        )
        bootstrap_result = {"items_seeded": 3, "error": None}
        cloud_summary = {"attempted": True, "menu_bootstrap": None}

        with patch(
            "src.core.menu_bootstrap_sync.get_menu_bootstrap_pull_endpoint",
            return_value="https://cloud.example/menu-bootstrap/latest",
        ), patch(
            "src.core.menu_bootstrap_sync.get_menu_bootstrap_apply_mode",
            return_value="seed_only",
        ), patch(
            "src.core.config.cloud_sync_config.get_cloud_sync_config",
            return_value=("https://cloud.example", "test-key"),
        ), patch(
            "src.core.menu_bootstrap_sync.fetch_and_apply_menu_bootstrap_snapshot",
            return_value=bootstrap_result,
        ) as bootstrap_pull, patch(
            "src.api.routers.operations.sync_database",
            return_value=sync_statuses,
        ) as order_sync, patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ) as cloud_pull:
            statuses = list(operations.iter_sync_statuses(conn))

        bootstrap_pull.assert_called_once()
        order_sync.assert_called_once_with(conn)
        cloud_pull.assert_called_once_with(
            conn, on_phase=ANY, skip_menu_bootstrap=True, already_locked=False
        )
        self.assertEqual(statuses[0].message, "Menu catalog empty — pulling from cloud before order sync...")
        self.assertIn("3 items", statuses[1].message)
        self.assertEqual(statuses[-1].type, "done")
        conn.close()

    def test_iter_sync_statuses_skips_pre_bootstrap_when_catalog_populated(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE menu_items (menu_item_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO menu_items (menu_item_id) VALUES ('item_1')")
        conn.commit()

        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )

        with patch(
            "src.core.menu_bootstrap_sync.fetch_and_apply_menu_bootstrap_snapshot"
        ) as bootstrap_pull, patch(
            "src.api.routers.operations.sync_database",
            return_value=sync_statuses,
        ), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value={"attempted": False},
        ) as cloud_pull:
            statuses = list(operations.iter_sync_statuses(conn))

        bootstrap_pull.assert_not_called()
        cloud_pull.assert_called_once_with(
            conn, on_phase=ANY, skip_menu_bootstrap=False, already_locked=False
        )
        self.assertEqual(statuses[0].message, "Order sync complete. Pulling cloud data...")
        conn.close()

    def test_iter_sync_statuses_marks_menu_pull_failure_as_terminal_error(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )
        cloud_summary = {
            "attempted": True,
            "menu_merges": {"error": "HTTP 503"},
            "menu_bootstrap": None,
            "menu_assignments_bootstrap": None,
            "menu_mapping_verifications": None,
            "customer_merges": None,
        }

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ), patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(statuses[-1].type, "error")
        self.assertIn("Menu pull failed", statuses[-1].message)
        self.assertTrue(statuses[-1].stats.get("menu_pull_failed"))
        self.assertEqual(statuses[-1].stats["menu_pull_errors"][0]["stream"], "menu_merges")

    def test_iter_sync_statuses_preserves_global_cloud_failure_code(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )
        cloud_summary = {
            "attempted": True,
            "menu_merges": {"error": "invalid_api_key: Invalid API key"},
            "menu_bootstrap": None,
            "menu_assignments_bootstrap": None,
            "menu_mapping_verifications": None,
            "customer_merges": None,
        }

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ), patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            terminal = list(operations.iter_sync_statuses(conn))[-1]

        self.assertEqual(terminal.type, "error")
        self.assertEqual(terminal.code, "invalid_api_key")
        self.assertEqual(terminal.stats["failure_code"], "invalid_api_key")

    def test_iter_sync_statuses_marks_customer_pull_failure_as_terminal_error(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )
        cloud_summary = {
            "attempted": True,
            "menu_merges": None,
            "menu_bootstrap": None,
            "menu_assignments_bootstrap": None,
            "menu_mapping_verifications": None,
            "customer_merges": {"error": "HTTP 503"},
        }

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ), patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(statuses[-1].type, "error")
        self.assertIn("Customer pull failed", statuses[-1].message)
        self.assertTrue(statuses[-1].stats.get("customer_pull_failed"))
        self.assertEqual(statuses[-1].stats["customer_pull_errors"][0]["stream"], "customer_merges")

    def test_iter_sync_statuses_keeps_done_when_customer_pull_only_has_unresolved_warning(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )
        cloud_summary = {
            "attempted": True,
            "menu_merges": None,
            "menu_bootstrap": None,
            "menu_assignments_bootstrap": None,
            "menu_mapping_verifications": None,
            "customer_merges": {"error": None, "unresolved_pending": 2},
        }

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ), patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(statuses[-1].type, "done")
        self.assertIn("Warning", statuses[-1].message)
        self.assertIn("quarantined", statuses[-1].message)
        self.assertFalse(statuses[-1].stats.get("customer_pull_failed"))
        self.assertEqual(
            statuses[-1].stats["customer_pull_warnings"][0]["stream"], "customer_merges"
        )

    def test_iter_sync_statuses_surfaces_nonfatal_forecast_warning(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )
        cloud_summary = {
            "attempted": True,
            "menu_merges": None,
            "menu_bootstrap": None,
            "menu_assignments_bootstrap": None,
            "menu_mapping_verifications": None,
            "customer_merges": {"error": None, "unresolved_pending": 2},
            "forecasts": {"status": "error", "error": "missing parent run"},
        }

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ), patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            terminal = list(operations.iter_sync_statuses(conn))[-1]

        self.assertEqual(terminal.type, "done")
        self.assertIn("2 customer merge event(s)", terminal.message)
        self.assertIn("Warning: missing parent run", terminal.message)
        self.assertEqual(
            terminal.stats["forecast_pull_warnings"],
            [{"stream": "forecasts", "warning": "missing parent run"}],
        )

    def test_iter_sync_statuses_surfaces_nonfatal_global_history_warning(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )
        cloud_summary = {
            "attempted": True,
            "menu_merges": None,
            "menu_bootstrap": None,
            "menu_assignments_bootstrap": None,
            "menu_mapping_verifications": None,
            "customer_merges": None,
            "global_menu_history": {
                "status": "error",
                "error": "malformed unified history page",
            },
        }

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ), patch(
            "src.api.routers.operations._menu_items_empty",
            return_value=False,
        ):
            terminal = list(operations.iter_sync_statuses(conn))[-1]

        self.assertEqual(terminal.type, "done")
        self.assertIn("Warning: malformed unified history page", terminal.message)
        self.assertFalse(terminal.stats.get("menu_pull_failed"))
        self.assertEqual(
            terminal.stats["global_menu_history_warnings"],
            [
                {
                    "stream": "global_menu_history",
                    "warning": "malformed unified history page",
                }
            ],
        )


class SyncConcurrencyGuardTests(unittest.TestCase):
    """One Sync DB per restaurant: a second press must not race the first."""

    def setUp(self) -> None:
        operations._ACTIVE_SYNC_SLOTS.clear()
        self.addCleanup(operations._ACTIVE_SYNC_SLOTS.clear)

    @staticmethod
    def _profile(restaurant_id: str = "rest-1"):
        from src.core.profiles import RestaurantProfile

        return RestaurantProfile(
            restaurant_id=restaurant_id,
            display_name="Dach & Nona",
            timezone="Asia/Kolkata",
            database_path=f"/tmp/{restaurant_id}.db",
            authorization_state="authorized",
            is_bound=True,
            menu_group_id="group-1",
            menu_capabilities=("global_menu_v1",),
        )

    def _run(self, profile):
        from src.core.analytics_scope import restaurant_scope

        return operations.run_sync(
            operations.SyncRunRequest(restaurant_id=profile.restaurant_id),
            scope=restaurant_scope(profile),
        )

    def test_a_second_run_for_a_busy_restaurant_is_refused(self) -> None:
        from fastapi import HTTPException

        profile = self._profile()
        captured = []

        with patch.object(
            operations.JobManager,
            "start_job",
            side_effect=lambda factory: captured.append(factory) or "job-1",
        ), patch.object(
            operations.JobManager,
            "get_job",
            return_value={"status": "running"},
        ), patch.object(
            operations, "get_profile_connection", return_value=(Mock(), "ok")
        ):
            first = self._run(profile)
            with self.assertRaises(HTTPException) as caught:
                self._run(profile)

        self.assertEqual(first["job_id"], "job-1")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "sync_already_running")
        self.assertEqual(caught.exception.detail["job_id"], "job-1")

    def test_the_claim_is_released_once_the_job_generator_finishes(self) -> None:
        profile = self._profile()
        captured = []

        with patch.object(
            operations.JobManager,
            "start_job",
            side_effect=lambda factory: captured.append(factory) or "job-1",
        ), patch.object(
            operations.JobManager,
            "get_job",
            return_value={"status": "running"},
        ), patch.object(
            operations, "get_profile_connection", return_value=(Mock(), "ok")
        ), patch.object(
            operations,
            "iter_sync_statuses",
            side_effect=lambda _conn: iter([SyncStatus("done", "complete")]),
        ):
            self._run(profile)
            list(captured[0]())
            # The restaurant is free again, so the next press is accepted.
            second = self._run(profile)

        self.assertEqual(second["job_id"], "job-1")
        self.assertEqual(len(captured), 2)

    def test_all_stores_and_a_member_restaurant_cannot_run_at_once(self) -> None:
        from fastapi import HTTPException

        from src.core.analytics_scope import all_stores_scope
        from src.core.profiles import ALL_STORES_TOKEN

        members = (self._profile("rest-A"), self._profile("rest-B"))

        with patch.object(
            operations.JobManager,
            "start_job",
            side_effect=lambda factory: "job-all",
        ), patch.object(
            operations.JobManager,
            "get_job",
            return_value={"status": "running"},
        ), patch.object(
            operations, "get_profile_connection", return_value=(Mock(), "ok")
        ):
            operations.run_sync(
                operations.SyncRunRequest(restaurant_id=ALL_STORES_TOKEN),
                scope=all_stores_scope(members),
            )
            with self.assertRaises(HTTPException) as caught:
                self._run(members[1])

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "sync_already_running")

    def test_a_slot_left_by_a_job_the_manager_never_registered_does_not_block(self) -> None:
        profile = self._profile()

        with patch.object(
            operations.JobManager, "start_job", side_effect=lambda factory: "job-ghost"
        ), patch.object(
            operations.JobManager, "get_job", return_value=None
        ), patch.object(
            operations, "get_profile_connection", return_value=(Mock(), "ok")
        ):
            self._run(profile)
            second = self._run(profile)

        self.assertEqual(second["job_id"], "job-ghost")


if __name__ == "__main__":
    unittest.main()
