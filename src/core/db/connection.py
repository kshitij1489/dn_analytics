import os
import sqlite3
from pathlib import Path

# Resolve absolute path to analytics.db (in project root)
# src/core/db/connection.py -> src/core/db -> src/core -> src -> project_root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(BASE_DIR, "analytics.db")

GLOBAL_MENU_ITEMS_TABLE = "global_menu_items"
GLOBAL_MENU_ITEMS_REBUILD_TABLE = "global_menu_items_allow_blank_type"
GLOBAL_MENU_ITEMS_LEGACY_TYPE_CHECK = (
    "canonical_type text not null check (trim(canonical_type) <> '')"
)
GLOBAL_MENU_ITEM_REFERENCE_TABLES = frozenset(
    {
        "global_menu_mapping_rules",
        "global_menu_redirects",
        "menu_item_global_links",
    }
)


def analytics_schema_path() -> Path:
    from src.core.utils.path_helper import get_resource_path

    return Path(get_resource_path(os.path.join("database", "schema_sqlite.sql")))


def _relax_global_menu_item_type_constraint(conn) -> None:
    """Allow the server's valid blank canonical item type on older profiles.

    The initial global-menu projection required a non-blank type even though the
    central contract and Django model allow an empty value. SQLite cannot drop a
    CHECK constraint in place, so preserve the derived projection rows while
    rebuilding only their parent catalog table. Child foreign-key definitions keep
    referencing the stable table name throughout the transaction.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (GLOBAL_MENU_ITEMS_TABLE,),
    ).fetchone()
    table_sql = " ".join(str(row[0] or "").lower().split()) if row else ""
    if GLOBAL_MENU_ITEMS_LEGACY_TYPE_CHECK not in table_sql:
        return

    if conn.in_transaction:
        conn.commit()
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"""
            CREATE TABLE {GLOBAL_MENU_ITEMS_REBUILD_TABLE} (
                global_menu_item_id TEXT PRIMARY KEY
                    CHECK (TRIM(global_menu_item_id) <> ''),
                menu_group_id TEXT NOT NULL CHECK (TRIM(menu_group_id) <> ''),
                canonical_name TEXT NOT NULL CHECK (TRIM(canonical_name) <> ''),
                canonical_type TEXT NOT NULL DEFAULT '',
                is_verified INTEGER NOT NULL DEFAULT 0 CHECK (is_verified IN (0, 1)),
                lifecycle_state TEXT NOT NULL DEFAULT 'active'
                    CHECK (lifecycle_state IN ('active', 'redirected', 'tombstoned')),
                server_revision INTEGER NOT NULL CHECK (server_revision >= 0),
                created_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {GLOBAL_MENU_ITEMS_REBUILD_TABLE} (
                global_menu_item_id, menu_group_id, canonical_name, canonical_type,
                is_verified, lifecycle_state, server_revision, created_at, updated_at
            )
            SELECT
                global_menu_item_id, menu_group_id, canonical_name, canonical_type,
                is_verified, lifecycle_state, server_revision, created_at, updated_at
            FROM {GLOBAL_MENU_ITEMS_TABLE}
            """
        )
        conn.execute(f"DROP TABLE {GLOBAL_MENU_ITEMS_TABLE}")
        conn.execute(
            f"ALTER TABLE {GLOBAL_MENU_ITEMS_REBUILD_TABLE} "
            f"RENAME TO {GLOBAL_MENU_ITEMS_TABLE}"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_global_menu_items_group "
            "ON global_menu_items(menu_group_id, lifecycle_state)"
        )
        violations = [
            violation
            for violation in conn.execute("PRAGMA foreign_key_check").fetchall()
            if str(violation[0]) in GLOBAL_MENU_ITEM_REFERENCE_TABLES
        ]
        if violations:
            raise RuntimeError(
                "Global-menu item constraint upgrade would leave broken references"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(
            f"PRAGMA foreign_keys = {'ON' if foreign_keys_enabled else 'OFF'}"
        )


def apply_analytics_schema(conn) -> None:
    schema_path = analytics_schema_path()
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    schema_sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    _relax_global_menu_item_type_constraint(conn)

    # New global-menu tables are additive. The canonical CREATE statements
    # above upgrade old profile databases; this focused owner validates the
    # projection and initializes its singleton state idempotently.
    from src.core.global_menu_schema import ensure_global_menu_schema

    ensure_global_menu_schema(conn)

    # CREATE TABLE IF NOT EXISTS does not add columns to databases created by
    # older releases. Keep these upgrade migrations beside the canonical schema
    # owner instead of duplicating DDL in API startup or routers.
    additive_columns = {
        "ai_logs": (
            ("uploaded_at", "TEXT"),
            ("model", "TEXT"),
            ("total_prompt_tokens", "INTEGER"),
            ("total_completion_tokens", "INTEGER"),
            ("llm_calls", "INTEGER"),
            ("cache_hits", "INTEGER"),
        ),
        "ai_feedback": (("uploaded_at", "TEXT"),),
        "forecast_cache": (("uploaded_at", "TEXT"),),
        "item_forecast_cache": (("uploaded_at", "TEXT"),),
        "volume_forecast_cache": (("uploaded_at", "TEXT"),),
        "revenue_backtest_cache": (("uploaded_at", "TEXT"),),
        "item_backtest_cache": (("uploaded_at", "TEXT"),),
        "volume_backtest_cache": (("uploaded_at", "TEXT"),),
        "menu_item_variants": (
            ("shared_pos_rule_tombstoned", "INTEGER NOT NULL DEFAULT 0"),
            ("shared_pos_prior_is_active", "INTEGER"),
        ),
    }
    for table, columns in additive_columns.items():
        existing = {
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, column_type in columns:
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    app_user_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(app_users)").fetchall()
    }
    if "user_id" in app_user_columns and "employee_id" not in app_user_columns:
        conn.execute("ALTER TABLE app_users RENAME TO app_users_legacy")
        # Re-run the canonical file so it, rather than this module, owns the
        # replacement table definition.
        conn.executescript(schema_sql)
        legacy_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(app_users_legacy)").fetchall()
        }
        if {"user_id", "name"}.issubset(legacy_columns):
            active_expression = "is_active" if "is_active" in legacy_columns else "1"
            conn.execute(
                f"""
                INSERT OR IGNORE INTO app_users (employee_id, name, is_active)
                SELECT CAST(user_id AS TEXT), name,
                       COALESCE({active_expression}, 1)
                FROM app_users_legacy
                """
            )
        conn.execute("DROP TABLE app_users_legacy")

    if conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO app_users (name, employee_id, is_active) VALUES ('Owner', '0001', 1)"
        )
    conn.commit()


def _connect(target_db):
    conn = sqlite3.connect(str(target_db), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def get_db_connection(db_url=None):
    """
    Create SQLite database connection.
    Arguments like host, port, user, password are ignored but kept for signature compatibility if needed.
    """
    try:
        # Use env var or default path
        if db_url is None:
            from src.core.db.control import control_runtime_enabled

            if control_runtime_enabled():
                return None, "Explicit restaurant profile database is required"
        target_db = db_url or os.environ.get("DB_URL") or DB_PATH
        
        print(f"Connecting to database at: {os.path.abspath(target_db)}")
        
        conn = _connect(target_db)
        
        return conn, "Connected to SQLite"
        
    except Exception as e:
        return None, str(e)


def get_profile_connection(profile, *, apply_schema: bool = True):
    """Open and verify one immutable RestaurantProfile."""
    from src.core.db.control import app_data_root
    from src.core.profiles import (
        CleanProfileRebuildRequired,
        ProfileMismatch,
        registered_profile_rebuild_status,
        validate_restaurant_id,
    )

    restaurant_id = validate_restaurant_id(profile.restaurant_id)
    root = app_data_root().resolve()
    path = Path(profile.database_path).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ProfileMismatch(f"Profile path is outside app-data root: {path}") from exc
    rebuild_status = registered_profile_rebuild_status(restaurant_id, path)
    if rebuild_status == "required":
        raise CleanProfileRebuildRequired(
            f"Restaurant profile {restaurant_id} requires an archived clean rebuild"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(path)
    try:
        if apply_schema:
            apply_analytics_schema(conn)
        row = conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            raise ProfileMismatch(f"Profile database is not bound: {restaurant_id}")
        if str(row[0]) != restaurant_id:
            raise ProfileMismatch(
                f"Profile database is bound to {row[0]}, not {restaurant_id}"
            )
        return conn, "Connected to restaurant profile"
    except Exception:
        conn.close()
        raise
