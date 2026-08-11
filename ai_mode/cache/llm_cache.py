"""
LLM response cache: SQLite-backed exact-key cache with get_or_call.

Used to avoid calling the LLM when we have a cached response for the same
(call_id, key_parts). See docs/LLM_CACHE_PLAN.md.

- Key: hash of (call_id, key_parts). key_parts must be JSON-serializable.
- Value: any JSON-serializable object (string, dict, list). Stored as JSON.
- Eviction: LRU by last_used_at (or created_at when never read). Global max
  entries from cache_config.MAX_ENTRIES.
"""

import hashlib
import json
import random
import re
import sqlite3
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from ai_mode.cache.cache_config import CACHE_DB_PATH, DIVERSITY_CACHE_SIZE, MAX_ENTRIES
from ai_mode.telemetry import record_cache_hit

_TABLE = "llm_cache"
# §4 telemetry: global cache hit/miss counters per call_id (cache effectiveness over time)
_COUNTERS = "llm_cache_counters"




def normalize_prompt(prompt: str) -> str:
    """Strip, lowercase and collapse whitespace so ' Hi ' and 'hi' hit the same cache key."""
    if not prompt or not isinstance(prompt, str):
        return ""
    return " ".join(re.split(r"\s+", prompt.strip().lower()))



def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            key_hash TEXT PRIMARY KEY,
            call_id TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            is_incorrect INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # Migration: add is_incorrect if table existed without it
    try:
        conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN is_incorrect INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Optimization: Index for LRU eviction query to avoid full table scan
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_lru ON {_TABLE} (last_used_at, created_at)"
    )
    # Index for cloud sync: fetch entries marked incorrect
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_is_incorrect ON {_TABLE} (is_incorrect) WHERE is_incorrect = 1"
    )
    _ensure_counters_table(conn)


def _ensure_counters_table(conn: sqlite3.Connection) -> None:
    """Create the per-call_id hit/miss counters table if missing (§4 cache effectiveness)."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COUNTERS} (
            call_id TEXT PRIMARY KEY,
            hits INTEGER NOT NULL DEFAULT 0,
            misses INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def _bump_counter(conn: sqlite3.Connection, call_id: str, hit: bool) -> None:
    """Increment the hit or miss counter for call_id on an already-open connection."""
    if hit:
        conn.execute(
            f"INSERT INTO {_COUNTERS} (call_id, hits, misses) VALUES (?, 1, 0) "
            f"ON CONFLICT(call_id) DO UPDATE SET hits = hits + 1",
            (call_id,),
        )
    else:
        conn.execute(
            f"INSERT INTO {_COUNTERS} (call_id, hits, misses) VALUES (?, 0, 1) "
            f"ON CONFLICT(call_id) DO UPDATE SET misses = misses + 1",
            (call_id,),
        )


def bump_cache_counter(call_id: str, hit: bool) -> None:
    """Increment the global hit/miss counter for call_id (own connection). Errors are swallowed."""
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_counters_table(conn)
            _bump_counter(conn, call_id, hit)
            conn.commit()
    except Exception as e:
        print(f"⚠️ LLM cache counter error: {e}")


def get_cache_counters() -> List[dict]:
    """Return [{call_id, hits, misses}] for cache-effectiveness reporting (busiest first)."""
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_counters_table(conn)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT call_id, hits, misses FROM {_COUNTERS} ORDER BY (hits + misses) DESC"
            ).fetchall()
            return [
                {"call_id": r["call_id"], "hits": r["hits"], "misses": r["misses"]}
                for r in rows
            ]
    except Exception as e:
        print(f"⚠️ LLM cache get_cache_counters error: {e}")
        return []


def build_key(call_id: str, key_parts: Tuple[Any, ...]) -> str:
    """
    Build a stable cache key hash from call_id and key_parts.
    key_parts must be JSON-serializable (e.g. (model, normalized_prompt)).
    """
    payload = json.dumps([call_id] + list(key_parts), sort_keys=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get(conn: sqlite3.Connection, key_hash: str) -> Optional[Any]:
    """Return cached value (deserialized) or None. Updates last_used_at on hit.
    Entries flagged is_incorrect by human feedback are treated as a miss (row kept for learning)."""
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute(
        f"UPDATE {_TABLE} SET last_used_at = ? WHERE key_hash = ?",
        (now, key_hash),
    )
    row = conn.execute(
        f"SELECT value, is_incorrect FROM {_TABLE} WHERE key_hash = ?", (key_hash,)
    ).fetchone()
    if row is None:
        return None
    if row[1]:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def _set(
    conn: sqlite3.Connection, key_hash: str, call_id: str, value: Any
) -> None:
    """Store value (JSON-serialized). Evicts oldest entry only when adding a new key at capacity."""
    now = datetime.utcnow().isoformat() + "Z"
    value_str = json.dumps(value, default=str)
    existing = conn.execute(
        f"SELECT 1 FROM {_TABLE} WHERE key_hash = ?", (key_hash,)
    ).fetchone()
    if existing:
        # Fresh value replaces the old one; clear any human is_incorrect flag on the stale value
        conn.execute(
            f"UPDATE {_TABLE} SET value = ?, last_used_at = ?, is_incorrect = 0 WHERE key_hash = ?",
            (value_str, now, key_hash),
        )
        return
    count = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]
    if count >= MAX_ENTRIES:
        conn.execute(
            f"""
            DELETE FROM {_TABLE}
            WHERE key_hash = (
                SELECT key_hash FROM {_TABLE}
                ORDER BY COALESCE(last_used_at, created_at) ASC
                LIMIT 1
            )
            """
        )
    conn.execute(
        f"""
        INSERT INTO {_TABLE} (key_hash, call_id, value, created_at, last_used_at, is_incorrect)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (key_hash, call_id, value_str, now, now),
    )


