"""Canonical schema and connection helpers for the app-level control database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Optional


CONTROL_SCHEMA_VERSION = 4

PROFILE_LOCAL_CONFIG_KEYS = {
    "sync_device_id",
    "sync_install_id",
    "menu_state_revision",
    "customer_state_revision",
    "menu_assignments_bootstrapped",
    "menu_bootstrap_apply_mode",
    "central_forecast_cursor",
    "central_forecast_status",
    "menu_bootstrap_last_push_hash",
    "sync_cursor_schema_version",
}
PROFILE_LOCAL_CONFIG_SUFFIXES = ("_pull_cursor", "_sync_cursor", "_bootstrapped")


def _is_global_config_key(key: str) -> bool:
    return key not in PROFILE_LOCAL_CONFIG_KEYS and not key.endswith(PROFILE_LOCAL_CONFIG_SUFFIXES)

CONTROL_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS restaurant_profiles (
    restaurant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    database_path TEXT NOT NULL UNIQUE,
    local_address TEXT,
    authorization_state TEXT NOT NULL CHECK (
        authorization_state IN ('authorized', 'unauthorized')
    ),
    last_listed_at TEXT,
    last_sync_status TEXT,
    last_sync_at TEXT,
    menu_group_id TEXT,
    menu_capabilities TEXT NOT NULL DEFAULT '[]',
    clean_rebuild_status TEXT CHECK (
        clean_rebuild_status IS NULL
        OR clean_rebuild_status IN ('required', 'rebuilding', 'complete')
    ),
    last_archive_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (menu_group_id IS NULL OR TRIM(menu_group_id) <> '')
);

CREATE TABLE IF NOT EXISTS app_selection (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    selection_mode TEXT NOT NULL CHECK (selection_mode IN ('restaurant', 'all')),
    restaurant_id TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (restaurant_id) REFERENCES restaurant_profiles(restaurant_id),
    -- The All Stores token is a local selection mode, never a profile identity:
    -- it must never be storable in a restaurant_id column.
    CHECK (
        (selection_mode = 'restaurant' AND restaurant_id IS NOT NULL)
        OR (selection_mode = 'all' AND restaurant_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS global_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS control_metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def app_data_root() -> Path:
    explicit = (os.environ.get("ANALYTICS_APP_DATA_ROOT") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    analytics_path = Path(
        os.environ.get("ANALYTICS_DB_PATH")
        or os.environ.get("DB_URL")
        or Path(__file__).resolve().parents[3] / "analytics.db"
    ).expanduser().resolve()
    return analytics_path.parent


def control_runtime_enabled() -> bool:
    """Electron sets an explicit control location; standalone legacy/test DBs do not."""
    return bool(
        (os.environ.get("ANALYTICS_CONTROL_DB_PATH") or "").strip()
        or (os.environ.get("ANALYTICS_APP_DATA_ROOT") or "").strip()
    )


def existing_analytics_db_path() -> Path:
    return Path(
        os.environ.get("ANALYTICS_DB_PATH")
        or os.environ.get("DB_URL")
        or app_data_root() / "analytics.db"
    ).expanduser().resolve()


def control_db_path() -> Path:
    return Path(
        os.environ.get("ANALYTICS_CONTROL_DB_PATH")
        or app_data_root() / "analytics-control.db"
    ).expanduser().resolve()


def get_control_connection() -> sqlite3.Connection:
    path = control_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_app_selection(conn: sqlite3.Connection) -> None:
    """Widen a revision-1 `app_selection` so All Stores can be persisted.

    Revision 1 pinned `selection_mode` to `'restaurant'` and required a
    restaurant ID, so `CREATE TABLE IF NOT EXISTS` alone would leave an upgraded
    install unable to store the Phase 2 selection. Rebuild it in place, keeping
    the existing physical selection.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='app_selection'"
    ).fetchone()
    if not row or "selection_mode IN ('restaurant', 'all')" in str(row[0]):
        return
    conn.execute("ALTER TABLE app_selection RENAME TO app_selection_legacy")
    conn.executescript(CONTROL_SCHEMA_SQL)
    conn.execute(
        """
        INSERT INTO app_selection (singleton_id, selection_mode, restaurant_id, updated_at)
        SELECT singleton_id, selection_mode, restaurant_id, updated_at
        FROM app_selection_legacy
        """
    )
    conn.execute("DROP TABLE app_selection_legacy")


