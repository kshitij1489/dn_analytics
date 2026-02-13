"""
Cloud Sync Scheduler
Background service that triggers the 'Push' sync to the cloud every 5 minutes.
Scope:
1. Client Learning (Errors, Logs, Menu Bootstrap) -> /api/errors/ingest, /api/learning/ingest, etc.
2. Conversations -> /api/conversations/sync
3. Forecast Automation: Checks if local forecast is fresh at the start of business day; triggers full retrain if missing.
"""

import asyncio
import os
import logging
from src.core.db.connection import get_db_connection
from src.core.client_learning_shipper import run_all as run_client_learning_shippers
from services.sync_conversations import run_sync_cycle as run_conversation_sync
from src.core.config.cloud_sync_config import get_cloud_sync_config

# Forecast Automation Imports
from src.core.utils.business_date import get_current_business_date
from src.core.learning.revenue_forecasting.forecast_cache import is_revenue_cache_fresh
from src.api.routers import forecast_training_status

# Default interval: 5 minutes
SYNC_INTERVAL_SECONDS = 300
STARTUP_DELAY_SECONDS = 30  # Wait briefly after startup before heavy lifting

logger = logging.getLogger(__name__)

def check_and_trigger_auto_forecast(conn):
    """
    Check if the forecast for the current business date exists.
    If missing and no training is active, trigger a full retrain (Revenue -> Items -> Volume).
    """
    try:
        # 1. Is training already active?
        if forecast_training_status.is_training():
            return
        
        # 2. Is forecast fresh for today?
        today_str = get_current_business_date()
        if is_revenue_cache_fresh(conn, today_str):
            return  # Already have forecast for today
        
        # 3. Forecast missing & idle -> Trigger Retrain
        logger.info(f"[Auto-Forecast] Forecast missing for {today_str}. Triggering automatic full retrain...")
        # Avoid circular import by importing inside function
        from src.api.routers.forecast import _full_retrain_task
        
        # Determine scope: "all" covers revenue, items, and volume
        _full_retrain_task(scope="all")
        
    except Exception as e:
        logger.error(f"[Auto-Forecast] Check/Trigger failed: {e}")

async def background_sync_task():
    """
    Infinite loop to run cloud sync every 5 minutes.
    Also checks for missing daily forecasts.
    """
    print(f"[Cloud Sync] Scheduler started. Interval: {SYNC_INTERVAL_SECONDS}s")
    
    # Initial startup delay to let the app initialize
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    
    # Run a check immediately on startup (after delay)
    try:
        conn, _ = get_db_connection()
        if conn:
            # Run forecast check in a separate thread to avoid blocking asyncio loop
            await asyncio.to_thread(check_and_trigger_auto_forecast, conn)
            conn.close()
    except Exception as e:
        print(f"[Cloud Sync] Startup forecast check failed: {e}")
    
    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

            # Skip sync while forecast training is in progress to avoid CPU contention
            if forecast_training_status.is_training():
                print("[Cloud Sync] Skipping — forecast training in progress")
                continue

            # Open a fresh connection for this cycle
            conn, _ = get_db_connection()
            if not conn:
                print("[Cloud Sync] Failed to get DB connection. Skipping cycle.")
                continue
                
            try:
                # --- 1. Auto-Forecast Check ---
                # Check if we crossed into a new business day or still lack forecast
                await asyncio.to_thread(check_and_trigger_auto_forecast, conn)
                
                # If training started, skip the sync part this cycle
                if forecast_training_status.is_training():
                    print("[Cloud Sync] Auto-forecast started. Skipping sync this cycle.")
                    continue

                # --- 2. Cloud Sync ---
                # Fetch Config
                cloud_sync_url, cloud_sync_api_key = get_cloud_sync_config(conn)
                
                if not cloud_sync_url:
                    # If not configured, we might skip or fallback to env vars if we want legacy support.
                    # For now, let's log and skip to avoid noise if user hasn't set it up.
                    # print("[Cloud Sync] No URL configured in system_config. Skipping.")
                    continue
                
                # Ensure no trailing slash for consistency (get_cloud_sync_config already strips)
                base_url = cloud_sync_url
                
                # Run Learning/Error/Menu Sync (Synchronous function)
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

                # Run Conversation Sync (Async function)
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