def get(call_id: str, key_parts: Tuple[Any, ...]) -> Optional[Any]:
    """
    Return cached value for (call_id, key_parts) if present, else None.
    Updates last_used_at on hit. Use for flows where you store one shape but
    return another (e.g. chart: cache config only, re-run SQL on hit).
    """
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_table(conn)
            key_hash = build_key(call_id, key_parts)
            cached = _get(conn, key_hash)
            conn.commit()
            return cached
    except Exception as e:
        print(f"⚠️ LLM cache get error: {e}")
        return None


def set(call_id: str, key_parts: Tuple[Any, ...], value: Any) -> None:
    """Store value for (call_id, key_parts). Evicts if at capacity."""
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_table(conn)
            key_hash = build_key(call_id, key_parts)
            _set(conn, key_hash, call_id, value)
            conn.commit()
    except Exception as e:
        print(f"⚠️ LLM cache set error: {e}")


def get_or_call_diversity(
    call_id: str,
    key_parts: Tuple[Any, ...],
    fn: Callable[[], str],
) -> str:
    """
    Diversity cache for run_general_chat: per prompt, store up to DIVERSITY_CACHE_SIZE
    distinct response strings. When full, randomly pick one instead of calling fn().
    When fewer than full, call fn(); if the new response is not already in the list
    (exact string match), append it. Return the new response or the randomly picked one.
    fn() must return a single string (the LLM response content).
    """
    cached = get(call_id, key_parts)  # get() swallows cache-layer errors and returns None
    if cached is not None:
        if not isinstance(cached, list):
            cached = [cached] if isinstance(cached, str) else []
        if len(cached) >= DIVERSITY_CACHE_SIZE:
            chosen = random.choice(cached)
            record_cache_hit(call_id)
            bump_cache_counter(call_id, hit=True)
            try:
                from ai_mode.debug_log import append_entry, preview_value
                append_entry(call_id, "cache", preview_value(chosen))
            except Exception:
                pass
            return chosen
    # fn() errors propagate uncached so a transient LLM failure is never stored.
    # The LLM round-trip is recorded into telemetry by chat_completion (source=llm).
    bump_cache_counter(call_id, hit=False)
    result = fn()
    try:
        from ai_mode.debug_log import append_entry, preview_value
        append_entry(call_id, "llm", preview_value(result))
    except Exception:
        pass
    existing = cached if isinstance(cached, list) else []
    if result not in existing:
        set(call_id, key_parts, existing + [result])
    return result


