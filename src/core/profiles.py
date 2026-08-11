"""Restaurant profile registry, safe paths, binding, and selection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.core.db.control import (
    app_data_root,
    ensure_control_schema,
    existing_analytics_db_path,
    get_control_connection,
)


ALL_STORES_TOKEN = "__all__"
PROFILE_SCHEMA_VERSION = 1
SHARED_POS_CATALOG_CAPABILITY = "global_menu_shared_pos_catalog_v1"


class ProfileError(RuntimeError):
    code = "profile_error"


class ProfileSelectionRequired(ProfileError):
    code = "restaurant_selection_required"


class ProfileMismatch(ProfileError):
    code = "restaurant_profile_mismatch"


class ProfileBindingConfirmationRequired(ProfileError):
    code = "restaurant_binding_confirmation_required"


class MixedRestaurantDatabase(ProfileError):
    code = "mixed_restaurant_database"


class SingleRestaurantRequired(ProfileError):
    """Raised when All Stores is active but the operation owns exactly one store."""

    code = "single_restaurant_required"


class AllStoresUnavailable(ProfileError):
    code = "all_stores_unavailable"


class CleanProfileRebuildRequired(ProfileError):
    code = "clean_profile_rebuild_required"


class ProfileRegistryRefreshError(ProfileError):
    def __init__(self, message: str, *, code: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class RestaurantProfile:
    restaurant_id: str
    display_name: str
    timezone: str
    database_path: str
    authorization_state: str
    local_address: Optional[str] = None
    is_bound: bool = False
    menu_group_id: Optional[str] = None
    menu_capabilities: tuple[str, ...] = ()
    clean_rebuild_status: Optional[str] = None
    last_archive_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_restaurant_id(value: Optional[str]) -> str:
    restaurant_id = str(value or "").strip()
    if not restaurant_id or restaurant_id == ALL_STORES_TOKEN:
        raise ProfileSelectionRequired("Select one physical restaurant")
    return restaurant_id


def _canonical_under_root(path: Path) -> Path:
    root = app_data_root().resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProfileError(f"Profile database path is outside app-data root: {resolved}") from exc
    return resolved


def _restaurant_digest(restaurant_id: str) -> str:
    return hashlib.sha256(restaurant_id.encode("utf-8")).hexdigest()[:32]


def profile_path_for(restaurant_id: str) -> Path:
    restaurant_id = validate_restaurant_id(restaurant_id)
    return _canonical_under_root(
        app_data_root() / "profiles" / f"restaurant-{_restaurant_digest(restaurant_id)}.db"
    )


def profile_data_dir(restaurant_id: str) -> Path:
    """Writable per-profile directory for derived exports (never shared).

    Derived files such as the weather CSV are rebuilt from one profile's rows,
    so they must not collide the way the old app-global
    `data/weather_history.csv` and `menu_bootstrap_last_push_hash.txt` did.
    """
    restaurant_id = validate_restaurant_id(restaurant_id)
    path = _canonical_under_root(
        app_data_root() / "data" / f"restaurant-{_restaurant_digest(restaurant_id)}"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _identity_for_path(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restaurant_profile_identity'"
        ).fetchone()
        if not exists:
            return None
        row = conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def _profile_from_row(row: sqlite3.Row) -> RestaurantProfile:
    path = _canonical_under_root(Path(row["database_path"]))
    clean_rebuild_status = (
        str(row["clean_rebuild_status"]).strip()
        if row["clean_rebuild_status"]
        else None
    )
    # A revision-1.6 file awaiting the controlled revision-1.7 rebuild must
    # not even be opened for identity/schema discovery. Its registered path
    # and existence are enough for the reset route to capture it safely.
    identity = None if clean_rebuild_status == "required" else _identity_for_path(path)
    try:
        raw_capabilities = json.loads(row["menu_capabilities"] or "[]")
    except (TypeError, ValueError):
        raw_capabilities = []
    capabilities = tuple(
        sorted({str(value).strip() for value in raw_capabilities if str(value).strip()})
    ) if isinstance(raw_capabilities, list) else ()
    return RestaurantProfile(
        restaurant_id=str(row["restaurant_id"]),
        display_name=str(row["display_name"]),
        timezone=str(row["timezone"]),
        database_path=str(path),
        authorization_state=str(row["authorization_state"]),
        local_address=row["local_address"],
        is_bound=(
            path.is_file()
            if clean_rebuild_status == "required"
            else identity == str(row["restaurant_id"])
        ),
        menu_group_id=str(row["menu_group_id"]).strip() if row["menu_group_id"] else None,
        menu_capabilities=capabilities,
        clean_rebuild_status=clean_rebuild_status,
        last_archive_path=row["last_archive_path"],
    )


def list_profiles() -> List[RestaurantProfile]:
    ensure_control_schema()
    conn = get_control_connection()
    try:
        rows = conn.execute(
            """
            SELECT restaurant_id, display_name, timezone, database_path,
                   authorization_state, local_address, menu_group_id, menu_capabilities,
                   clean_rebuild_status, last_archive_path
            FROM restaurant_profiles
            ORDER BY display_name, restaurant_id
            """
        ).fetchall()
        return [_profile_from_row(row) for row in rows]
    finally:
        conn.close()


def get_profile(restaurant_id: str, *, require_authorized: bool = False) -> RestaurantProfile:
    restaurant_id = validate_restaurant_id(restaurant_id)
    ensure_control_schema()
    conn = get_control_connection()
    try:
        row = conn.execute(
            """
            SELECT restaurant_id, display_name, timezone, database_path,
                   authorization_state, local_address, menu_group_id, menu_capabilities,
                   clean_rebuild_status, last_archive_path
            FROM restaurant_profiles WHERE restaurant_id=?
            """,
            (restaurant_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ProfileError(f"Unknown restaurant profile: {restaurant_id}")
    profile = _profile_from_row(row)
    if require_authorized and profile.authorization_state != "authorized":
        raise ProfileError(f"Restaurant profile is not authorized: {restaurant_id}")
    return profile


def mark_profile_clean_rebuild(
    restaurant_id: str,
    status: str,
    *,
    archive_path: Optional[str] = None,
) -> None:
    """Persist the operator rebuild lifecycle without opening the profile DB."""
    restaurant_id = validate_restaurant_id(restaurant_id)
    if status not in {"required", "rebuilding", "complete"}:
        raise ValueError(f"Invalid clean rebuild status: {status}")
    ensure_control_schema()
    conn = get_control_connection()
    try:
        updated = conn.execute(
            """
            UPDATE restaurant_profiles
            SET clean_rebuild_status=?,
                last_archive_path=COALESCE(?, last_archive_path),
                updated_at=CURRENT_TIMESTAMP
            WHERE restaurant_id=?
            """,
            (status, archive_path, restaurant_id),
        )
        if updated.rowcount != 1:
            raise ProfileError(f"Unknown restaurant profile: {restaurant_id}")
        conn.commit()
    finally:
        conn.close()


def registered_profile_rebuild_status(
    restaurant_id: str, database_path: str | Path
) -> Optional[str]:
    """Read a rebuild marker for an exact registry path without opening SQLite."""
    restaurant_id = validate_restaurant_id(restaurant_id)
    ensure_control_schema()
    conn = get_control_connection()
    try:
        row = conn.execute(
            """
            SELECT database_path, clean_rebuild_status
            FROM restaurant_profiles WHERE restaurant_id=?
            """,
            (restaurant_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    if Path(str(row[0])).expanduser().resolve() != Path(database_path).expanduser().resolve():
        return None
    return str(row[1]) if row[1] else None


def profile_rebuild_status_for_connection(conn) -> Optional[str]:
    """Return the control-plane rebuild marker for this exact registered file."""
    try:
        identity = conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()
        if not identity:
            return None
        database_rows = conn.execute("PRAGMA database_list").fetchall()
        main_path = next(
            (str(row[2]) for row in database_rows if str(row[1]) == "main"), ""
        )
        if not main_path:
            return None
        return registered_profile_rebuild_status(str(identity[0]), main_path)
    except Exception:
        return None


def complete_profile_rebuild_for_connection(conn) -> bool:
    """Mark a registered fresh profile complete after its Phase-F sync passes."""
    if profile_rebuild_status_for_connection(conn) != "rebuilding":
        return False
    identity = conn.execute(
        "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
    ).fetchone()
    if not identity:
        return False
    mark_profile_clean_rebuild(str(identity[0]), "complete")
    return True


def selected_selection() -> Dict[str, Optional[str]]:
    """The persisted selection: All Stores, one restaurant, or nothing yet."""
    ensure_control_schema()
    conn = get_control_connection()
    try:
        row = conn.execute(
            "SELECT selection_mode, restaurant_id FROM app_selection WHERE singleton_id=1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"selection_mode": None, "restaurant_id": None}
    return {
        "selection_mode": str(row["selection_mode"]),
        "restaurant_id": row["restaurant_id"],
    }


def selected_profile(*, require_authorized: bool = False) -> RestaurantProfile:
    selection = selected_selection()
    if selection["selection_mode"] == "all":
        raise SingleRestaurantRequired("All Stores is selected; choose one physical restaurant")
    restaurant_id = selection["restaurant_id"]
    if not restaurant_id:
        raise ProfileSelectionRequired("Select one physical restaurant")
    return get_profile(str(restaurant_id), require_authorized=require_authorized)


def federation_profiles() -> List[RestaurantProfile]:
    """The All Stores membership: authorized profiles with an initialized database.

    Sorted by restaurant ID so one press of Sync DB, one federated read, and one
    tie-break comparison all see the same deterministic order.
    """
    return sorted(
        (
            profile
            for profile in list_profiles()
            if profile.is_bound and profile.authorization_state == "authorized"
        ),
        key=lambda profile: profile.restaurant_id,
    )


def excluded_federation_profiles() -> List[RestaurantProfile]:
    """Bound profiles kept out of All Stores, reported so the gap is never silent."""
    return sorted(
        (
            profile
            for profile in list_profiles()
            if profile.is_bound and profile.authorization_state != "authorized"
        ),
        key=lambda profile: profile.restaurant_id,
    )


def all_stores_available() -> bool:
    return len(federation_profiles()) >= 2


def select_all_stores() -> None:
    """Persist the All Stores selection. `__all__` never reaches restaurant_id."""
    if not all_stores_available():
        raise AllStoresUnavailable(
            "All Stores needs at least two authorized restaurants with initialized databases"
        )
    ensure_control_schema()
    conn = get_control_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_selection (singleton_id, selection_mode, restaurant_id, updated_at)
            VALUES (1, 'all', NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(singleton_id) DO UPDATE SET
                selection_mode='all', restaurant_id=NULL, updated_at=CURRENT_TIMESTAMP
            """
        )
        conn.commit()
    finally:
        conn.close()


