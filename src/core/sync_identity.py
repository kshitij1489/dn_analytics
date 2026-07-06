"""
Shared cloud sync attribution helpers.

Provides a stable per-device identity and a persistent per-install identity so
cloud event payloads can distinguish:
  - which employee triggered the action
  - which physical device produced it
  - which local installation uploaded it
"""

import hashlib
import platform
import uuid
from typing import Any, Dict, Optional


SYNC_DEVICE_ID_KEY = "sync_device_id"
SYNC_INSTALL_ID_KEY = "sync_install_id"
MENU_STATE_REVISION_KEY = "menu_state_revision"
MENU_STRICT_MODE_ENABLED_KEY = "menu_strict_mode_enabled"
CUSTOMER_STATE_REVISION_KEY = "customer_state_revision"
CUSTOMER_STRICT_MODE_ENABLED_KEY = "customer_strict_mode_enabled"


def ensure_sync_identity_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _get_system_config_value(conn, key: str) -> Optional[str]:
    ensure_sync_identity_tables(conn)
    row = conn.execute(
        "SELECT value FROM system_config WHERE key = ? LIMIT 1",
        (key,),
    ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def _set_system_config_value(conn, key: str, value: str) -> None:
    ensure_sync_identity_tables(conn)
    conn.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )


def _current_device_label() -> str:
    for candidate in (platform.node(), platform.machine(), platform.system()):
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return "desktop-device"


def _generate_device_id() -> str:
    seed = "|".join(
        value
        for value in (
            platform.node().strip(),
            platform.system().strip(),
            platform.machine().strip(),
        )
        if value
    )
    if not seed:
        seed = uuid.uuid4().hex
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"device-{digest}"


def _generate_install_id() -> str:
    return f"install-{uuid.uuid4().hex}"


def get_device_identity(conn) -> Dict[str, str]:
    device_id = _get_system_config_value(conn, SYNC_DEVICE_ID_KEY)
    if not device_id:
        device_id = _generate_device_id()
        _set_system_config_value(conn, SYNC_DEVICE_ID_KEY, device_id)

    install_id = _get_system_config_value(conn, SYNC_INSTALL_ID_KEY)
    if not install_id:
        install_id = _generate_install_id()
        _set_system_config_value(conn, SYNC_INSTALL_ID_KEY, install_id)

    return {
        "device_id": device_id,
        "install_id": install_id,
        "device_label": _current_device_label(),
        "platform": platform.system() or "unknown",
        "platform_release": platform.release() or "unknown",
        "machine": platform.machine() or "unknown",
    }


def get_active_user_identity(conn) -> Optional[Dict[str, str]]:
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT employee_id, name FROM app_users WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    except Exception:
        return None

    if row is None:
        return None
    return {"employee_id": str(row[0]), "name": str(row[1])}


def get_sync_attribution(conn) -> Dict[str, Any]:
    return {
        "employee": get_active_user_identity(conn),
        "device": get_device_identity(conn),
    }


def get_menu_state_revision(conn) -> Optional[int]:
    """Return the mirrored server menu revision, or None if never advertised."""
    raw = _get_system_config_value(conn, MENU_STATE_REVISION_KEY)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def set_menu_state_revision(conn, value: int) -> None:
    """Store menu revision monotonically — never rewind on replay or out-of-order pulls."""
    current = get_menu_state_revision(conn)
    stored = value if current is None else max(current, int(value))
    _set_system_config_value(conn, MENU_STATE_REVISION_KEY, str(stored))


def get_menu_strict_mode_enabled(conn) -> bool:
    raw = _get_system_config_value(conn, MENU_STRICT_MODE_ENABLED_KEY)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def set_menu_strict_mode_enabled(conn, value: bool) -> None:
    _set_system_config_value(conn, MENU_STRICT_MODE_ENABLED_KEY, "1" if value else "0")


def extract_menu_scope_state(data: Any) -> Dict[str, Any]:
    """Parse menu_revision / strict_mode_enabled from a server pull response payload."""
    if not isinstance(data, dict):
        return {}
    scope: Dict[str, Any] = {}
    if "menu_revision" in data and data["menu_revision"] is not None:
        scope["menu_revision"] = data["menu_revision"]
    if "strict_mode_enabled" in data and data["strict_mode_enabled"] is not None:
        scope["strict_mode_enabled"] = data["strict_mode_enabled"]
    return scope


def apply_menu_scope_state(conn, scope: Dict[str, Any]) -> None:
    """Mirror advertised scope state into system_config when the server sends it."""
    if not scope:
        return
    revision = scope.get("menu_revision")
    if revision is not None:
        set_menu_state_revision(conn, int(revision))
    if "strict_mode_enabled" in scope and scope["strict_mode_enabled"] is not None:
        set_menu_strict_mode_enabled(conn, bool(scope["strict_mode_enabled"]))


