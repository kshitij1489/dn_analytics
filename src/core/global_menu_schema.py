"""Global-menu projection state, validation, and the single capability gate.

The projection is deliberately dormant by default. A profile can enter
``global_menu_v1`` only when the current server-managed registry row authorizes
the selected physical restaurant and advertises the matching menu group and
capability. SQLite never enables global writes by itself.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


GLOBAL_MENU_CAPABILITY = "global_menu_v1"
GLOBAL_MENU_RESOLUTION_CAPABILITY = "global_menu_resolution_v1"
GLOBAL_MENU_AGGREGATION_CAPABILITY = "global_menu_aggregation_v1"
GLOBAL_MENU_MUTATION_CAPABILITY = "global_menu_mutations_v1"
GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY = "global_menu_shared_pos_catalog_v1"
GLOBAL_MENU_SCHEMA_VERSION = 1
LEGACY_MENU_MODE = "legacy_restaurant_v1"
GLOBAL_MENU_MODE = "global_menu_v1"

GLOBAL_MENU_TABLES: Mapping[str, Tuple[str, ...]] = {
    "global_menu_state": (
        "singleton_id",
        "mode",
        "menu_group_id",
        "capability_schema_version",
        "catalog_revision",
        "mutation_revision",
        "snapshot_cursor",
        "event_cursor",
        "assignment_cursor",
        "history_cursor",
        "bootstrap_status",
        "coverage_linked",
        "coverage_total",
        "last_error",
    ),
    "global_menu_items": (
        "global_menu_item_id",
        "menu_group_id",
        "canonical_name",
        "canonical_type",
        "server_revision",
    ),
    "global_variants": (
        "global_variant_id",
        "menu_group_id",
        "canonical_name",
        "server_revision",
    ),
    "menu_item_global_links": (
        "local_menu_item_id",
        "global_menu_item_id",
        "server_revision",
    ),
    "variant_global_links": (
        "local_variant_id",
        "global_variant_id",
        "server_revision",
    ),
    "global_menu_redirects": (
        "redirect_id",
        "menu_group_id",
        "entity_type",
        "server_revision",
    ),
    "global_menu_mapping_rules": (
        "rule_id",
        "menu_group_id",
        "locator_scope",
        "restaurant_id",
        "locator_kind",
        "normalized_locator",
        "price",
        "server_revision",
    ),
    "global_menu_sync_quarantine": (
        "quarantine_id",
        "payload_key",
        "stream",
        "payload",
        "error_code",
        "error_message",
    ),
    "global_menu_events": (
        "event_id",
        "menu_group_id",
        "mutation_id",
        "event_type",
        "catalog_revision",
        "payload",
    ),
    "global_menu_history": (
        "history_id",
        "menu_group_id",
        "source_event_id",
        "source_kind",
        "event_type",
        "origin_restaurant_id",
        "actor",
        "attribution",
        "occurred_at",
        "server_ingested_at",
        "source",
        "target",
        "mutation_id",
        "is_undoable",
        "detail",
        "cached_at",
    ),
}


class GlobalMenuSchemaError(RuntimeError):
    code = "global_menu_schema_invalid"


class GlobalMenuCapabilityError(RuntimeError):
    code = "global_menu_capability_required"


@dataclass(frozen=True)
class GlobalMenuCapabilityStatus:
    mode: str
    restaurant_id: Optional[str]
    menu_group_id: Optional[str]
    schema_version: int
    server_advertised: bool
    authorized: bool
    selected: bool
    reason: str
    catalog_revision: int = 0
    mutation_revision: int = 0
    snapshot_cursor: Optional[str] = None
    event_cursor: Optional[str] = None
    assignment_cursor: Optional[str] = None
    history_cursor: Optional[str] = None
    bootstrap_status: str = "not_started"
    coverage_linked: int = 0
    coverage_total: int = 0
    quarantine_count: int = 0
    capabilities: Tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.mode == GLOBAL_MENU_MODE

    @property
    def coverage_complete(self) -> bool:
        return (
            self.bootstrap_status == "complete"
            and self.coverage_linked == self.coverage_total
            and self.quarantine_count == 0
        )

    @property
    def aggregation_advertised(self) -> bool:
        return GLOBAL_MENU_AGGREGATION_CAPABILITY in self.capabilities

    @property
    def resolution_advertised(self) -> bool:
        return GLOBAL_MENU_RESOLUTION_CAPABILITY in self.capabilities

    @property
    def mutation_advertised(self) -> bool:
        return GLOBAL_MENU_MUTATION_CAPABILITY in self.capabilities

    @property
    def shared_pos_catalog_advertised(self) -> bool:
        return GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY in self.capabilities

    @property
    def shared_pos_catalog_ready(self) -> bool:
        return (
            self.active
            and self.shared_pos_catalog_advertised
            and self.bootstrap_status == "complete"
            and self.quarantine_count == 0
        )

    @property
    def mutation_ready(self) -> bool:
        return self.active and self.mutation_advertised and self.coverage_complete

    @property
    def resolution_ready(self) -> bool:
        return (
            self.active
            and self.resolution_advertised
            and self.bootstrap_status == "complete"
            and self.quarantine_count == 0
        )

    @property
    def aggregation_ready(self) -> bool:
        return self.active and self.aggregation_advertised and self.coverage_complete

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result.update(
            {
                "active": self.active,
                "coverage_complete": self.coverage_complete,
                "resolution_advertised": self.resolution_advertised,
                "aggregation_advertised": self.aggregation_advertised,
                "mutation_advertised": self.mutation_advertised,
                "shared_pos_catalog_advertised": self.shared_pos_catalog_advertised,
                "resolution_ready": self.resolution_ready,
                "mutation_ready": self.mutation_ready,
                "aggregation_ready": self.aggregation_ready,
                "shared_pos_catalog_ready": self.shared_pos_catalog_ready,
            }
        )
        return result


def _table_columns(conn, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_global_menu_schema(conn) -> None:
    """Validate canonical DDL and initialize the projection singleton.

    ``apply_analytics_schema`` owns table creation by executing
    ``database/schema_sqlite.sql`` first. Keeping this helper DDL-free prevents
    routers and startup code from becoming competing schema owners.
    """
    for table, required in GLOBAL_MENU_TABLES.items():
        columns = _table_columns(conn, table)
        if not columns:
            raise GlobalMenuSchemaError(f"Missing global menu projection table: {table}")
        missing = sorted(set(required) - columns)
        if missing:
            raise GlobalMenuSchemaError(
                f"Global menu projection table {table} is missing: {', '.join(missing)}"
            )
    if conn.execute(
        "SELECT 1 FROM global_menu_state WHERE singleton_id=1"
    ).fetchone() is None:
        conn.execute(
            """
            INSERT INTO global_menu_state (
                singleton_id, mode, capability_schema_version, bootstrap_status
            ) VALUES (1, ?, ?, 'not_started')
            """,
            (LEGACY_MENU_MODE, GLOBAL_MENU_SCHEMA_VERSION),
        )


def _state_row(conn) -> Dict[str, Any]:
    ensure_global_menu_schema(conn)
    row = conn.execute(
        "SELECT * FROM global_menu_state WHERE singleton_id=1"
    ).fetchone()
    return dict(row) if row is not None else {}


def _parse_capabilities(raw: Any) -> Tuple[str, ...]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = []
    if not isinstance(raw, (list, tuple, set)):
        return ()
    return tuple(sorted({str(value).strip() for value in raw if str(value).strip()}))


def _connection_main_path(conn) -> Optional[Path]:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        raw = next((row[2] for row in rows if str(row[1]) == "main"), None)
        return Path(str(raw)).expanduser().resolve() if raw else None
    except Exception:
        return None


def _identity_restaurant_id(conn) -> Optional[str]:
    try:
        row = conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()
    except Exception:
        return None
    value = str(row[0] or "").strip() if row else ""
    return value or None


def _runtime_registry_entry(conn, restaurant_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not restaurant_id:
        return None
    try:
        from src.core.db.control import ensure_control_schema, get_control_connection

        ensure_control_schema()
        control = get_control_connection()
        try:
            row = control.execute(
                """
                SELECT restaurant_id, database_path, authorization_state,
                       menu_group_id, menu_capabilities
                FROM restaurant_profiles WHERE restaurant_id=?
                """,
                (restaurant_id,),
            ).fetchone()
            selection = control.execute(
                "SELECT selection_mode, restaurant_id FROM app_selection WHERE singleton_id=1"
            ).fetchone()
        finally:
            control.close()
    except Exception:
        return None
    if row is None:
        return None
    main_path = _connection_main_path(conn)
    try:
        registered_path = Path(str(row[1])).expanduser().resolve()
    except Exception:
        return None
    if main_path is None or main_path != registered_path:
        return None
    return {
        "restaurant_id": str(row[0]),
        "authorization_state": str(row[2]),
        "menu_group_id": row[3],
        "menu_capabilities": row[4],
        "selected": bool(
            selection
            and str(selection[0]) == "restaurant"
            and str(selection[1] or "") == restaurant_id
        ),
        "selection_mode": str(selection[0]) if selection else None,
    }


def resolve_global_menu_capability(
    conn,
    *,
    registry_entry: Optional[Mapping[str, Any]] = None,
    allow_federated_read: bool = False,
    allow_profile_sync: bool = False,
) -> GlobalMenuCapabilityStatus:
    """Resolve the only global-menu enablement decision used by backend code.

    ``registry_entry`` exists for focused tests and must include the same fields
    as a control-database row. Runtime callers omit it. ``allow_profile_sync``
    is reserved for background/All Stores coordinators that already captured an
    authorized physical profile; it never authorizes a user mutation.
    """
    restaurant_id = _identity_restaurant_id(conn)
    try:
        state = _state_row(conn)
    except GlobalMenuSchemaError:
        # Focused compatibility schemas and pre-upgrade standalone databases
        # must retain exact legacy behavior. Runtime profile opens execute the
        # canonical additive upgrade before reaching this resolver.
        return GlobalMenuCapabilityStatus(
            mode=LEGACY_MENU_MODE,
            restaurant_id=restaurant_id,
            menu_group_id=None,
            schema_version=0,
            server_advertised=False,
            authorized=False,
            selected=False,
            reason="projection_schema_missing",
        )
    entry = dict(registry_entry) if registry_entry is not None else _runtime_registry_entry(
        conn, restaurant_id
    )
    group_id = str((entry or {}).get("menu_group_id") or "").strip() or None
    projection_group_id = str(state.get("menu_group_id") or "").strip() or None
    capabilities = _parse_capabilities((entry or {}).get("menu_capabilities"))
    authorized = (entry or {}).get("authorization_state") == "authorized"
    selected = bool((entry or {}).get("selected")) or bool(
        allow_federated_read and (entry or {}).get("selection_mode") == "all"
    ) or bool(allow_profile_sync and entry)
    server_advertised = GLOBAL_MENU_CAPABILITY in capabilities
    schema_version = int(state.get("capability_schema_version") or 0)
    reasons = []
    if not restaurant_id:
        reasons.append("profile_identity_missing")
    if not entry:
        reasons.append("registry_entry_missing")
    if not authorized:
        reasons.append("restaurant_not_authorized")
    if not selected:
        reasons.append("restaurant_not_selected")
    if not group_id:
        reasons.append("menu_group_missing")
    if projection_group_id and group_id and projection_group_id != group_id:
        # Never mix two groups in one profile cache. A server-side membership
        # change needs a fresh local projection before global behavior resumes.
        reasons.append("projection_group_mismatch")
    if schema_version != GLOBAL_MENU_SCHEMA_VERSION:
        reasons.append("schema_version_unsupported")
    if not server_advertised:
        reasons.append("capability_not_advertised")
    active = not reasons
    quarantine_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM global_menu_sync_quarantine WHERE resolved_at IS NULL"
        ).fetchone()[0]
        or 0
    )
    return GlobalMenuCapabilityStatus(
        mode=GLOBAL_MENU_MODE if active else LEGACY_MENU_MODE,
        restaurant_id=restaurant_id,
        menu_group_id=group_id if active else None,
        schema_version=schema_version,
        server_advertised=server_advertised,
        authorized=bool(authorized),
        selected=selected,
        reason="active" if active else ",".join(reasons),
        catalog_revision=int(state.get("catalog_revision") or 0),
        mutation_revision=int(state.get("mutation_revision") or 0),
        snapshot_cursor=state.get("snapshot_cursor"),
        event_cursor=state.get("event_cursor"),
        assignment_cursor=state.get("assignment_cursor"),
        history_cursor=state.get("history_cursor"),
        bootstrap_status=str(state.get("bootstrap_status") or "not_started"),
        coverage_linked=int(state.get("coverage_linked") or 0),
        coverage_total=int(state.get("coverage_total") or 0),
        quarantine_count=quarantine_count,
        capabilities=capabilities,
    )


def require_global_menu_capability(
    conn,
    *,
    for_write: bool = False,
    allow_resolution_write: bool = False,
    allow_profile_sync: bool = False,
) -> GlobalMenuCapabilityStatus:
    status = resolve_global_menu_capability(
        conn, allow_profile_sync=allow_profile_sync
    )
    if not status.active:
        raise GlobalMenuCapabilityError(
            "Global menu capability is unavailable or stale; sync the restaurant registry first"
        )
    if for_write and allow_resolution_write and not status.resolution_advertised:
        error = GlobalMenuCapabilityError(
            "Global menu resolution writes are not enabled for this restaurant"
        )
        error.code = "global_menu_resolution_disabled"
        raise error
    if for_write and allow_resolution_write and not status.resolution_ready:
        error = GlobalMenuCapabilityError(
            "Global menu resolution is blocked until bootstrap is complete and quarantine is empty"
        )
        error.code = "global_menu_resolution_not_ready"
        raise error
    if for_write and not allow_resolution_write and not status.mutation_advertised:
        error = GlobalMenuCapabilityError(
            "Global menu mutations are not enabled for this restaurant"
        )
        error.code = "global_menu_mutations_disabled"
        raise error
    if for_write and not allow_resolution_write and not status.mutation_ready:
        error = GlobalMenuCapabilityError(
            "Global menu mutation is blocked until bootstrap coverage is complete and quarantine is empty"
        )
        error.code = "global_menu_coverage_incomplete"
        raise error
    return status


def update_global_menu_state(conn, **values: Any) -> None:
    allowed = {
        "mode",
        "menu_group_id",
        "catalog_revision",
        "mutation_revision",
        "snapshot_cursor",
        "event_cursor",
        "assignment_cursor",
        "history_cursor",
        "bootstrap_status",
        "coverage_linked",
        "coverage_total",
        "last_error",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"Unknown global menu state field(s): {', '.join(unknown)}")
    if not values:
        return
    assignments = ", ".join(f"{key}=?" for key in values)
    conn.execute(
        f"UPDATE global_menu_state SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE singleton_id=1",
        list(values.values()),
    )


def quarantine_global_menu_payload(
    conn,
    *,
    payload_key: str,
    stream: str,
    payload: Any,
    error_code: str,
    error_message: str,
    menu_group_id: Optional[str] = None,
    page_cursor: Optional[str] = None,
    server_revision: Optional[int] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO global_menu_sync_quarantine (
            payload_key, menu_group_id, stream, page_cursor, server_revision,
            payload, error_code, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(payload_key) DO UPDATE SET
            fail_count = global_menu_sync_quarantine.fail_count + 1,
            payload = excluded.payload,
            error_code = excluded.error_code,
            error_message = excluded.error_message,
            last_failed_at = CURRENT_TIMESTAMP,
            resolved_at = NULL
        """,
        (
            str(payload_key),
            menu_group_id,
            str(stream),
            page_cursor,
            server_revision,
            json.dumps(payload, sort_keys=True, default=str),
            str(error_code),
            str(error_message),
        ),
    )