def get_or_call(
    call_id: str,
    key_parts: Tuple[Any, ...],
    fn: Callable[[], Any],
) -> Any:
    """
    Return cached value for (call_id, key_parts) if present; otherwise call fn(),
    store the result, and return it.

    key_parts must be JSON-serializable (e.g. (model, normalized_prompt)).
    If the cache DB is unavailable or errors, fn() is called and the result
    is not stored. fn() errors propagate uncached so a transient LLM failure
    is never stored as the answer for this key.
    """
    key_hash = build_key(call_id, key_parts)
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_table(conn)
            cached = _get(conn, key_hash)
            if cached is not None:
                _bump_counter(conn, call_id, hit=True)
            conn.commit()
        if cached is not None:
            record_cache_hit(call_id)
            try:
                from ai_mode.debug_log import append_entry, preview_value
                append_entry(call_id, "cache", preview_value(cached))
            except Exception:
                pass
            return cached
    except Exception as e:
        print(f"⚠️ LLM cache get error, calling LLM: {e}")
    # Cache miss (or cache failure): call fn() outside the lock/connection.
    # The LLM round-trip is recorded into telemetry by chat_completion (source=llm).
    result = fn()
    try:
        from ai_mode.debug_log import append_entry, preview_value
        append_entry(call_id, "llm", preview_value(result))
    except Exception:
        pass
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_table(conn)
            _bump_counter(conn, call_id, hit=False)
            _set(conn, key_hash, call_id, result)
            conn.commit()
    except Exception as e:
        print(f"⚠️ LLM cache set error: {e}")
    return result


def list_entries(limit: int = 500) -> List[dict]:
    """
    Return cache entries for telemetry: key_hash, call_id, value_preview, created_at, last_used_at, is_incorrect.
    value_preview is truncated to 200 chars for display.
    """
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_table(conn)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT key_hash, call_id, value, created_at, last_used_at, is_incorrect
                FROM {_TABLE}
                ORDER BY COALESCE(last_used_at, created_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            out: List[dict] = []
            for row in rows:
                val = row["value"] or ""
                preview = val[:200] + ("..." if len(val) > 200 else "")
                is_incorrect = row["is_incorrect"]
                if is_incorrect is None:
                    is_incorrect = 0
                out.append({
                    "key_hash": row["key_hash"],
                    "call_id": row["call_id"],
                    "value_preview": preview,
                    "created_at": row["created_at"],
                    "last_used_at": row["last_used_at"],
                    "is_incorrect": bool(is_incorrect),
                })
            return out
    except Exception as e:
        print(f"⚠️ LLM cache list_entries error: {e}")
        return []


def set_incorrect(key_hash: str, is_incorrect: bool) -> bool:
    """
    Set the is_incorrect flag for a cache entry (human feedback for cloud learning).
    Returns True if the entry was found and updated.
    """
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_table(conn)
            cur = conn.execute(
                f"UPDATE {_TABLE} SET is_incorrect = ? WHERE key_hash = ?",
                (1 if is_incorrect else 0, key_hash),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"⚠️ LLM cache set_incorrect error: {e}")
        return False


def clear_cache(call_id: Optional[str] = None) -> None:
    """
    Clear the cache.
    If call_id is None, clears EVERYTHING.
    If call_id is provided, clears only entries for that call_id.
    """
    try:
        with sqlite3.connect(CACHE_DB_PATH, timeout=10.0) as conn:
            _ensure_table(conn)
            if call_id is None:
                conn.execute(f"DELETE FROM {_TABLE}")
                print("🧹 LLM Cache cleared (ALL entries).")
            else:
                conn.execute(f"DELETE FROM {_TABLE} WHERE call_id = ?", (call_id,))
                print(f"🧹 LLM Cache cleared for call_id='{call_id}'.")
            conn.commit()
    except Exception as e:
        print(f"⚠️ Error clearing LLM cache: {e}")
