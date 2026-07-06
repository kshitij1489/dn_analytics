"""
Phase C5: shared pull lock (scheduler vs Sync DB job) and the fire-and-forget
menu-merge push nudge after local commits.
"""

import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from src.core.customer_mutation_commit import pull_latest_customer_state
from src.core.menu_merge_push_nudge import nudge_menu_merge_push_async
from src.core.menu_mutation_commit import pull_latest_menu_state
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

    def test_pull_latest_menu_state_holds_lock_during_pull(self) -> None:
        conn = _conn_with_cloud_config()
        self.addCleanup(conn.close)

        def _assert_locked(_conn):
            self.assertTrue(CLOUD_PULL_LOCK.locked())
            return {"menu_revision": 1}

        with patch(
            "src.core.menu_mutation_commit._pull_latest_menu_state_locked",
            side_effect=_assert_locked,
        ) as locked_pull:
            pull_latest_menu_state(conn)

        locked_pull.assert_called_once_with(conn)
        self.assertTrue(CLOUD_PULL_LOCK.acquire(blocking=False))
        CLOUD_PULL_LOCK.release()

    def test_pull_latest_menu_state_already_locked_avoids_reentrancy_deadlock(
        self,
    ) -> None:
        conn = _conn_with_cloud_config()
        self.addCleanup(conn.close)

        self.assertTrue(CLOUD_PULL_LOCK.acquire(blocking=False))
        try:
            with patch(
                "src.core.menu_mutation_commit._pull_latest_menu_state_locked",
                return_value={"menu_revision": 1},
            ) as locked_pull:
                result = pull_latest_menu_state(conn, already_locked=True)
        finally:
            CLOUD_PULL_LOCK.release()

        locked_pull.assert_called_once_with(conn)
        self.assertEqual(result["menu_revision"], 1)

    def test_pull_latest_customer_state_holds_lock_during_pull(self) -> None:
        conn = _conn_with_cloud_config()
        self.addCleanup(conn.close)

        def _assert_locked(_conn):
            self.assertTrue(CLOUD_PULL_LOCK.locked())
            return {"events_applied": 0}

        with patch(
            "src.core.customer_mutation_commit._pull_latest_customer_state_locked",
            side_effect=_assert_locked,
        ) as locked_pull:
            pull_latest_customer_state(conn)

        locked_pull.assert_called_once_with(conn)
        self.assertTrue(CLOUD_PULL_LOCK.acquire(blocking=False))
        CLOUD_PULL_LOCK.release()

    def test_pull_latest_customer_state_already_locked_avoids_reentrancy_deadlock(
        self,
    ) -> None:
        conn = _conn_with_cloud_config()
        self.addCleanup(conn.close)

        self.assertTrue(CLOUD_PULL_LOCK.acquire(blocking=False))
        try:
            with patch(
                "src.core.customer_mutation_commit._pull_latest_customer_state_locked",
                return_value={"events_applied": 0},
            ) as locked_pull:
                result = pull_latest_customer_state(conn, already_locked=True)
        finally:
            CLOUD_PULL_LOCK.release()

        locked_pull.assert_called_once_with(conn)
        self.assertEqual(result["events_applied"], 0)

    def test_orchestrator_passes_already_locked_to_menu_state_pull(self) -> None:
        conn = _conn_with_cloud_config(url="https://cloud.example")
        self.addCleanup(conn.close)

        with patch(
            "src.core.config.cloud_sync_config.get_cloud_sync_config",
            return_value=("https://cloud.example", "k"),
        ), patch(
            "src.core.menu_bootstrap_sync.get_menu_bootstrap_pull_endpoint",
            return_value=None,
        ), patch(
            "src.core.menu_assignment_bootstrap.get_menu_assignments_snapshot_endpoint",
            return_value=None,
        ), patch(
            "src.core.menu_mapping_verification_sync.get_menu_mapping_verification_pull_endpoint",
            return_value="https://cloud.example/verifications",
        ), patch(
            "src.core.menu_merge_sync.get_menu_merge_pull_endpoint",
            return_value="https://cloud.example/merges",
        ), patch(
            "src.core.customer_merge_sync.get_customer_merge_pull_endpoint",
            return_value=None,
        ), patch(
            "src.core.menu_mutation_commit.pull_latest_menu_state",
            return_value={
                "menu_mapping_verifications": {},
                "menu_merges": {},
            },
        ) as pull_menu_state:
            run_best_effort_cloud_pulls(conn, blocking=False)

        pull_menu_state.assert_called_once_with(conn, already_locked=True)

    def test_orchestrator_passes_already_locked_to_customer_state_pull(self) -> None:
        conn = _conn_with_cloud_config(url="https://cloud.example")
        self.addCleanup(conn.close)

        with patch(
            "src.core.config.cloud_sync_config.get_cloud_sync_config",
            return_value=("https://cloud.example", "k"),
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
            return_value="https://cloud.example/customer-merges",
        ), patch(
            "src.core.customer_mutation_commit.pull_latest_customer_state",
            return_value={"events_applied": 0, "has_more": False},
        ) as pull_customer_state:
            run_best_effort_cloud_pulls(conn, blocking=False)

        pull_customer_state.assert_called_once_with(conn, already_locked=True)


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
