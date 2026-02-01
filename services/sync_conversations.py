"""
Sync Conversations Service

Background service that periodically syncs conversations to a master server.
"""

import os
import json
import asyncio
from datetime import datetime
from typing import Optional
import httpx


# Configuration from environment
MASTER_SERVER_URL = os.environ.get("MASTER_SERVER_URL", "")
MASTER_SERVER_API_KEY = os.environ.get("MASTER_SERVER_API_KEY", "")
SYNC_INTERVAL_SECONDS = int(os.environ.get("SYNC_INTERVAL_SECONDS", "60"))


async def get_unsynced_conversations(conn) -> list:
    """Get conversations that need syncing (never synced or updated since last sync)."""
    cursor = conn.execute("""
        SELECT 
            c.conversation_id,
            c.title,
            c.started_at,
            c.updated_at,
            c.synced_at
        FROM ai_conversations c
        WHERE c.synced_at IS NULL 
           OR c.synced_at < c.updated_at
        ORDER BY c.updated_at ASC
        LIMIT 50
    """)
    return [dict(row) for row in cursor.fetchall()]


async def get_messages_for_conversation(conn, conversation_id: str) -> list:
    """Get all messages for a conversation."""
    cursor = conn.execute("""
        SELECT 
            message_id,
            role,
            content,
            type,
            sql_query,
            explanation,
            log_id,
            query_status,
            created_at
        FROM ai_messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC
    """, (conversation_id,))
    
    messages = []
    for row in cursor.fetchall():
        msg = dict(row)
        # Parse content if JSON
        try:
            msg["content"] = json.loads(msg["content"])
        except (json.JSONDecodeError, TypeError):
            pass
        messages.append(msg)
    return messages


async def sync_to_master(conn, conversations: list) -> dict:
    """
    Sync conversations to master server.
    Returns {"success": bool, "synced_ids": [...], "error": str or None}
    """
    if not MASTER_SERVER_URL:
        return {"success": False, "synced_ids": [], "error": "No master server URL configured"}
    
    # Build payload with full conversation data
    payload = []
    for conv in conversations:
        messages = await get_messages_for_conversation(conn, conv["conversation_id"])
        payload.append({
            **conv,
            "messages": messages
        })
    
    headers = {"Content-Type": "application/json"}
    if MASTER_SERVER_API_KEY:
        headers["Authorization"] = f"Bearer {MASTER_SERVER_API_KEY}"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{MASTER_SERVER_URL}/api/conversations/sync",
                json={"conversations": payload},
                headers=headers
            )
            response.raise_for_status()
            
            return {
                "success": True,
                "synced_ids": [c["conversation_id"] for c in conversations],
                "error": None
            }
    except httpx.HTTPStatusError as e:
        return {"success": False, "synced_ids": [], "error": f"HTTP {e.response.status_code}"}
    except httpx.RequestError as e:
        return {"success": False, "synced_ids": [], "error": str(e)}


async def mark_as_synced(conn, conversation_ids: list):
    """Mark conversations as synced."""
    now = datetime.utcnow().isoformat()
    for cid in conversation_ids:
        conn.execute(
            "UPDATE ai_conversations SET synced_at = ? WHERE conversation_id = ?",
            (now, cid)
        )
    conn.commit()


async def run_sync_cycle(conn):
    """Run a single sync cycle."""
    try:
        conversations = await get_unsynced_conversations(conn)
        if not conversations:
            return {"synced": 0, "error": None}
        
        result = await sync_to_master(conn, conversations)
        if result["success"]:
            await mark_as_synced(conn, result["synced_ids"])
            print(f"[Sync] Synced {len(result['synced_ids'])} conversations to master")
            return {"synced": len(result["synced_ids"]), "error": None}
        else:
            print(f"[Sync] Failed: {result['error']}")
            return {"synced": 0, "error": result["error"]}
    except Exception as e:
        print(f"[Sync] Exception: {e}")
        return {"synced": 0, "error": str(e)}


async def start_sync_loop(get_conn_func):
    """
    Start the background sync loop.
    get_conn_func should be a callable that returns a database connection.
    """
    if not MASTER_SERVER_URL:
        print("[Sync] No MASTER_SERVER_URL configured, sync disabled")
        return
    
    print(f"[Sync] Starting sync loop, interval={SYNC_INTERVAL_SECONDS}s")
    
    while True:
        try:
            conn = get_conn_func()
            if conn:
                await run_sync_cycle(conn)
                conn.close()
        except Exception as e:
            print(f"[Sync] Loop error: {e}")
        
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def trigger_sync_now(conn):
    """Manually trigger a sync (synchronous wrapper for use on app close)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Schedule in existing loop
            asyncio.ensure_future(run_sync_cycle(conn))
        else:
            loop.run_until_complete(run_sync_cycle(conn))
    except Exception as e:
        print(f"[Sync] Manual trigger failed: {e}")
