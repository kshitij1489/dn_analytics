"""
Cloud Sync Config — Single source for cloud_sync_url and cloud_sync_api_key.

These are app-global settings, so they live in the control database and fall
back to a profile's legacy `system_config` rows. All modules that need the cloud
sync URL or API key should import from here instead of duplicating the lookup.
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_cloud_sync_config(conn) -> Tuple[Optional[str], Optional[str]]:
    """
    Read cloud sync URL and API key.

    Returns:
        (base_url, api_key) — either may be None if not configured or DB unavailable.
        base_url is stripped of trailing slashes.
    """
    try:
        from src.core.db.control import resolve_config_values

        values = resolve_config_values(conn, ("cloud_sync_url", "cloud_sync_api_key"))
    except Exception:
        logger.debug("Cloud sync config lookup failed", exc_info=True)
        return None, None

    raw_url = str(values.get("cloud_sync_url") or "").strip().rstrip("/")
    raw_key = str(values.get("cloud_sync_api_key") or "").strip()
    return (raw_url or None), (raw_key or None)


def get_global_menu_editor_key(conn) -> Optional[str]:
    """Read the separate credential required for menu-group mutations."""
    try:
        from src.core.db.control import resolve_config_values

        values = resolve_config_values(conn, ("global_menu_editor_key",))
    except Exception:
        logger.debug("Global menu editor credential lookup failed", exc_info=True)
        return None
    raw_key = str(values.get("global_menu_editor_key") or "").strip()
    return raw_key or None
