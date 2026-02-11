"""
Client learning shipper: upload ai_logs + ai_feedback + Tier 3 (cache_stats, aggregated_counters, schema_hash) to cloud.

Uses CLIENT_LEARNING_INGEST_URL (placeholder by default). When cloud server is ready,
set the env var to the real URL for plug-and-play. Call upload_pending(conn) periodically.
All tiers (1 + 3) go in one POST; no problem having them together.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.config.client_learning_config import (
    CLIENT_LEARNING_INGEST_URL,
)

# Max rows per batch to avoid huge payloads
BATCH_LIMIT_AI_LOGS = 500
BATCH_LIMIT_AI_FEEDBACK = 500


# --- Tier 3: cache stats, aggregated counters, schema hash ---

def _get_cache_stats() -> Dict[str, Any]:
    """LLM cache: total entries and count by call_id. No raw keys."""
    try:
        from ai_mode.cache.cache_config import CACHE_DB_PATH
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=5.0)
        try:
            cur = conn.execute(
                "SELECT call_id, COUNT(*) as cnt FROM llm_cache GROUP BY call_id"
            )
            by_call_id = {row[0]: row[1] for row in cur.fetchall()}
            total = sum(by_call_id.values())
            return {"total_entries": total, "by_call_id": by_call_id}
        finally:
            conn.close()
    except Exception:
        return {"total_entries": 0, "by_call_id": {}}


def _get_aggregated_counters(conn) -> Dict[str, Any]:
    """From ai_logs: intents per day (last 7d), response_type counts, total last 7d."""
    out: Dict[str, Any] = {"intents_per_day": [], "response_type_counts": {}, "total_ai_logs_7d": 0}
    try:
        cur = conn.execute("""
            SELECT intent, date(created_at) as d, COUNT(*) as cnt
            FROM ai_logs
            WHERE created_at >= date('now', '-7 days')
            GROUP BY intent, d
            ORDER BY d, intent
        """)
        out["intents_per_day"] = [{"intent": r[0], "date": r[1], "count": r[2]} for r in cur.fetchall()]
        cur = conn.execute("""
            SELECT response_type, COUNT(*) FROM ai_logs
            WHERE created_at >= date('now', '-7 days')
            GROUP BY response_type
        """)
        out["response_type_counts"] = {str(r[0]) if r[0] else "null": r[1] for r in cur.fetchall()}
        cur = conn.execute(
            "SELECT COUNT(*) FROM ai_logs WHERE created_at >= date('now', '-7 days')"
        )
        out["total_ai_logs_7d"] = cur.fetchone()[0]
    except sqlite3.OperationalError:
        pass
    return out


def _get_schema_hash() -> Optional[str]:
    """Hash of get_schema_context() for schema diversity / cache invalidation."""
    try:
        from ai_mode.llm.schema import get_schema_hash
        return get_schema_hash()
    except Exception:
        return None


def _select_incorrect_cache_entries(limit: int = 100) -> List[Dict[str, Any]]:
    """Select llm_cache rows where is_incorrect = 1."""
    try:
        from ai_mode.cache.cache_config import CACHE_DB_PATH
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            # Check if column exists first (it should now, but for safety)
            cursor = conn.execute("""
                SELECT key_hash, call_id, value, created_at, last_used_at, is_incorrect
                FROM llm_cache
                WHERE is_incorrect = 1
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
    except Exception:
        return []

def _select_unsent_ai_logs(conn, limit: int = BATCH_LIMIT_AI_LOGS) -> List[Dict[str, Any]]:
    """Select ai_logs rows where uploaded_at IS NULL. Prefer columns that exist."""
    conn.row_factory = None
    cursor = conn.execute("""
        SELECT query_id, user_query, intent, sql_generated, response_type, response_payload,
               error_message, execution_time_ms, created_at,
               raw_user_query, corrected_query, action_sequence, explanation
        FROM ai_logs
        WHERE uploaded_at IS NULL
        ORDER BY created_at ASC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    out = []
    for row in rows:
        d = dict(zip(cols, row))
        # JSON-decode where needed
        if isinstance(d.get("action_sequence"), str):
            try:
                d["action_sequence"] = json.loads(d["action_sequence"]) if d["action_sequence"] else None
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(d)
    return out


def _select_unsent_ai_feedback(conn, limit: int = BATCH_LIMIT_AI_FEEDBACK) -> List[Dict[str, Any]]:
    """Select ai_feedback rows where uploaded_at IS NULL."""
    cursor = conn.execute("""
        SELECT feedback_id, query_id, is_positive, comment, created_at
        FROM ai_feedback
        WHERE uploaded_at IS NULL
        ORDER BY created_at ASC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


def upload_pending(
    conn,
    endpoint: Optional[str] = None,
    auth: Optional[str] = None,
    uploaded_by: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Select unsent ai_logs and ai_feedback, add Tier 3 (cache_stats, aggregated_counters, schema_hash),
    POST to cloud, then set uploaded_at for sent rows.
    uploaded_by: optional {"employee_id": "...", "name": "..."} from app_users; appended to payload.
    Returns {"ai_logs_sent": int, "ai_feedback_sent": int, "tier3_included": True, "error": str or None}.
    All tiers in one POST; no problem having them together.
    """
    url = (endpoint or CLIENT_LEARNING_INGEST_URL).strip()
    if not url:
        return {"ai_logs_sent": 0, "ai_feedback_sent": 0, "tier3_included": False, "error": None}

    try:
        ai_logs = _select_unsent_ai_logs(conn)
        ai_feedback = _select_unsent_ai_feedback(conn)
    except sqlite3.OperationalError:
        ai_logs = []
        ai_feedback = []

    # Tier 3: always include so cloud gets cache/aggregates/schema even when no new logs
    cache_stats = _get_cache_stats()
    aggregated_counters = _get_aggregated_counters(conn)
    schema_hash = _get_schema_hash()
    llm_cache_feedback = _select_incorrect_cache_entries()
    # Always POST when URL is set so Tier 3 is sent every run (cache/aggregates/schema)
    headers = {"Content-Type": "application/json"}
    token = auth
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload: Dict[str, Any] = {
        "ai_logs": ai_logs,
        "ai_feedback": ai_feedback,
        "cache_stats": cache_stats,
        "aggregated_counters": aggregated_counters,
        "schema_hash": schema_hash,
        "llm_cache_feedback": llm_cache_feedback,
    }
    if uploaded_by:
        payload["uploaded_by"] = uploaded_by

    try:
        import requests
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code >= 400:
            return {"ai_logs_sent": 0, "ai_feedback_sent": 0, "tier3_included": True, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ai_logs_sent": 0, "ai_feedback_sent": 0, "tier3_included": True, "error": str(e)}

    now = datetime.now(timezone.utc).isoformat()
    if ai_logs:
        query_ids = [r["query_id"] for r in ai_logs]
        placeholders = ",".join("?" * len(query_ids))
        conn.execute(
            f"UPDATE ai_logs SET uploaded_at = ? WHERE query_id IN ({placeholders})",
            [now] + query_ids,
        )
    if ai_feedback:
        feedback_ids = [r["feedback_id"] for r in ai_feedback]
        placeholders = ",".join("?" * len(feedback_ids))
        conn.execute(
            f"UPDATE ai_feedback SET uploaded_at = ? WHERE feedback_id IN ({placeholders})",
            [now] + feedback_ids,
        )
    conn.commit()

    return {
        "ai_logs_sent": len(ai_logs),
        "ai_feedback_sent": len(ai_feedback),
        "tier3_included": True,
        "error": None,
    }
