"""
Phase C5: shared pull lock (scheduler vs Sync DB job).
"""

import sqlite3
import time
import unittest
from unittest.mock import Mock, patch

from src.core.customer_mutation_commit import pull_latest_customer_state
from src.core.menu_mutation_commit import pull_latest_menu_state
from src.core.services.cloud_pull_orchestrator import (
    CLOUD_PULL_LOCK,
    run_best_effort_cloud_pulls,
)
from tests.profile_test_helpers import bind_test_profile


def _conn_with_cloud_config(url=None, *, cross_thread=False):
    # Profile connections are opened with check_same_thread=False in
    # src/core/db/connection.py; the lock tests that hand a connection to a
    # worker thread need the same affordance.
    conn = sqlite3.connect(":memory:", check_same_thread=not cross_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT)")
    bind_test_profile(conn)
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
    def test_global_background_pull_uses_trusted_profile_sync_context(self) -> None:
        conn = _conn_with_cloud_config(url="https://cloud.example")
        self.addCleanup(conn.close)
        global_capability = Mock(active=True)
        with patch(
            "src.core.config.cloud_sync_config.get_cloud_sync_config",
            return_value=("https://cloud.example", "k"),
        ), patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=global_capability,
        ) as capability_resolver, patch(
            "src.core.global_menu_sync.pull_global_menu_state",
            return_value={"status": "applied"},
        ) as global_pull, patch(
            "src.core.global_menu_sync.pull_global_assignment_snapshot",
            return_value={"status": "applied"},
        ) as assignment_pull, patch(
            "src.core.global_menu_history.pull_global_menu_history",
            return_value={"status": "applied"},
        ) as history_pull, patch(
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
            result = run_best_effort_cloud_pulls(conn, blocking=False)

        self.assertTrue(result["attempted"])
        capability_resolver.assert_called_once_with(conn, allow_profile_sync=True)
        global_pull.assert_called_once_with(
            conn, auth="k", allow_profile_sync=True
        )
        assignment_pull.assert_called_once_with(
            conn, auth="k", allow_profile_sync=True
        )
        history_pull.assert_called_once_with(
            conn, auth="k", allow_profile_sync=True
        )

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

    def test_blocking_pull_runs_when_the_lock_is_free(self) -> None:
        import threading

        conn = _conn_with_cloud_config(cross_thread=True)
        self.addCleanup(conn.close)
        outcome = {}

        def run() -> None:
            with patch(
                "src.core.services.cloud_pull_orchestrator._run_best_effort_cloud_pulls_locked",
                return_value={"attempted": False},
            ):
                outcome["summary"] = run_best_effort_cloud_pulls(conn, blocking=True)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "blocking pull deadlocked on the free lock")
        self.assertEqual(outcome["summary"]["attempted"], False)
        self.assertTrue(CLOUD_PULL_LOCK.acquire(blocking=False))
        CLOUD_PULL_LOCK.release()

    def test_blocking_pull_names_the_wait_before_queueing_behind_the_holder(self) -> None:
        import threading

        conn = _conn_with_cloud_config(cross_thread=True)
        self.addCleanup(conn.close)
        phases = []
        outcome = {}

        def run() -> None:
            with patch(
                "src.core.services.cloud_pull_orchestrator._run_best_effort_cloud_pulls_locked",
                return_value={"attempted": False},
            ):
                outcome["summary"] = run_best_effort_cloud_pulls(
                    conn, blocking=True, on_phase=phases.append
                )

        self.assertTrue(CLOUD_PULL_LOCK.acquire(blocking=False))
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            # The waiting job reports itself before it blocks.
            for _ in range(100):
                if phases:
                    break
                time.sleep(0.01)
            self.assertEqual(
                phases, ["Waiting for the background cloud sync to finish..."]
            )
            self.assertNotIn("summary", outcome)
        finally:
            CLOUD_PULL_LOCK.release()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome["summary"]["attempted"], False)

    def test_pull_releases_lock_after_run(self) -> None:
        conn = _conn_with_cloud_config()
        self.addCleanup(conn.close)

        with patch(
            "src.core.services.cloud_pull_orchestrator._run_best_effort_cloud_pulls_locked",
            return_value={"attempted": False},
        ) as locked_body:
            summary = run_best_effort_cloud_pulls(conn, blocking=False)
        locked_body.assert_called_once()
        self.assertEqual(
            summary,
            {"attempted": False, "restaurant_id": "test-restaurant"},
        )
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


if __name__ == "__main__":
    unittest.main()
