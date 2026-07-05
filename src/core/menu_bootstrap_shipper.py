"""
Menu bootstrap shipper: upload id_maps_backup.json + cluster_state_backup.json to cloud.

Uses CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL (placeholder by default). When cloud server
is ready, set the env var to the real URL for plug-and-play. Call upload_pending() periodically
or after merge/verify.

Since Phase C4 (sync conflict plan) the payload is marked snapshot_role="seed_only" —
the server seeds fresh installs from it but no longer treats its cluster_state as
authoritative — and pushes are skipped while the id_maps catalog hash is unchanged
(per-order-item changes ride the assignment event stream instead).
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.config.client_learning_config import (
    CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL,
)
from src.core.utils.path_helper import get_resource_path


SNAPSHOT_ROLE = "seed_only"
LAST_PUSH_HASH_FILENAME = "menu_bootstrap_last_push_hash.txt"


def _hash_id_maps(id_maps: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(id_maps, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _last_push_hash_path(data_dir) -> Path:
    return Path(data_dir) / LAST_PUSH_HASH_FILENAME


def upload_pending(
    endpoint: Optional[str] = None,
    auth: Optional[str] = None,
    uploaded_by: Optional[Dict[str, str]] = None,
    uploaded_from: Optional[Dict[str, str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Read id_maps_backup.json and cluster_state_backup.json from data/, POST to cloud.
    uploaded_by: optional {"employee_id": "...", "name": "..."} from app_users; appended to payload.
    Returns {"sent": True/False, "error": str or None}. No local "mark sent" — fire-and-forget.
    Uses same data/ path as seed_from_backups / export_to_backups.

    Skips the push when the id_maps hash matches the last successful push
    (catalog shape unchanged) unless force=True.
    """
    url = (endpoint or CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL).strip()
    if not url:
        return {"sent": False, "error": None}

    data_dir = get_resource_path("data")
    id_maps_path = Path(data_dir) / "id_maps_backup.json"
    cluster_state_path = Path(data_dir) / "cluster_state_backup.json"

    if not id_maps_path.exists() or not cluster_state_path.exists():
        return {"sent": False, "error": "Backup files not found"}

    try:
        with open(id_maps_path, "r", encoding="utf-8") as f:
            id_maps = json.load(f)
        with open(cluster_state_path, "r", encoding="utf-8") as f:
            cluster_state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"sent": False, "error": str(e)}

    id_maps_hash = _hash_id_maps(id_maps)
    hash_path = _last_push_hash_path(data_dir)
    if not force:
        try:
            if hash_path.exists() and hash_path.read_text().strip() == id_maps_hash:
                return {"sent": False, "skipped": "id_maps unchanged", "error": None}
        except OSError:
            pass

    payload: Dict[str, Any] = {
        "id_maps": id_maps,
        "cluster_state": cluster_state,
        "snapshot_role": SNAPSHOT_ROLE,
    }
    if uploaded_by:
        payload["uploaded_by"] = uploaded_by
    if uploaded_from:
        payload["uploaded_from"] = uploaded_from
    headers = {"Content-Type": "application/json"}
    token = auth
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        import requests
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        if r.status_code >= 400:
            return {"sent": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"sent": False, "error": str(e)}

    try:
        hash_path.write_text(id_maps_hash)
    except OSError:
        pass

    return {"sent": True, "error": None}
