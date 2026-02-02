"""
AI Mode: database schema context for SQL/chart generation.
"""

import hashlib
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def get_schema_context():
    """Read the base schema from the SQL file. Cached for performance."""
    try:
        # ai_mode/llm/schema.py -> ai_mode -> project root -> database/schema_sqlite.sql
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        schema_path = os.path.join(base, "database", "schema_sqlite.sql")
        with open(schema_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading schema: {str(e)}"


def clear_schema_cache():
    """
    Clear the cached schema. Call this during development after schema changes.
    If you edit schema_sqlite.sql without restarting the server, call this (or restart)
    so get_schema_hash() and LLM cache keys reflect the new schema; otherwise
    cached SQL may not invalidate on hot reload.
    """
    get_schema_context.cache_clear()


def get_schema_hash() -> str:
    """
    Hash of the schema content. Use this in LLM cache keys for generate_sql and
    generate_chart_config so cache invalidates when the schema changes.
    Must be derived from get_schema_context() (same source as SQL/chart generators).
    """
    return hashlib.sha256(get_schema_context().encode("utf-8")).hexdigest()
