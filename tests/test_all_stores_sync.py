"""Phase 2 All Stores Sync DB coordinator tests (plan §8.2, §8.6)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.api.routers import operations
from src.core.analytics_scope import all_stores_scope
from src.core.central_api import CentralAPIError, scoped_headers
from src.core.profiles import (
    ALL_STORES_TOKEN,
    bind_and_select_profile,
    upsert_allowed_restaurants,
)
from src.core.services.all_stores_sync import iter_all_stores_sync
from src.core.services.sync_service import SyncStatus
from tests.all_stores_test_helpers import TwoStoreFixture, allowed


def _bound_restaurant_id(conn) -> str:
    return str(
        conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()[0]
    )


def _main_database_path(conn) -> str:
    return next(
        str(row[2]) for row in conn.execute("PRAGMA database_list").fetchall() if row[1] == "main"
    )


class AllStoresSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TwoStoreFixture().start()

    def tearDown(self) -> None:
        self.fixture.stop()

    def test_every_store_is_synced_once_with_its_own_headers_and_database(self) -> None:
        seen = []

        def fake_single_store(conn, *, already_locked=False):
            headers = scoped_headers(conn, auth_kind="analytics", credential="api-key")
            seen.append(
                {
                    "restaurant_header": headers["X-Restaurant-ID"],
                    "database_path": _main_database_path(conn),
                    "already_locked": already_locked,
                }
            )
            yield SyncStatus("done", "Sync Complete", stats={"orders": 1})

        statuses = list(
            iter_all_stores_sync(self.fixture.profiles, iter_single_store=fake_single_store)
        )

        self.assertEqual([entry["restaurant_header"] for entry in seen], ["rest-A", "rest-B"])
        self.assertEqual(
            [entry["database_path"] for entry in seen],
            [Path(profile.database_path).as_posix() for profile in self.fixture.profiles],
        )
        # The coordinator owns the process-wide lock for the whole loop.
        self.assertTrue(all(entry["already_locked"] for entry in seen))

        terminal = statuses[-1]
        self.assertEqual(terminal.type, "done")
        self.assertEqual(terminal.stats["outcome"], "completed")
        self.assertEqual(terminal.stats["stores_completed"], 2)
        self.assertEqual(terminal.stats["orders"], 2)
        self.assertIn("Store 1 of 2 — Dach & Nona", statuses[0].message)

    def test_allowed_list_change_during_the_job_does_not_change_the_iteration_set(self) -> None:
        captured = tuple(all_stores_scope().profiles)
        attempted = []

        def fake_single_store(conn, *, already_locked=False):
            attempted.append(_bound_restaurant_id(conn))
            # A refresh lands mid-loop: rest-B loses its grant and a new store
            # appears. Neither may alter this run.
            upsert_allowed_restaurants(
                allowed(
                    ("rest-A", "Dach & Nona", "Asia/Kolkata"),
                    ("rest-C", "Dach & Nona Three", "Asia/Kolkata"),
                )
            )
            # Fails closed for a revoked store, exactly as a scoped request must.
            scoped_headers(conn, auth_kind="analytics", credential="k")
            yield SyncStatus("done", "Sync Complete", stats={"orders": 0})

        statuses = list(iter_all_stores_sync(captured, iter_single_store=fake_single_store))
        self.assertEqual(attempted, ["rest-A", "rest-B"])
        stores = {entry["restaurant_id"]: entry for entry in statuses[-1].stats["stores"]}
        # The newly granted store is not substituted into a job already running.
        self.assertNotIn("rest-C", stores)
        self.assertEqual(stores["rest-A"]["status"], "completed")
        self.assertEqual(stores["rest-B"]["status"], "failed")
        self.assertIn("not authorized", stores["rest-B"]["error"])

    def test_store_local_failure_continues_and_reports_per_store_detail(self) -> None:
        def fake_single_store(conn, *, already_locked=False):
            restaurant_id = scoped_headers(conn, auth_kind="analytics", credential="k")["X-Restaurant-ID"]
            if restaurant_id == "rest-A":
                raise CentralAPIError("Restaurant is not allowed", code="restaurant_forbidden")
            yield SyncStatus("done", "Sync Complete", stats={"orders": 3})

        statuses = list(
            iter_all_stores_sync(self.fixture.profiles, iter_single_store=fake_single_store)
        )
        terminal = statuses[-1]
        self.assertEqual(terminal.type, "done")
        self.assertEqual(terminal.stats["outcome"], "partial")
        stores = {entry["restaurant_id"]: entry for entry in terminal.stats["stores"]}
        self.assertEqual(stores["rest-A"]["status"], "failed")
        self.assertEqual(stores["rest-A"]["code"], "restaurant_forbidden")
        # One forbidden restaurant must not be retried as another store.
        self.assertEqual(stores["rest-B"]["status"], "completed")
        self.assertEqual(stores["rest-B"]["stats"]["orders"], 3)

    def test_global_credential_failure_stops_the_remaining_stores(self) -> None:
        attempted = []

        def fake_single_store(conn, *, already_locked=False):
            attempted.append(scoped_headers(conn, auth_kind="analytics", credential="k")["X-Restaurant-ID"])
            raise CentralAPIError("Invalid API key", code="invalid_api_key")
            yield  # pragma: no cover - generator marker

        statuses = list(
            iter_all_stores_sync(self.fixture.profiles, iter_single_store=fake_single_store)
        )
        terminal = statuses[-1]
        self.assertEqual(attempted, ["rest-A"])
        self.assertEqual(terminal.type, "error")
        self.assertEqual(terminal.stats["outcome"], "failed")
        stores = {entry["restaurant_id"]: entry for entry in terminal.stats["stores"]}
        self.assertEqual(stores["rest-B"]["status"], "not_attempted")
        self.assertEqual(terminal.stats["aborted"]["code"], "invalid_api_key")

    def test_yielded_global_failure_keeps_code_and_stops_remaining_stores(self) -> None:
        """The ordinary sync reports failures as statuses, not exceptions."""
        attempted = []

        def fake_single_store(conn, *, already_locked=False):
            attempted.append(_bound_restaurant_id(conn))
            yield SyncStatus(
                "error",
                "Invalid API key",
                stats={"failure_code": "invalid_api_key", "phase": "orders"},
                code="invalid_api_key",
            )

        statuses = list(
            iter_all_stores_sync(self.fixture.profiles, iter_single_store=fake_single_store)
        )
        terminal = statuses[-1]
        stores = {entry["restaurant_id"]: entry for entry in terminal.stats["stores"]}

        self.assertEqual(attempted, ["rest-A"])
        self.assertEqual(terminal.type, "error")
        self.assertEqual(terminal.stats["outcome"], "failed")
        self.assertEqual(terminal.stats["aborted"]["code"], "invalid_api_key")
        self.assertEqual(stores["rest-A"]["code"], "invalid_api_key")
        self.assertEqual(stores["rest-A"]["stats"]["phase"], "orders")
        self.assertEqual(stores["rest-B"]["status"], "not_attempted")

    def test_cursors_advance_independently_per_profile(self) -> None:
        def fake_single_store(conn, *, already_locked=False):
            restaurant_id = scoped_headers(conn, auth_kind="analytics", credential="k")["X-Restaurant-ID"]
            if restaurant_id == "rest-B":
                raise RuntimeError("stream unavailable")
            conn.execute(
                """
                INSERT INTO system_config (key, value) VALUES ('menu_merge_pull_cursor', 'cursor-A')
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """
            )
            conn.commit()
            yield SyncStatus("done", "Sync Complete", stats={"orders": 1})

        list(iter_all_stores_sync(self.fixture.profiles, iter_single_store=fake_single_store))

        first = self.fixture.connection("rest-A")
        second = self.fixture.connection("rest-B")
        try:
            self.assertEqual(
                first.execute(
                    "SELECT value FROM system_config WHERE key='menu_merge_pull_cursor'"
                ).fetchone()[0],
                "cursor-A",
            )
            self.assertIsNone(
                second.execute(
                    "SELECT value FROM system_config WHERE key='menu_merge_pull_cursor'"
                ).fetchone()
            )
        finally:
            first.close()
            second.close()

        # A restart syncs only the failed store's stream again; the succeeded
        # store keeps its own cursor.
        def retry_single_store(conn, *, already_locked=False):
            restaurant_id = scoped_headers(conn, auth_kind="analytics", credential="k")["X-Restaurant-ID"]
            conn.execute(
                """
                INSERT INTO system_config (key, value) VALUES ('menu_merge_pull_cursor', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (f"cursor-{restaurant_id}",),
            )
            conn.commit()
            yield SyncStatus("done", "Sync Complete", stats={"orders": 1})

        list(iter_all_stores_sync(self.fixture.profiles, iter_single_store=retry_single_store))
        first = self.fixture.connection("rest-A")
        second = self.fixture.connection("rest-B")
        try:
            self.assertEqual(
                first.execute("SELECT value FROM system_config WHERE key='menu_merge_pull_cursor'").fetchone()[0],
                "cursor-rest-A",
            )
            self.assertEqual(
                second.execute("SELECT value FROM system_config WHERE key='menu_merge_pull_cursor'").fetchone()[0],
                "cursor-rest-B",
            )
        finally:
            first.close()
            second.close()

    def test_run_sync_freezes_the_store_list_before_the_job_thread_starts(self) -> None:
        captured_jobs = []

        def capture_job(factory):
            captured_jobs.append(factory)
            return "job-all"

        synced = []

        def fake_single_store(conn, *, already_locked=False):
            synced.append(_bound_restaurant_id(conn))
            yield SyncStatus("done", "Sync Complete", stats={})

        with patch.object(operations.JobManager, "start_job", side_effect=capture_job):
            response = operations.run_sync(
                operations.SyncRunRequest(restaurant_id=ALL_STORES_TOKEN),
                scope=all_stores_scope(),
            )
        self.assertEqual(response["job_id"], "job-all")

        # The user switches to one physical store while the job is queued.
        bind_and_select_profile("rest-A")
        upsert_allowed_restaurants(allowed(("rest-A", "Dach & Nona", "Asia/Kolkata")))

        with patch(
            "src.core.services.all_stores_sync.iter_all_stores_sync",
            side_effect=lambda profiles, **kwargs: iter_all_stores_sync(
                profiles, iter_single_store=fake_single_store
            ),
        ):
            list(captured_jobs[0]())

        self.assertEqual(synced, ["rest-A", "rest-B"])

    def test_run_sync_rejects_a_mismatched_scope_token(self) -> None:
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as caught:
            operations.run_sync(
                operations.SyncRunRequest(restaurant_id="rest-A"),
                scope=all_stores_scope(),
            )
        self.assertEqual(caught.exception.detail["code"], "profile_scope_mismatch")


class SchedulerFanOutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TwoStoreFixture().start()

    def tearDown(self) -> None:
        self.fixture.stop()

    def test_all_mode_cycle_visits_every_store_and_uploads_global_files_once(self) -> None:
        import asyncio

        from src.core.profiles import select_all_stores
        from src.core.services import cloud_sync_scheduler

        select_all_stores()
        calls = []

        def fake_shipper(conn, *, base_url=None, auth=None, include_global_uploads=True):
            calls.append(
                {
                    "restaurant_id": conn.execute(
                        "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
                    ).fetchone()[0],
                    "include_global_uploads": include_global_uploads,
                }
            )
            return {}

        async def fake_conversation_sync(conn, **kwargs):
            return {}

        with patch.object(cloud_sync_scheduler, "run_client_learning_shippers", fake_shipper), patch.object(
            cloud_sync_scheduler, "run_conversation_sync", fake_conversation_sync
        ), patch.object(
            cloud_sync_scheduler, "get_cloud_sync_config", lambda conn: ("https://cloud.example", "token")
        ), patch(
            "src.core.services.cloud_pull_orchestrator.run_best_effort_cloud_pulls",
            lambda conn, **kwargs: {"attempted": False},
        ):
            profiles = cloud_sync_scheduler.scheduler_profiles()
            self.assertTrue(asyncio.run(cloud_sync_scheduler.run_cloud_cycle(profiles)))

        self.assertEqual([call["restaurant_id"] for call in calls], ["rest-A", "rest-B"])
        self.assertEqual([call["include_global_uploads"] for call in calls], [True, False])

    def test_scheduler_skips_the_whole_cycle_when_sync_lock_is_held(self) -> None:
        import asyncio

        from src.core.services import cloud_sync_scheduler
        from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK

        CLOUD_PULL_LOCK.acquire()
        try:
            with patch.object(cloud_sync_scheduler, "run_cloud_cycle_for_profile") as run_profile:
                ran = asyncio.run(
                    cloud_sync_scheduler.run_cloud_cycle(tuple(self.fixture.profiles))
                )
        finally:
            CLOUD_PULL_LOCK.release()

        self.assertFalse(ran)
        run_profile.assert_not_called()

    def test_scheduler_refuses_to_guess_when_nothing_is_selected(self) -> None:
        from src.core.db.control import get_control_connection
        from src.core.profiles import ProfileError
        from src.core.services import cloud_sync_scheduler

        conn = get_control_connection()
        try:
            conn.execute("DELETE FROM app_selection")
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(ProfileError):
            cloud_sync_scheduler.scheduler_profiles()


if __name__ == "__main__":
    unittest.main()
