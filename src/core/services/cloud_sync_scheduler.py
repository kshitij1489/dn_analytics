"""
Cloud Sync Scheduler
Background service that triggers cloud push/pull every 5 minutes.

In All Stores mode the cycle fans out over the same authorized profile list the
selector shows, sequentially and with one explicit connection per store. It
never starts the full raw-order Sync DB loop — that stays an explicit button
action (plan §8.2.10).
"""

import asyncio
import logging

from src.core.analytics_scope import selected_scope
from src.core.db.connection import get_profile_connection
from src.core.profiles import ProfileError
from src.core.client_learning_shipper import run_all as run_client_learning_shippers
from services.sync_conversations import run_sync_cycle as run_conversation_sync
from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK

SYNC_INTERVAL_SECONDS = 300
STARTUP_DELAY_SECONDS = 30

logger = logging.getLogger(__name__)


def scheduler_profiles():
    """The profiles this cycle owns: one selected store, or the All snapshot.

    Never falls back to the first profile or to "all" when nothing is selected.
    """
    scope = selected_scope()
    profiles = [
        profile
        for profile in scope.profiles
        if profile.is_bound and profile.authorization_state == "authorized"
    ]
    if not profiles:
        raise ProfileError("No authorized, initialized restaurant profile is selected")
    return profiles


async def run_cloud_cycle_for_profile(
    profile,
    *,
    include_global_uploads: bool,
    already_locked: bool = False,
) -> bool:
    """Run one store's cloud-only cycle. Returns whether it did any work.

    A store with no cloud sync URL does nothing, so it must not be the store
    that consumes the cycle's single global upload slot.
    """
    conn, _ = get_profile_connection(profile)
    try:
        cloud_sync_url, cloud_sync_api_key = get_cloud_sync_config(conn)
        if not cloud_sync_url:
            return False

        try:
            # App-global error files are uploaded once per cycle; scoped menu
            # bootstrap seeds and per-profile telemetry run for every store.
            run_client_learning_shippers(
                conn,
                base_url=cloud_sync_url,
                auth=cloud_sync_api_key,
                include_global_uploads=include_global_uploads,
            )
        except Exception as e:
            print(f"[Cloud Sync] Learning Sync Failed ({profile.restaurant_id}): {e}")

        try:
            await run_conversation_sync(
                conn,
                base_url=cloud_sync_url,
                auth=cloud_sync_api_key,
            )
        except Exception as e:
            print(f"[Cloud Sync] Convers. Sync Failed ({profile.restaurant_id}): {e}")

        try:
            from src.core.services.cloud_pull_orchestrator import (
                run_best_effort_cloud_pulls,
            )

            res_pull = await asyncio.to_thread(
                run_best_effort_cloud_pulls,
                conn,
                blocking=False,
                already_locked=already_locked,
            )
            if res_pull.get("skipped"):
                print(
                    f"[Cloud Sync] Pull skipped for {profile.restaurant_id}: "
                    f"{res_pull.get('reason')}"
                )
        except Exception as e:
            print(f"[Cloud Sync] Cloud pull failed ({profile.restaurant_id}): {e}")
        return True
    finally:
        conn.close()


async def run_cloud_cycle(profiles) -> bool:
    """Serialize the entire scheduler cycle with Sync DB and mutation commits."""
    if not CLOUD_PULL_LOCK.acquire(blocking=False):
        print("[Cloud Sync] Cycle skipped: another sync or mutation is in progress")
        return False
    try:
        global_uploads_done = False
        for profile in tuple(profiles):
            try:
                ran = await run_cloud_cycle_for_profile(
                    profile,
                    include_global_uploads=not global_uploads_done,
                    already_locked=True,
                )
                global_uploads_done = global_uploads_done or ran
            except Exception as e:
                print(f"[Cloud Sync] Cycle failed for {profile.restaurant_id}: {e}")
        return True
    finally:
        CLOUD_PULL_LOCK.release()


async def background_sync_task():
    print(f"[Cloud Sync] Scheduler started. Interval: {SYNC_INTERVAL_SECONDS}s")
    await asyncio.sleep(STARTUP_DELAY_SECONDS)

    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

            try:
                profiles = scheduler_profiles()
            except ProfileError as exc:
                print(f"[Cloud Sync] No usable selected profile: {exc}. Skipping cycle.")
                continue

            await run_cloud_cycle(profiles)

        except asyncio.CancelledError:
            print("[Cloud Sync] Scheduler cancelled.")
            break
        except Exception as e:
            print(f"[Cloud Sync] Unexpected error in loop: {e}")
