"""
Cloud Sync Scheduler
Background service that triggers the 'Push' sync to the cloud every 5 minutes.
Scope:
1. Client Learning (Errors, Logs, Menu Bootstrap) -> /api/errors/ingest, /api/learning/ingest, etc.
2. Conversations -> /api/conversations/sync
"""

import asyncio
import os
from src.core.db.connection import get_db_connection
from src.core.client_learning_shipper import run_all as run_client_learning_shippers
from services.sync_conversations import run_sync_cycle as run_conversation_sync
from src.core.config.cloud_sync_config import get_cloud_sync_config

# Default interval: 5 minutes
SYNC_INTERVAL_SECONDS = 300

async def background_sync_task():
    """
    Infinite loop to run cloud sync every 5 minutes.
    Reads 'cloud_sync_url' from system_config.
    """
    print(f"[Cloud Sync] Scheduler started. Interval: {SYNC_INTERVAL_SECONDS}s")
    
    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

            # Skip sync while forecast training is in progress to avoid CPU contention
            try:
                from src.api.routers.forecast_training_status import is_training
                if is_training():
                    print("[Cloud Sync] Skipping — forecast training in progress")
                    continue
            except ImportError:
                pass  # Module not available, proceed normally

            # Open a fresh connection for this cycle
            conn, _ = get_db_connection()
            if not conn:
                print("[Cloud Sync] Failed to get DB connection. Skipping cycle.")
                continue
                
            try:
                # 1. Fetch Config
                cloud_sync_url, cloud_sync_api_key = get_cloud_sync_config(conn)
                
                if not cloud_sync_url:
                    # If not configured, we might skip or fallback to env vars if we want legacy support.
                    # For now, let's log and skip to avoid noise if user hasn't set it up.
                    # print("[Cloud Sync] No URL configured in system_config. Skipping.")
                    continue
                
                # Ensure no trailing slash for consistency (get_cloud_sync_config already strips)
                base_url = cloud_sync_url
                
                # 2. Run Learning/Error/Menu Sync (Synchronous function)
                # run_all returns a dict, we can log it if needed
                # We assume run_client_learning_shippers has been updated to accept base_url/auth
                try:
                    res_learning = run_client_learning_shippers(
                        conn, 
                        base_url=base_url, 
                        auth=cloud_sync_api_key
                    )
                    # Optional: Log meaningful stats
                    # print(f"[Cloud Sync] Learning: {res_learning}")
                except Exception as e:
                    print(f"[Cloud Sync] Learning Sync Failed: {e}")

                # 3. Run Conversation Sync (Async function)
                try:
                    res_conv = await run_conversation_sync(
                        conn, 
                        base_url=base_url,
                        auth=cloud_sync_api_key
                    )
                    # print(f"[Cloud Sync] Conversations: {res_conv}")
                except Exception as e:
                    print(f"[Cloud Sync] Convers. Sync Failed: {e}")
                    
            finally:
                conn.close()
                
        except asyncio.CancelledError:
            print("[Cloud Sync] Scheduler cancelled.")
            break
        except Exception as e:
            print(f"[Cloud Sync] Unexpected error in loop: {e}")
