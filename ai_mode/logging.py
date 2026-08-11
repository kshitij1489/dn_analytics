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
    execution_time_ms: Optional[int] = None,
    model: Optional[str] = None,
    total_prompt_tokens: Optional[int] = None,
    total_completion_tokens: Optional[int] = None,
    llm_calls: Optional[int] = None,
    cache_hits: Optional[int] = None,
) -> Optional[str]:
    """
    Log the AI interaction to the database.
    Phase 6: stores raw_user_query, corrected_query, action_sequence, explanation;
    limits response_payload to a summary when large (no full result data).
    execution_time_ms: total orchestrator time (start to finish) for the query.
    §4 telemetry: model, total_prompt_tokens, total_completion_tokens, llm_calls,
    cache_hits (aggregated from the per-step trace) so cost/cache-effectiveness are queryable.
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
             raw_user_query, corrected_query, action_sequence, explanation, execution_time_ms,
             model, total_prompt_tokens, total_completion_tokens, llm_calls, cache_hits)
            VALUES (:query_id, :query, :intent, :sql, :type, :payload, :error, datetime('now'),
                    :raw_user_query, :corrected_query, :action_sequence, :explanation, :execution_time_ms,
                    :model, :total_prompt_tokens, :total_completion_tokens, :llm_calls, :cache_hits)
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
            "execution_time_ms": execution_time_ms,
            "model": model,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "llm_calls": llm_calls,
            "cache_hits": cache_hits,
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


def persist_call_trace(conn, query_id: Optional[str], trace: Optional[List[dict]]) -> None:
    """
    Persist the per-step telemetry trace (§4) to ai_call_trace, keyed by query_id.
    Each entry: {step, source, model, latency_ms, prompt_tokens, completion_tokens}.
    Best-effort: swallows errors (e.g. table missing pre-migration) since the
    aggregate counts are already stored on the ai_logs row.
    """
    if not query_id or not trace:
        return
    try:
        rows = [
            (
                query_id,
                e.get("step"),
                e.get("source"),
                e.get("model"),
                e.get("latency_ms"),
                int(e.get("prompt_tokens") or 0),
                int(e.get("completion_tokens") or 0),
            )
            for e in trace
        ]
        conn.executemany(
            """
            INSERT INTO ai_call_trace
            (query_id, step, source, model, latency_ms, prompt_tokens, completion_tokens, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            rows,
        )
        conn.commit()
    except Exception:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().exception("Error persisting ai_call_trace")
        except Exception:
            pass


def persist_debug_log(conn, query_id: Optional[str], debug_log: Optional[List[dict]]) -> None:
    """
    Persist the request-scoped debug log (§3.7) to ai_debug_log, keyed by query_id.
    Each entry: {step, source, input_preview, output_preview}. Replaces the old
    cross-request-racy in-memory globals; the debug panel reads it back per query.
    Best-effort: swallows errors (e.g. table missing pre-migration).
    """
    if not query_id or not debug_log:
        return
    try:
        rows = [
            (
                query_id,
                i,
                e.get("step"),
                e.get("source"),
                e.get("input_preview", "") or "",
                e.get("output_preview", "") or "",
            )
            for i, e in enumerate(debug_log)
        ]
        conn.executemany(
            """
            INSERT INTO ai_debug_log
            (query_id, seq, step, source, input_preview, output_preview, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            rows,
        )
        conn.commit()
    except Exception:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().exception("Error persisting ai_debug_log")
        except Exception:
            pass


def fetch_debug_entries(conn, query_id: Optional[str] = None) -> List[dict]:
    """
    Read persisted debug entries (§3.7) for the debug panel. With query_id, returns
    that query's steps; without it, returns the most-recently persisted query's steps.
    Race-free: keyed by query_id in the DB, not a shared module global.
    """
    try:
        if query_id:
            cursor = conn.execute(
                """
                SELECT step, source, input_preview, output_preview
                FROM ai_debug_log
                WHERE query_id = ?
                ORDER BY debug_id ASC
                """,
                (query_id,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT step, source, input_preview, output_preview
                FROM ai_debug_log
                WHERE query_id = (SELECT query_id FROM ai_debug_log ORDER BY debug_id DESC LIMIT 1)
                ORDER BY debug_id ASC
                """
            )
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().exception("Error reading ai_debug_log")
        except Exception:
            pass
        return []
