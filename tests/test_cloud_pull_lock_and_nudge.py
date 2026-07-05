"""
Phase C5: shared pull lock (scheduler vs Sync DB job) and the fire-and-forget
menu-merge push nudge after local commits.
"""

import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from src.core.menu_merge_push_nudge import nudge_menu_merge_push_async
from src.core.services.cloud_pull_orchestrator import (
    CLOUD_PULL_LOCK,
    run_best_effort_cloud_pulls,
)


def _conn_with_cloud_config(url=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT)")
    if url:
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', ?)", (url,)
        )
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_api_key', 'k')"
        )
    conn.commit()
    return conn


class CloudPullLockTests(unittest.TestCase):
    def test_nonblocking_pull_skips_while_lock_is_held(self) -> None:
        conn = _conn_with_cloud_config()
        self.addCleanup(conn.close)

        self.assertTrue(CLOUD_PULL_LOCK.acquire(blocking=False))
        try:
            summary = run_best_effort_cloud_pulls(conn, blocking=False)
        finally:
            CLOUD_PULL_LOCK.release()

        self.assertTrue(summary["skipped"])
        self.assertFalse(summary["attempted"])
        self.assertIn("in progress", summary["reason"])

    def test_pull_releases_lock_after_run(self) -> None:
        conn = _conn_with_cloud_config()
        self.addCleanup(conn.close)

        with patch(
            "src.core.services.cloud_pull_orchestrator._run_best_effort_cloud_pulls_locked",
            return_value={"attempted": False},
        ) as locked_body:
            summary = run_best_effort_cloud_pulls(conn, blocking=False)
        locked_body.assert_called_once()
        self.assertEqual(summary, {"attempted": False})
        # The lock must be free again after the run.
        self.assertTrue(CLOUD_PULL_LOCK.acquire(blocking=False))
        CLOUD_PULL_LOCK.release()


class MenuMergePushNudgeTests(unittest.TestCase):
    def test_no_thread_without_cloud_config(self) -> None:
        conn = _conn_with_cloud_config(url=None)
        self.addCleanup(conn.close)
        with patch("threading.Thread") as thread_cls:
            started = nudge_menu_merge_push_async(conn)
        self.assertFalse(started)
        thread_cls.assert_not_called()

    def test_thread_started_with_cloud_config(self) -> None:
        conn = _conn_with_cloud_config(url="https://cloud.example")
        self.addCleanup(conn.close)
        thread = MagicMock()
        with patch("threading.Thread", return_value=thread) as thread_cls:
            started = nudge_menu_merge_push_async(conn)
        self.assertTrue(started)
        thread.start.assert_called_once()
        kwargs = thread_cls.call_args.kwargs
        self.assertTrue(kwargs["daemon"])
        self.assertEqual(
            kwargs["args"][0],
            "https://cloud.example/desktop-analytics-sync/menu-merges/ingest",
        )

    def test_local_merge_triggers_nudge(self) -> None:
        from tests.test_menu_assignment_apply import make_install_db
        from utils import menu_utils

        conn = make_install_db()
        self.addCleanup(conn.close)
        with patch("utils.menu_utils.export_to_backups", return_value=True), patch(
            "utils.menu_utils._clear_impacted_models", return_value=None
        ), patch(
            "src.core.menu_merge_push_nudge.nudge_menu_merge_push_async"
        ) as nudge:
            result = menu_utils.merge_menu_items(conn, "item_a", "item_b")
            self.assertEqual(result["status"], "success")
            nudge.assert_called_once()

            # Remote applies (emit_sync_event=False) must not nudge.
            nudge.reset_mock()
            result = menu_utils.undo_merge(conn, int(result["merge_id"]), emit_sync_event=False)
            self.assertEqual(result["status"], "success")
            nudge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