def ensure_control_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    owned = conn is None
    target = conn or get_control_connection()
    try:
        target.executescript(CONTROL_SCHEMA_SQL)
        profile_columns = {
            str(row[1]) for row in target.execute("PRAGMA table_info(restaurant_profiles)").fetchall()
        }
        if "menu_group_id" not in profile_columns:
            target.execute("ALTER TABLE restaurant_profiles ADD COLUMN menu_group_id TEXT")
        if "menu_capabilities" not in profile_columns:
            target.execute(
                "ALTER TABLE restaurant_profiles ADD COLUMN menu_capabilities TEXT NOT NULL DEFAULT '[]'"
            )
        if "clean_rebuild_status" not in profile_columns:
            target.execute(
                "ALTER TABLE restaurant_profiles ADD COLUMN clean_rebuild_status TEXT"
            )
        if "last_archive_path" not in profile_columns:
            target.execute(
                "ALTER TABLE restaurant_profiles ADD COLUMN last_archive_path TEXT"
            )
        # Revision 1.7 has no in-place shared-POS profile migration. Once the
        # central registry advertises the shared-POS capability, an existing
        # profile must be archived and recreated before ordinary runtime code
        # opens it. This marker lives in the control DB specifically so checking
        # it never needs to inspect the old analytics schema.
        #
        # Capability absence preserves legacy behavior: a profile whose group
        # never advertises the policy keeps opening in place, upgraded by the
        # additive column migrations in db/connection.py.
        target.execute(
            """
            UPDATE restaurant_profiles
            SET clean_rebuild_status='required', updated_at=CURRENT_TIMESTAMP
            WHERE clean_rebuild_status IS NULL
              AND menu_capabilities LIKE '%\"global_menu_shared_pos_catalog_v1\"%'
            """
        )
        _migrate_app_selection(target)
        target.execute(
            """
            INSERT INTO control_metadata (key, value, updated_at)
            VALUES ('schema_version', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (str(CONTROL_SCHEMA_VERSION),),
        )
        target.commit()
    finally:
        if owned:
            target.close()


def get_global_config(keys: Optional[Iterable[str]] = None) -> Dict[str, str]:
    ensure_control_schema()
    conn = get_control_connection()
    try:
        if keys is None:
            rows = conn.execute("SELECT key, value FROM global_config").fetchall()
        else:
            wanted = list(keys)
            if not wanted:
                return {}
            placeholders = ",".join("?" for _ in wanted)
            rows = conn.execute(
                f"SELECT key, value FROM global_config WHERE key IN ({placeholders})",
                wanted,
            ).fetchall()
        return {str(row["key"]): row["value"] for row in rows}
    finally:
        conn.close()


def resolve_config_values(conn, keys: Iterable[str]) -> Dict[str, str]:
    """Read app-global settings from the control DB, falling back to a profile's
    legacy `system_config` rows.

    A registered profile uses the control database first. An explicit
    standalone/support/test database is not a profile and reads its own legacy
    rows instead, so it cannot accidentally inherit production credentials from
    another file. The fallback never overrides a populated control value.
    """
    wanted = [str(key) for key in keys]
    if not wanted:
        return {}
    values: Dict[str, str] = {}
    if conn is None or _is_registered_profile_connection(conn):
        try:
            values = {
                key: value
                for key, value in get_global_config(wanted).items()
                if value is not None and str(value).strip() != ""
            }
        except Exception:
            values = {}
    missing = [key for key in wanted if key not in values]
    if not missing or conn is None:
        return values
    try:
        placeholders = ",".join("?" for _ in missing)
        rows = conn.execute(
            f"SELECT key, value FROM system_config WHERE key IN ({placeholders})",
            missing,
        ).fetchall()
        for row in rows:
            key, value = str(row[0]), row[1]
            if value is not None and str(value).strip() != "":
                values[key] = value
    except Exception:
        pass
    return values


def set_global_config(settings: Dict[str, str]) -> None:
    ensure_control_schema()
    conn = get_control_connection()
    try:
        for key, value in settings.items():
            conn.execute(
                """
                INSERT INTO global_config (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def _is_registered_profile_connection(conn) -> bool:
    """True only when `conn` is the exact file registered for its identity."""
    if conn is None:
        return False
    try:
        identity = conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()
        if not identity:
            return False
        database_rows = conn.execute("PRAGMA database_list").fetchall()
        main_path = next(
            (str(row[2]) for row in database_rows if row[1] == "main"),
            "",
        )
        if not main_path:
            return False
        ensure_control_schema()
        control = get_control_connection()
        try:
            registered = control.execute(
                "SELECT database_path FROM restaurant_profiles WHERE restaurant_id=?",
                (str(identity[0]),),
            ).fetchone()
        finally:
            control.close()
        return bool(
            registered
            and Path(main_path).expanduser().resolve()
            == Path(registered[0]).expanduser().resolve()
        )
    except Exception:
        return False


def delete_global_config_matching(patterns: Iterable[str]) -> None:
    """Delete app-global settings using a fixed caller-supplied key pattern list."""
    wanted = tuple(str(pattern) for pattern in patterns if str(pattern))
    if not wanted:
        return
    ensure_control_schema()
    conn = get_control_connection()
    try:
        where = " OR ".join("key LIKE ?" for _ in wanted)
        conn.execute(f"DELETE FROM global_config WHERE {where}", wanted)
        conn.commit()
    finally:
        conn.close()


def copy_legacy_global_config_once(analytics_path: Optional[Path] = None) -> None:
    """Copy legacy configuration without deleting rollback evidence."""
    ensure_control_schema()
    path = (analytics_path or existing_analytics_db_path()).resolve()
    if not path.exists():
        return
    control = get_control_connection()
    try:
        rebuild = control.execute(
            """
            SELECT clean_rebuild_status
            FROM restaurant_profiles WHERE database_path=?
            """,
            (str(path),),
        ).fetchone()
        if rebuild and str(rebuild[0] or "") == "required":
            # Revision-1.7 clean rebuilds treat the old profile as opaque. Any
            # global config needed for cutover must already be in the preserved
            # control DB; do not inspect the revision-1.6 system_config table.
            return
        marker = control.execute(
            "SELECT value FROM control_metadata WHERE key='legacy_global_config_copied'"
        ).fetchone()
        if marker:
            return
        legacy = sqlite3.connect(str(path))
        try:
            exists = legacy.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_config'"
            ).fetchone()
            rows = legacy.execute("SELECT key, value FROM system_config").fetchall() if exists else []
        finally:
            legacy.close()
        for key, value in rows:
            if not _is_global_config_key(str(key)):
                continue
            control.execute(
                "INSERT OR IGNORE INTO global_config (key, value) VALUES (?, ?)",
                (key, value),
            )
        control.execute(
            "INSERT OR REPLACE INTO control_metadata (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            ("legacy_global_config_copied", "1"),
        )
        control.commit()
    finally:
        control.close()