def get_customer_state_revision(conn) -> Optional[int]:
    """Return the mirrored server customer revision, or None if never advertised."""
    raw = _get_system_config_value(conn, CUSTOMER_STATE_REVISION_KEY)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def set_customer_state_revision(conn, value: int) -> None:
    """Store customer revision monotonically — never rewind on replay or out-of-order pulls."""
    current = get_customer_state_revision(conn)
    stored = value if current is None else max(current, int(value))
    _set_system_config_value(conn, CUSTOMER_STATE_REVISION_KEY, str(stored))


def get_customer_strict_mode_enabled(conn) -> bool:
    raw = _get_system_config_value(conn, CUSTOMER_STRICT_MODE_ENABLED_KEY)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def set_customer_strict_mode_enabled(conn, value: bool) -> None:
    _set_system_config_value(conn, CUSTOMER_STRICT_MODE_ENABLED_KEY, "1" if value else "0")


def extract_customer_scope_state(data: Any) -> Dict[str, Any]:
    """Parse customer_revision / strict_mode_enabled from a server pull response payload."""
    if not isinstance(data, dict):
        return {}
    scope: Dict[str, Any] = {}
    if "customer_revision" in data and data["customer_revision"] is not None:
        scope["customer_revision"] = data["customer_revision"]
    if "strict_mode_enabled" in data and data["strict_mode_enabled"] is not None:
        scope["strict_mode_enabled"] = data["strict_mode_enabled"]
    return scope


def apply_customer_scope_state(conn, scope: Dict[str, Any]) -> None:
    """Mirror advertised customer scope state into system_config when the server sends it."""
    if not scope:
        return
    revision = scope.get("customer_revision")
    if revision is not None:
        set_customer_state_revision(conn, int(revision))
    if "strict_mode_enabled" in scope and scope["strict_mode_enabled"] is not None:
        set_customer_strict_mode_enabled(conn, bool(scope["strict_mode_enabled"]))


def should_apply_pulled_customer_revision(*, has_more: bool, stats: Dict[str, Any]) -> bool:
    """Only advance customer_revision after a fully drained pull with no failures."""
    if has_more:
        return False
    if stats.get("error"):
        return False
    return True


def apply_customer_pull_scope_state(
    conn,
    scope_state: Dict[str, Any],
    *,
    has_more: bool,
    stats: Dict[str, Any],
    allow_customer_revision: bool = False,
) -> Optional[int]:
    """
    Apply strict-mode flags from a customer merge pull page.

    Returns the advertised customer_revision when present. The revision is written to
    system_config only when allow_customer_revision is True and the pull page is clean.
    """
    if not scope_state:
        return None

    advertised_revision = None
    if scope_state.get("customer_revision") is not None:
        advertised_revision = int(scope_state["customer_revision"])

    scope_for_apply = dict(scope_state)
    if not allow_customer_revision or not should_apply_pulled_customer_revision(
        has_more=has_more,
        stats=stats,
    ):
        scope_for_apply.pop("customer_revision", None)
    apply_customer_scope_state(conn, scope_for_apply)
    return advertised_revision


def should_apply_pulled_menu_revision(conn, *, has_more: bool, stats: Dict[str, Any]) -> bool:
    """
    menu_revision is global across merge + verification streams.

    Only advance it after a fully drained pull with no quarantined/failed events on
    either stream.
    """
    from src.core.menu_mapping_verification_sync import QUARANTINE_STREAM_MAPPING_VERIFICATION
    from src.core.menu_merge_sync import QUARANTINE_STREAM_MENU_MERGE
    from src.core.menu_sync_quarantine import get_unresolved_quarantined_count

    if has_more:
        return False
    if (
        stats.get("events_failed")
        or stats.get("events_quarantined")
        or stats.get("deferred")
    ):
        return False
    if get_unresolved_quarantined_count(conn, QUARANTINE_STREAM_MENU_MERGE) > 0:
        return False
    if get_unresolved_quarantined_count(conn, QUARANTINE_STREAM_MAPPING_VERIFICATION) > 0:
        return False
    return True


def apply_pull_scope_state(
    conn,
    scope_state: Dict[str, Any],
    *,
    has_more: bool,
    stats: Dict[str, Any],
    allow_menu_revision: bool = False,
) -> Optional[int]:
    """
    Apply strict-mode flags from a pull page.

    Returns the advertised menu_revision when present. The revision is written to
    system_config only when allow_menu_revision is True and both streams are clean.
    """
    if not scope_state:
        return None

    advertised_revision = None
    if scope_state.get("menu_revision") is not None:
        advertised_revision = int(scope_state["menu_revision"])

    scope_for_apply = dict(scope_state)
    if not allow_menu_revision or not should_apply_pulled_menu_revision(
        conn,
        has_more=has_more,
        stats=stats,
    ):
        scope_for_apply.pop("menu_revision", None)
    apply_menu_scope_state(conn, scope_for_apply)
    return advertised_revision