def mark_profile_unauthorized(restaurant_id: str) -> None:
    """Fail closed after the central server rejects a registered scope.

    The profile row and database are deliberately retained for offline reads.
    A later successful allowed-restaurant refresh can authorize it again.
    """
    restaurant_id = validate_restaurant_id(restaurant_id)
    ensure_control_schema()
    conn = get_control_connection()
    try:
        conn.execute(
            """
            UPDATE restaurant_profiles
            SET authorization_state='unauthorized', menu_group_id=NULL,
                menu_capabilities='[]', updated_at=CURRENT_TIMESTAMP
            WHERE restaurant_id=?
            """,
            (restaurant_id,),
        )
        conn.commit()
    finally:
        conn.close()


def registered_profile_authorization(
    restaurant_id: str, analytics_conn=None
) -> Optional[str]:
    """Return authorization only when this exact file is in the registry.

    A standalone support/test database may deliberately reuse a restaurant ID;
    it must not inherit authorization state from some other registered path.
    """
    restaurant_id = validate_restaurant_id(restaurant_id)
    ensure_control_schema()
    conn = get_control_connection()
    try:
        row = conn.execute(
            """
            SELECT authorization_state, database_path
            FROM restaurant_profiles WHERE restaurant_id=?
            """,
            (restaurant_id,),
        ).fetchone()
        if not row:
            return None
        if analytics_conn is None:
            return str(row[0])
        database_rows = analytics_conn.execute("PRAGMA database_list").fetchall()
        main_path = next(
            (str(database_row[2]) for database_row in database_rows if database_row[1] == "main"),
            "",
        )
        if not main_path:
            return None
        if Path(main_path).expanduser().resolve() != Path(row[1]).expanduser().resolve():
            return None
        return str(row[0])
    finally:
        conn.close()


