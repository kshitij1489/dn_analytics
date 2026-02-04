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


# Configuration from environment (fallback for legacy/env-based usage)
MASTER_SERVER_URL = os.environ.get("MASTER_SERVER_URL", "")
MASTER_SERVER_API_KEY = os.environ.get("MASTER_SERVER_API_KEY", "")


def row_to_dict(cursor, row):
    """Helper to convert a row (tuple or sqlite3.Row) to a dictionary."""
    if isinstance(row, tuple):
        columns = [col[0] for col in cursor.description]
        return dict(zip(columns, row))
    return dict(row)

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
    
    return [row_to_dict(cursor, row) for row in cursor.fetchall()]


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
            query_id,
            query_status,
            created_at
        FROM ai_messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC
    """, (conversation_id,))
    
    messages = []
    
    for row in cursor.fetchall():
        msg = row_to_dict(cursor, row)
        
        # Parse content if JSON
        try:
            msg["content"] = json.loads(msg["content"])
        except (json.JSONDecodeError, TypeError):
            pass
        messages.append(msg)
    return messages


async def sync_to_master(conn, conversations: list, base_url: str = None, auth: str = None) -> dict:
    """
    Sync conversations to master server.
    Returns {"success": bool, "synced_ids": [...], "error": str or None}
    """
    # Use provided base_url or fallback to env var
    url = (base_url or MASTER_SERVER_URL).rstrip("/")
    api_key = auth if auth is not None else MASTER_SERVER_API_KEY
    
    if not url:
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
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{url}/api/conversations/sync",
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


async def run_sync_cycle(conn, base_url: str = None, auth: str = None):
    """Run a single sync cycle."""
    try:
        conversations = await get_unsynced_conversations(conn)
        if not conversations:
            return {"synced": 0, "error": None}
        
        result = await sync_to_master(conn, conversations, base_url=base_url, auth=auth)
        if result["success"]:
            await mark_as_synced(conn, result["synced_ids"])
            # print(f"[Sync] Synced {len(result['synced_ids'])} conversations to master")
            return {"synced": len(result["synced_ids"]), "error": None}
        else:
            # print(f"[Sync] Failed: {result['error']}")
            return {"synced": 0, "error": result["error"]}
    except Exception as e:
        print(f"[Sync] Exception: {e}")
        return {"synced": 0, "error": str(e)}



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
