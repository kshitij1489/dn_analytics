"""
Menu bootstrap shipper: upload the SQLite-derived catalog seed payload to cloud.

Uses CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL (placeholder by default). When cloud server
is ready, set the env var to the real URL for plug-and-play. Call upload_pending(conn)
periodically as a best-effort seed mirror.

Since Phase C4 (sync conflict plan) the payload is marked snapshot_role="seed_only" —
the server seeds fresh installs from it but no longer treats its cluster_state as
authoritative — and pushes are skipped while the id_maps + shared POS observation hash
is unchanged. Per-order-item assignment changes still ride the assignment event stream.
Catalog-only human edits are authorized by strict catalog_update commits; this shipper
is not the OCC or peer-convergence path for those edits.
"""

import hashlib
import json
from typing import Any, Dict, Optional

from src.core.config.client_learning_config import (
    CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL,
)
from src.core.menu_catalog_seed import (
    build_cluster_state,
    build_id_maps,
    build_shared_pos_catalog,
)


SNAPSHOT_ROLE = "seed_only"
LAST_PUSH_HASH_KEY = "menu_bootstrap_last_push_hash"


def _hash_bootstrap_observation(
    id_maps: Dict[str, Any], shared_pos_catalog: list[Dict[str, Any]]
) -> str:
    return hashlib.sha256(
        json.dumps(
            {"id_maps": id_maps, "shared_pos_catalog": shared_pos_catalog},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def upload_pending(
    conn,
    endpoint: Optional[str] = None,
    auth: Optional[str] = None,
    uploaded_by: Optional[Dict[str, str]] = None,
    uploaded_from: Optional[Dict[str, str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Build the catalog seed payload from SQLite and POST it to cloud.
    uploaded_by: optional {"employee_id": "...", "name": "..."} from app_users; appended to payload.
    Returns {"sent": True/False, "error": str or None}. No local "mark sent" — fire-and-forget.

    Skips the push when the id_maps + shared POS observation hash matches the
    last successful confirmed push unless force=True.
    """
    url = (endpoint or CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL).strip()
    if not url:
        return {"sent": False, "error": None}
    if conn is None:
        return {"sent": False, "error": "No connection"}

    try:
        id_maps = build_id_maps(conn)
        cluster_state = build_cluster_state(conn)
        shared_pos_catalog = build_shared_pos_catalog(conn)
    except Exception as e:
        return {"sent": False, "error": str(e)}

    observation_hash = _hash_bootstrap_observation(id_maps, shared_pos_catalog)
    if not force:
        try:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key=?", (LAST_PUSH_HASH_KEY,)
            ).fetchone()
            if row and str(row[0]).strip() == observation_hash:
                return {
                    "sent": False,
                    "skipped": "id_maps + shared_pos_catalog unchanged",
                    "error": None,
                }
        except Exception:
            pass

    payload: Dict[str, Any] = {
        "id_maps": id_maps,
        "cluster_state": cluster_state,
        "shared_pos_catalog": shared_pos_catalog,
        "snapshot_role": SNAPSHOT_ROLE,
    }
    if uploaded_by:
        payload["uploaded_by"] = uploaded_by
    if uploaded_from:
        payload["uploaded_from"] = uploaded_from
    from src.core.central_api import response_error_text, scoped_headers

    headers = scoped_headers(
        conn, auth_kind="sync", credential=auth, content_type="application/json"
    )

    try:
        import requests
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        if r.status_code >= 400:
            return {"sent": False, "error": response_error_text(r, conn=conn)}
        try:
            response_payload = r.json()
        except Exception:
            return {
                "sent": False,
                "error": "Menu bootstrap response was not valid JSON",
            }
        if not isinstance(response_payload, dict) or (
            response_payload.get("shared_pos_catalog_updated") is not True
        ):
            return {
                "sent": False,
                "error": "Server did not confirm shared_pos_catalog update",
            }
    except Exception as e:
        return {"sent": False, "error": str(e)}

    try:
        conn.execute(
            """
            INSERT INTO system_config (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (LAST_PUSH_HASH_KEY, observation_hash),
        )
        conn.commit()
    except Exception:
        conn.rollback()

    return {"sent": True, "error": None}
