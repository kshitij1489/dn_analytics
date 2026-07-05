"""
Schema helpers for per-order-item assignment sync (plan Phase C3).

Adds the columns and tables the assignment-based applier needs on top of the
long-lived local schema:

- menu_item_variants.assignment_seq: highest server_seq applied to this row.
- menu_item_variants.verification_seq: highest verification-stream server_seq
  applied to this row's is_verified flag. Kept separate from assignment_seq
  because the merge and verification event tables have independent server id
  spaces and their sequences are not comparable.
- menu_item_variants.pending_local: row was rewritten locally and not yet
  acknowledged by the server echo of our own event.
- merge_history.origin: 'remote' for rows written by the remote applier, NULL
  for locally-authored merges (kept nullable so old rows stay valid).
- menu_merge_remote_events.server_seq: server ingestion order of the event,
  used to find which event outranked a local decision.
- menu_sync_supersede_notices: user-visible "your resolution was superseded"
  records surfaced via /api/menu/sync-conflicts.

All ALTERs are conditional so this is safe to call on every connection.
"""

from typing import Set


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_names(conn, table_name: str) -> Set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_column(conn, table_name: str, column_name: str, ddl: str) -> None:
    if not _table_exists(conn, table_name):
        return
    if column_name in _column_names(conn, table_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def ensure_assignment_sync_schema(conn) -> None:
    _ensure_column(conn, "menu_item_variants", "assignment_seq", "assignment_seq INTEGER")
    _ensure_column(conn, "menu_item_variants", "verification_seq", "verification_seq INTEGER")
    _ensure_column(conn, "menu_item_variants", "pending_local", "pending_local INTEGER DEFAULT 0")
    _ensure_column(conn, "merge_history", "origin", "origin TEXT")
    _ensure_column(conn, "menu_merge_remote_events", "server_seq", "server_seq INTEGER")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_sync_supersede_notices (
            notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_item_id TEXT NOT NULL,
            local_merge_id INTEGER,
            superseded_by_event_id TEXT,
            attribution TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            acknowledged_at TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_menu_sync_supersede_notices_open
        ON menu_sync_supersede_notices(acknowledged_at, created_at);
        """
    )