def _business_restaurant_ids(path: Path) -> Sequence[str]:
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restaurants'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT DISTINCT TRIM(petpooja_restid) FROM restaurants WHERE TRIM(COALESCE(petpooja_restid,'')) <> ''"
        ).fetchall()
        found = {str(row[0]) for row in rows}
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='orders'"
        ).fetchone():
            order_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(orders)").fetchall()
            }
            if "raw_event" in order_columns:
                for (raw_event,) in conn.execute(
                    "SELECT raw_event FROM orders WHERE raw_event IS NOT NULL"
                ):
                    try:
                        payload = json.loads(raw_event) if isinstance(raw_event, str) else raw_event
                        raw = (payload or {}).get("raw_payload", payload or {})
                        rid = (((raw.get("properties") or {}).get("Restaurant") or {}).get("restID"))
                        if rid:
                            found.add(str(rid).strip())
                    except Exception:
                        continue
        return sorted(found)
    finally:
        conn.close()


def _has_unidentified_profile_data(path: Path) -> bool:
    """Whether an unbound database contains data that cannot be scoped safely.

    Legacy global config and the singleton app user do not identify a
    restaurant, so a config-only first-run database may still be claimed. Any
    other populated analytics table requires a recoverable restaurant ID; an
    arbitrary dropdown choice must never become its identity.
    """
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path))
    try:
        ignored = {
            "app_users",
            "restaurant_profile_identity",
            "sqlite_sequence",
            "system_config",
        }
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table_name,) in rows:
            table = str(table_name)
            if table in ignored:
                continue
            quoted = table.replace('"', '""')
            if conn.execute(f'SELECT 1 FROM "{quoted}" LIMIT 1').fetchone():
                return True
        return False
    finally:
        conn.close()