def clear_resolved_quarantine(conn, payload_keys: Iterable[str]) -> None:
    keys = sorted({str(value) for value in payload_keys if str(value).strip()})
    if not keys:
        return
    placeholders = ",".join("?" for _ in keys)
    conn.execute(
        f"UPDATE global_menu_sync_quarantine SET resolved_at=CURRENT_TIMESTAMP WHERE payload_key IN ({placeholders})",
        keys,
    )


def resolve_global_menu_quarantine_page(
    conn, *, stream: str, page_cursor: Optional[str]
) -> None:
    """Close the poison-page record after the same server page is repaired."""
    conn.execute(
        """
        UPDATE global_menu_sync_quarantine
        SET resolved_at=CURRENT_TIMESTAMP
        WHERE stream=? AND page_cursor IS ? AND resolved_at IS NULL
        """,
        (str(stream), page_cursor),
    )


def list_global_menu_quarantine(
    conn, *, include_resolved: bool = False
) -> list[Dict[str, Any]]:
    if not _table_columns(conn, "global_menu_sync_quarantine"):
        # Focused legacy tests/tools may intentionally create only the older
        # conflict tables. The dormant projection contributes no conflicts in
        # that compatibility shape.
        return []
    where = "" if include_resolved else "WHERE resolved_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT quarantine_id, payload_key, menu_group_id, stream, page_cursor,
               server_revision, payload, error_code, error_message, fail_count,
               first_failed_at, last_failed_at, resolved_at
        FROM global_menu_sync_quarantine
        {where}
        ORDER BY resolved_at IS NOT NULL, last_failed_at DESC, quarantine_id DESC
        """
    ).fetchall()
    columns = (
        "quarantine_id",
        "payload_key",
        "menu_group_id",
        "stream",
        "page_cursor",
        "server_revision",
        "payload",
        "error_code",
        "error_message",
        "fail_count",
        "first_failed_at",
        "last_failed_at",
        "resolved_at",
    )
    return [dict(zip(columns, row)) for row in rows]
