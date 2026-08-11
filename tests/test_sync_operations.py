import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.api.routers import operations
from src.core.services.sync_service import SyncStatus


class SyncOperationsTests(unittest.TestCase):
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
        cloud_pull.assert_called_once_with(conn, skip_menu_bootstrap=False, already_locked=False)

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
        cloud_pull.assert_called_once_with(conn, skip_menu_bootstrap=True, already_locked=False)
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
        cloud_pull.assert_called_once_with(conn, skip_menu_bootstrap=False, already_locked=False)
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


if __name__ == "__main__":
    unittest.main()
