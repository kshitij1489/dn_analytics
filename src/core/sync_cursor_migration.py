"""
One-time pull-cursor schema migration for the server's cursor-ordering change.

Phase S1 on the server reordered merge-event replay from
(occurred_at, ingested_at, id) to (ingested_at, id) and moved cursors to a v2
format; v1 cursors are rejected. Rather than translating old cursors, delete
them once and re-pull from the beginning: every applied event is deduped via
the *_remote_events tables, so a full re-pull is safe (plan: Phase C1.4).
"""

import logging

logger = logging.getLogger(__name__)

SYNC_CURSOR_SCHEMA_VERSION_KEY = "sync_cursor_schema_version"
SYNC_CURSOR_SCHEMA_VERSION = "2"

_PULL_CURSOR_KEYS = (
    "menu_merge_pull_cursor",
    "menu_mapping_verification_pull_cursor",
    "customer_merge_pull_cursor",
)


def ensure_sync_cursor_schema(conn) -> bool:
    """
    Reset all pull cursors once when the cursor schema version is outdated.

    Returns True when a reset was performed. Idempotent and cheap when the
    version already matches, so every pull path can call it defensively.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    row = conn.execute(
        "SELECT value FROM system_config WHERE key = ? LIMIT 1",
        (SYNC_CURSOR_SCHEMA_VERSION_KEY,),
    ).fetchone()
    if row and str(row[0]) == SYNC_CURSOR_SCHEMA_VERSION:
        return False

    placeholders = ",".join("?" for _ in _PULL_CURSOR_KEYS)
    conn.execute(
        f"DELETE FROM system_config WHERE key IN ({placeholders})",
        list(_PULL_CURSOR_KEYS),
    )
    conn.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (SYNC_CURSOR_SCHEMA_VERSION_KEY, SYNC_CURSOR_SCHEMA_VERSION),
    )
    conn.commit()
    logger.info(
        "Reset sync pull cursors for cursor schema v%s (server ordering change)",
        SYNC_CURSOR_SCHEMA_VERSION,
    )
    return True
