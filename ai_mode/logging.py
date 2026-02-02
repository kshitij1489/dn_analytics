"""
AI Mode: logging interactions to ai_logs (Phase 6: pipeline metadata, no large payloads).
"""

import json
import uuid
from typing import List, Optional

from src.api.models import AIResponse

# Phase 6: max payload size; beyond this we store a summary only (no full result data)
MAX_PAYLOAD_CHARS = 2000


def _payload_summary(response: AIResponse) -> str:
    """Build a small summary of response content for storage (avoid large result data)."""
    content = response.content
    if isinstance(content, list):
        if response.type == "multi":
            return json.dumps({"type": "multi", "parts": len(content)})
        # table rows
        return json.dumps({"type": "table", "row_count": len(content)})
    if isinstance(content, dict):
        if "data" in content and isinstance(content["data"], list):
            return json.dumps({"type": "chart", "data_points": len(content["data"])})
        return json.dumps({"type": "chart", "keys": list(content.keys())[:5]})
    if isinstance(content, str):
        if len(content) <= MAX_PAYLOAD_CHARS:
            return json.dumps({"text": content})
        return json.dumps({"text_preview": content[:MAX_PAYLOAD_CHARS] + "...", "len": len(content)})
    return json.dumps({"type": response.type})


def log_interaction(
    conn,
    query: str,
    intent: str,
    response: AIResponse,
    sql: str = None,
    error: str = None,
    *,
    raw_user_query: Optional[str] = None,
    corrected_query: Optional[str] = None,
    action_sequence: Optional[List[str]] = None,
    explanation: Optional[str] = None,
) -> Optional[str]:
    """
    Log the AI interaction to the database.
    Phase 6: stores raw_user_query, corrected_query, action_sequence, explanation;
    limits response_payload to a summary when large (no full result data).
    Returns query_id (one per user query + AI response).
    """
    try:
        query_id = str(uuid.uuid4())

        payload = None
        if isinstance(response.content, (dict, list)):
            payload = json.dumps(response.content)
        elif isinstance(response.content, str):
            payload = json.dumps({"text": response.content})
        if payload and len(payload) > MAX_PAYLOAD_CHARS:
            payload = _payload_summary(response)

        action_sequence_json = json.dumps(action_sequence) if action_sequence is not None else None
        raw_q = raw_user_query
        corrected_q = corrected_query or query

        query_sql = """
            INSERT INTO ai_logs
            (query_id, user_query, intent, sql_generated, response_type, response_payload, error_message, created_at,
             raw_user_query, corrected_query, action_sequence, explanation)
            VALUES (:query_id, :query, :intent, :sql, :type, :payload, :error, datetime('now'),
                    :raw_user_query, :corrected_query, :action_sequence, :explanation)
        """
        conn.execute(query_sql, {
            "query_id": query_id,
            "query": query,
            "intent": intent,
            "sql": sql,
            "type": response.type,
            "payload": payload,
            "error": error,
            "raw_user_query": raw_q,
            "corrected_query": corrected_q,
            "action_sequence": action_sequence_json,
            "explanation": explanation,
        })
        conn.commit()
        return query_id
    except Exception as e:
        # Phase 6: if new columns missing (migration not run), fall back to minimal insert
        if "raw_user_query" in str(e) or "no such column" in str(e).lower():
            try:
                return _log_interaction_fallback(conn, query, intent, response, sql, error)
            except Exception as e2:
                try:
                    from src.core.error_log import get_error_logger
                    get_error_logger().exception("Error logging interaction (fallback)")
                except Exception:
                    pass
                return None
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().exception("Error logging interaction")
        except Exception:
            pass
        return None


def _log_interaction_fallback(
    conn, query: str, intent: str, response: AIResponse, sql: str = None, error: str = None
) -> Optional[str]:
    """Fallback insert when Phase 6 columns are not present (pre-migration)."""
    query_id = str(uuid.uuid4())
    payload = None
    if isinstance(response.content, (dict, list)):
        payload = json.dumps(response.content)
    elif isinstance(response.content, str):
        payload = json.dumps({"text": response.content})
    if payload and len(payload) > MAX_PAYLOAD_CHARS:
        payload = _payload_summary(response)

    query_sql = """
        INSERT INTO ai_logs
        (query_id, user_query, intent, sql_generated, response_type, response_payload, error_message, created_at)
        VALUES (:query_id, :query, :intent, :sql, :type, :payload, :error, datetime('now'))
    """
    conn.execute(query_sql, {
        "query_id": query_id,
        "query": query,
        "intent": intent,
        "sql": sql,
        "type": response.type,
        "payload": payload,
        "error": error
    })
    conn.commit()
    return query_id
