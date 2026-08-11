"""
AI Mode: database schema context for SQL/chart generation.

The prompts only ever need the analytics tables. Feeding the LLM the whole
900-line DDL (sync/merge/forecast/AI-log/system tables, indexes, backfill
INSERTs) burned ~7-8k tokens per call and let the model write queries against
internal plumbing. get_schema_context() now emits a curated allowlist only.

With a live DB connection it introspects sqlite_master, so the schema (and its
hash, used in LLM cache keys) reflects the ACTUAL migrated DB — not the shipped
file, which can drift in packaged builds. Without a connection (the SQL Console
prompt endpoint) it falls back to the shipped schema file.
"""

import hashlib
import os
import sqlite3
from functools import lru_cache

# Tables/views the NL->SQL and chart prompts are allowed to see. Everything else
# in the DB is internal plumbing the LLM must not query. Fixed order so the
# emitted DDL — and get_schema_hash() derived from it — is deterministic.
ALLOWLIST_TABLES = (
    "restaurants",
    "customers",
    "customer_addresses",
    "menu_items",
    "variants",
    "menu_item_variants",
    "menu_items_summary_view",
    "orders",
    "order_items",
    "order_item_addons",
    "order_taxes",
    "order_discounts",
    "weather_daily",
)


def _read_schema_file() -> str:
    """Raw contents of the shipped schema_sqlite.sql (full file, uncurated)."""
    try:
        # ai_mode/llm/schema.py -> ai_mode -> project root -> database/schema_sqlite.sql
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        schema_path = os.path.join(base, "database", "schema_sqlite.sql")
        with open(schema_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading schema: {str(e)}"


def _curated_from_conn(conn) -> str:
    """
    DDL for the allowlisted tables/views present in `conn`, in ALLOWLIST_TABLES order.
    Returns '' if introspection fails or none are present (caller falls back to file).
    """
    try:
        placeholders = ",".join("?" for _ in ALLOWLIST_TABLES)
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type IN ('table','view') AND sql IS NOT NULL "
            f"AND name IN ({placeholders})",
            ALLOWLIST_TABLES,
        ).fetchall()
    except Exception:
        return ""
    # positional access works for both sqlite3.Row and plain tuples
    by_name = {row[0]: row[1] for row in rows}
    parts = [by_name[n].strip() + ";" for n in ALLOWLIST_TABLES if n in by_name]
    return "\n\n".join(parts)


@lru_cache(maxsize=1)
def _curated_from_file() -> str:
    """
    Fallback when there is no live DB connection (e.g. the SQL Console prompt).
    Loads the shipped schema into an in-memory SQLite DB and introspects it with
    the same allowlist — robustly handling view/subquery parentheses that a text
    parser would choke on. Cached; cleared via clear_schema_cache().
    """
    raw = _read_schema_file()
    try:
        mem = sqlite3.connect(":memory:")
        try:
            mem.executescript(raw)
        except Exception:
            # Partial apply is fine: introspect whatever tables did get created.
            pass
        curated = _curated_from_conn(mem)
        mem.close()
        if curated:
            return curated
    except Exception:
        pass
    return raw  # last resort: full file (previous behaviour)


def get_schema_context(conn=None) -> str:
    """
    Curated schema DDL (allowlisted analytics tables/views only) for the SQL and
    chart prompts.

    With a live DB `conn`, introspect the actual DB via sqlite_master — reflects
    real migrations (fixes packaged-app drift where the shipped file and the live
    DB differ) and makes get_schema_hash() invalidate LLM caches when the DB
    changes. Without a conn, fall back to the shipped schema file. Internal
    sync/merge/forecast/AI-log tables are excluded either way.
    """
    if conn is not None:
        curated = _curated_from_conn(conn)
        if curated:
            return curated
    return _curated_from_file()


def clear_schema_cache():
    """
    Clear the cached file-based schema. Call this during development after editing
    schema_sqlite.sql without restarting the server, so get_schema_hash() and LLM
    cache keys reflect the new schema. (The live-DB path is not cached.)
    """
    _curated_from_file.cache_clear()


def get_schema_hash(conn=None) -> str:
    """
    Hash of the curated schema. Used in LLM cache keys for generate_sql and
    generate_chart_config so cache invalidates when the schema changes. Pass the
    live `conn` (same source the generators use) so the hash tracks the real DB.
    """
    return hashlib.sha256(get_schema_context(conn).encode("utf-8")).hexdigest()