def _write_identity(path: Path, restaurant_id: str) -> None:
    from src.core.db.connection import apply_analytics_schema

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    try:
        apply_analytics_schema(conn)
        existing = conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()
        if existing and str(existing[0]) != restaurant_id:
            raise ProfileMismatch(
                f"Database is bound to {existing[0]}, not {restaurant_id}"
            )
        conn.execute(
            """
            INSERT INTO restaurant_profile_identity
                (singleton_id, restaurant_id, bound_at, profile_schema_version)
            VALUES (1, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(singleton_id) DO NOTHING
            """,
            (restaurant_id, PROFILE_SCHEMA_VERSION),
        )
        conn.commit()
    finally:
        conn.close()


def _persist_restaurant_selection(restaurant_id: str) -> None:
    conn = get_control_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_selection (singleton_id, selection_mode, restaurant_id, updated_at)
            VALUES (1, 'restaurant', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(singleton_id) DO UPDATE SET
                selection_mode='restaurant', restaurant_id=excluded.restaurant_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (restaurant_id,),
        )
        conn.commit()
    finally:
        conn.close()


def bind_and_select_profile(
    restaurant_id: str, *, confirm_existing_binding: bool = False
) -> RestaurantProfile:
    profile = get_profile(restaurant_id)
    # A previously bound profile remains selectable for offline read access
    # after it disappears from the current credential's allowed list. Never
    # initialize a new profile for an unauthorized restaurant.
    path = _canonical_under_root(Path(profile.database_path))
    existing_path = _canonical_under_root(existing_analytics_db_path())
    if profile.clean_rebuild_status == "required" and path.is_file():
        # Keep the old file opaque. Selection is allowed so the operator can
        # invoke the selected-profile reset endpoint, but every DB opener will
        # refuse it until reset_database archives and recreates it.
        _persist_restaurant_selection(profile.restaurant_id)
        return get_profile(profile.restaurant_id)
    if profile.authorization_state != "authorized" and not profile.is_bound:
        # First-upgrade recovery: a populated legacy database may belong to a
        # restaurant that is absent from the current grant list. It can be bound
        # in place for offline reads, but an empty/new unauthorized profile must
        # never be initialized.
        local_ids = _business_restaurant_ids(path)
        if path != existing_path or local_ids != [profile.restaurant_id]:
            raise ProfileError(f"Restaurant profile is not authorized: {restaurant_id}")
    existing_ids = _business_restaurant_ids(existing_path) if not _identity_for_path(existing_path) else []
    # An unbound legacy database containing more than one restaurant blocks the
    # entire first-run activation. Letting an unrelated restaurant claim a new
    # profile would hide the mixed file and bypass the required support gate.
    if len(existing_ids) > 1:
        raise MixedRestaurantDatabase(
            f"Existing database contains multiple restaurants: {', '.join(existing_ids)}"
        )
    if (
        not _identity_for_path(existing_path)
        and not existing_ids
        and _has_unidentified_profile_data(existing_path)
    ):
        raise ProfileMismatch(
            "Existing database contains analytics data but no recoverable restaurant identity"
        )
    if path != existing_path and not _identity_for_path(existing_path) and not _business_restaurant_ids(existing_path):
        control = get_control_connection()
        try:
            claimed = control.execute(
                "SELECT restaurant_id FROM restaurant_profiles WHERE database_path=? AND restaurant_id<>?",
                (str(existing_path), profile.restaurant_id),
            ).fetchone()
            if claimed is None:
                control.execute(
                    "UPDATE restaurant_profiles SET database_path=?, updated_at=CURRENT_TIMESTAMP WHERE restaurant_id=?",
                    (str(existing_path), profile.restaurant_id),
                )
                control.commit()
                profile = get_profile(profile.restaurant_id, require_authorized=True)
                path = existing_path
        finally:
            control.close()
    identity = _identity_for_path(path)
    if identity and identity != profile.restaurant_id:
        raise ProfileMismatch(f"Database is bound to {identity}, not {profile.restaurant_id}")
    if not identity:
        ids = _business_restaurant_ids(path)
        if len(ids) > 1:
            raise MixedRestaurantDatabase(
                f"Existing database contains multiple restaurants: {', '.join(ids)}"
            )
        if ids and ids[0] != profile.restaurant_id:
            raise ProfileMismatch(
                f"Existing database contains {ids[0]}, not {profile.restaurant_id}"
            )
        if not ids and _has_unidentified_profile_data(path):
            raise ProfileMismatch(
                "Existing database contains analytics data but no recoverable restaurant identity"
            )
        if ids and not confirm_existing_binding:
            raise ProfileBindingConfirmationRequired(
                f"Confirm binding the existing database to {profile.display_name}"
            )
        _write_identity(path, profile.restaurant_id)
        if profile.clean_rebuild_status == "required":
            # No old file existed, so no archive is needed; the fresh schema
            # still owes the ordered catalog/order/assignment/history/
            # observation hydration when shared-POS is advertised.
            mark_profile_clean_rebuild(
                profile.restaurant_id,
                (
                    "rebuilding"
                    if SHARED_POS_CATALOG_CAPABILITY in profile.menu_capabilities
                    else "complete"
                ),
            )

    _persist_restaurant_selection(profile.restaurant_id)
    return get_profile(profile.restaurant_id)


