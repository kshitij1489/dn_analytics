"""
Cloud Sync Config — Single source for cloud_sync_url and cloud_sync_api_key.

Reads from the system_config DB table. All modules that need the cloud sync
URL or API key should import from here instead of duplicating the SQL lookup.
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_cloud_sync_config(conn) -> Tuple[Optional[str], Optional[str]]:
    """
    Read cloud sync URL and API key from system_config table.

    Returns:
        (base_url, api_key) — either may be None if not configured or DB unavailable.
        base_url is stripped of trailing slashes.
    """
    base_url: Optional[str] = None
    api_key: Optional[str] = None

    if not conn:
        return base_url, api_key

    try:
        cur = conn.execute("SELECT value FROM system_config WHERE key='cloud_sync_url'")
        row = cur.fetchone()
        if row and row[0]:
            base_url = row[0].strip().rstrip("/") or None
    except Exception:
        pass

    try:
        cur = conn.execute("SELECT value FROM system_config WHERE key='cloud_sync_api_key'")
        row = cur.fetchone()
        if row and row[0]:
            api_key = row[0].strip() or None
    except Exception:
        pass

    return base_url, api_key
