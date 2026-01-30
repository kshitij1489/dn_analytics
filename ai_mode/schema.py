"""
AI Mode: database schema context for SQL/chart generation.
"""

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def get_schema_context():
    """Read the base schema from the SQL file."""
    try:
        # ai_mode/schema.py -> project root -> database/schema_sqlite.sql
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        schema_path = os.path.join(base, "database", "schema_sqlite.sql")
        with open(schema_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading schema: {str(e)}"