def upsert_allowed_restaurants(restaurants: Sequence[Dict[str, Any]]) -> List[RestaurantProfile]:
    ensure_control_schema()
    conn = get_control_connection()
    try:
        # The current response is authoritative. Clear capability metadata for
        # every cached row before applying it so omission/removal cannot leave a
        # stale global-menu enablement behind.
        conn.execute(
            """
            UPDATE restaurant_profiles
            SET authorization_state='unauthorized', menu_group_id=NULL,
                menu_capabilities='[]', updated_at=CURRENT_TIMESTAMP
            """
        )
        existing_path = existing_analytics_db_path().resolve()
        registered_existing = conn.execute(
            "SELECT restaurant_id FROM restaurant_profiles WHERE database_path=?",
            (str(_canonical_under_root(existing_path)),),
        ).fetchone()
        if registered_existing is not None:
            # The control registry already owns this path. Do not reopen a
            # potentially revision-1.6 profile merely to rediscover identity
            # while applying the capability response that may require reset.
            existing_identity = str(registered_existing[0])
            existing_ids = [existing_identity]
        else:
            existing_identity = _identity_for_path(existing_path)
            existing_ids = (
                _business_restaurant_ids(existing_path)
                if existing_path.exists()
                else []
            )
        for raw in restaurants:
            rid = validate_restaurant_id(raw.get("restaurant_id"))
            display_name = str(raw.get("display_name") or rid)
            timezone = str(raw.get("timezone") or "Asia/Kolkata")
            menu_group_id = str(raw.get("menu_group_id") or "").strip() or None
            menu_capabilities = sorted(
                {
                    str(value).strip()
                    for value in (raw.get("menu_capabilities") or [])
                    if str(value).strip()
                }
            )
            clean_rebuild_status = (
                "required"
                if SHARED_POS_CATALOG_CAPABILITY in menu_capabilities
                else None
            )
            current = conn.execute(
                "SELECT database_path FROM restaurant_profiles WHERE restaurant_id=?", (rid,)
            ).fetchone()
            if current:
                db_path = _canonical_under_root(Path(current[0]))
            elif existing_identity == rid or (not existing_identity and existing_ids == [rid]):
                db_path = _canonical_under_root(existing_path)
            else:
                db_path = profile_path_for(rid)
            conn.execute(
                """
                INSERT INTO restaurant_profiles (
                    restaurant_id, display_name, timezone, database_path,
                    authorization_state, last_listed_at, menu_group_id,
                    menu_capabilities, clean_rebuild_status, updated_at
                ) VALUES (?, ?, ?, ?, 'authorized', CURRENT_TIMESTAMP, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(restaurant_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    timezone=excluded.timezone,
                    authorization_state='authorized',
                    menu_group_id=excluded.menu_group_id,
                    menu_capabilities=excluded.menu_capabilities,
                    clean_rebuild_status=CASE
                        WHEN restaurant_profiles.clean_rebuild_status IS NULL
                         AND excluded.clean_rebuild_status='required'
                        THEN 'required'
                        ELSE restaurant_profiles.clean_rebuild_status
                    END,
                    last_listed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    rid,
                    display_name,
                    timezone,
                    str(db_path),
                    menu_group_id,
                    json.dumps(menu_capabilities, separators=(",", ":")),
                    clean_rebuild_status,
                ),
            )

        # Preserve a first-upgrade legacy database even when its one restaurant
        # is no longer in the credential's grant list. This row is deliberately
        # unauthorized: it exists only so the user can confirm an in-place bind
        # and read historical analytics offline.
        local_restaurant_id = existing_identity
        if not local_restaurant_id and len(existing_ids) == 1:
            local_restaurant_id = existing_ids[0]
        if local_restaurant_id:
            registered = conn.execute(
                "SELECT 1 FROM restaurant_profiles WHERE restaurant_id=?",
                (local_restaurant_id,),
            ).fetchone()
            if registered is None:
                display_name = local_restaurant_id
                try:
                    analytics = sqlite3.connect(str(existing_path))
                    try:
                        name_row = analytics.execute(
                            """
                            SELECT name FROM restaurants
                            WHERE TRIM(petpooja_restid)=? AND TRIM(COALESCE(name,'')) <> ''
                            LIMIT 1
                            """,
                            (local_restaurant_id,),
                        ).fetchone()
                        if name_row:
                            display_name = str(name_row[0])
                    finally:
                        analytics.close()
                except Exception:
                    pass
                conn.execute(
                    """
                    INSERT INTO restaurant_profiles (
                        restaurant_id, display_name, timezone, database_path,
                        authorization_state, updated_at
                    ) VALUES (?, ?, 'Asia/Kolkata', ?, 'unauthorized', CURRENT_TIMESTAMP)
                    """,
                    (local_restaurant_id, display_name, str(_canonical_under_root(existing_path))),
                )
        conn.commit()
    finally:
        conn.close()
    return list_profiles()


def refresh_allowed_restaurants_from_server() -> List[RestaurantProfile]:
    """Refresh authorization/capabilities without touching any profile database."""
    from src.core.central_api import error_from_response, unscoped_analytics_headers
    from src.core.db.control import get_global_config
    from utils.api_client import normalize_integration_orders_base_url

    config = get_global_config(("integration_orders_url", "integration_orders_key"))
    base_url = normalize_integration_orders_base_url(
        config.get("integration_orders_url") or ""
    )
    api_key = config.get("integration_orders_key") or ""
    if not base_url or not api_key:
        raise ProfileRegistryRefreshError(
            "Configure the Orders Integration URL and API key first",
            code="restaurant_list_not_configured",
            http_status=409,
        )
    try:
        import requests

        response = requests.get(
            f"{base_url}/restaurants/",
            headers=unscoped_analytics_headers(api_key),
            timeout=30,
        )
    except Exception as exc:
        raise ProfileRegistryRefreshError(
            str(exc),
            code="restaurant_list_transport_error",
            http_status=502,
        ) from exc
    if response.status_code >= 400:
        error = error_from_response(response)
        raise ProfileRegistryRefreshError(
            error.message,
            code=error.code,
            http_status=response.status_code,
        )
    try:
        from src.core.analytics_stream_contract import parse_allowed_restaurants

        restaurants = parse_allowed_restaurants(response.json())
    except Exception as exc:
        raise ProfileRegistryRefreshError(
            str(exc),
            code=getattr(exc, "code", "restaurant_list_response_invalid"),
            http_status=502,
        ) from exc
    return upsert_allowed_restaurants(restaurants)
