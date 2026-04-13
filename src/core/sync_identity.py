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
