import unittest
from unittest.mock import Mock, patch

from src.api.routers import operations
from src.core.services.sync_service import SyncStatus


class SyncOperationsTests(unittest.TestCase):
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
        ) as cloud_pull:
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual([status.type for status in statuses], ["info", "info", "done"])
        self.assertEqual(statuses[-1].message, "No new orders to sync · Cloud pull (best-effort) finished")
        self.assertEqual(statuses[-1].stats["cloud_pull"], cloud_summary)
        cloud_pull.assert_called_once_with(conn)

    def test_iter_sync_statuses_does_not_run_cloud_pull_after_error(self) -> None:
        conn = Mock()
        sync_statuses = iter([SyncStatus("error", "Sync failed", progress=0.4)])

        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls"
        ) as cloud_pull:
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].type, "error")
        cloud_pull.assert_not_called()


if __name__ == "__main__":
    unittest.main()
