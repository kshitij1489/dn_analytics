"""
Menu bootstrap cloud pull/apply helpers.

This consumes the latest menu bootstrap snapshot from Dachnona, writes the same
backup files used by local export/import flows, reuses perform_seeding(), and
optionally relinks historical order_items from the seeded snapshot mappings.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from scripts.seed_from_backups import perform_seeding
from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.utils.path_helper import get_resource_path


DEFAULT_MENU_BOOTSTRAP_APPLY_MODE = "seed_and_relink_orders"
SUPPORTED_MENU_BOOTSTRAP_APPLY_MODES = {
    "seed_only",
    "seed_and_relink_orders",
}


def get_menu_bootstrap_pull_endpoint(conn) -> Optional[str]:
    from src.core.config.client_learning_config import CLIENT_LEARNING_MENU_BOOTSTRAP_PULL_URL

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url}/desktop-analytics-sync/menu-bootstrap/latest"
    return CLIENT_LEARNING_MENU_BOOTSTRAP_PULL_URL or None


def _normalize_snapshot_payload(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Invalid response payload")

    snapshot = data.get("snapshot")
    if isinstance(snapshot, dict):
        payload = snapshot
    else:
        payload = data

    id_maps = payload.get("id_maps")
    cluster_state = payload.get("cluster_state")
    if not isinstance(id_maps, dict) or not isinstance(cluster_state, dict):
        raise ValueError("Response is missing id_maps or cluster_state")

    metadata: Dict[str, Any] = {}
    for key in ("updated_at", "created_at", "snapshot_id", "cursor", "version"):
        if data.get(key) is not None:
            metadata[key] = data.get(key)
        elif payload.get(key) is not None:
            metadata[key] = payload.get(key)

    return {
        "id_maps": id_maps,
        "cluster_state": cluster_state,
        "metadata": metadata,
    }


def _write_menu_bootstrap_backups(id_maps: Dict[str, Any], cluster_state: Dict[str, Any]) -> None:
    data_dir = Path(get_resource_path("data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    id_maps_path = data_dir / "id_maps_backup.json"
    cluster_state_path = data_dir / "cluster_state_backup.json"

    with open(id_maps_path, "w", encoding="utf-8") as handle:
        json.dump(id_maps, handle, indent=2, sort_keys=True)
    with open(cluster_state_path, "w", encoding="utf-8") as handle:
        json.dump(cluster_state, handle, indent=2, sort_keys=True)


def _extract_snapshot_assignments(cluster_state: Dict[str, Any]) -> Dict[int, Dict[str, Optional[str]]]:
    assignments: Dict[int, Dict[str, Optional[str]]] = {}
    for key, orders in cluster_state.items():
        if not isinstance(orders, dict):
            continue
        menu_item_id = str(key).split(":", 1)[0]
        for order_item_id, items in orders.items():
            try:
                normalized_order_item_id = int(order_item_id)
            except (TypeError, ValueError):
                continue

            if not isinstance(items, list):
                continue

            seen_variants = set()
            for row in items:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                variant_id = row[1]
                if variant_id in seen_variants:
                    continue
                seen_variants.add(variant_id)
                assignments[normalized_order_item_id] = {
                    "menu_item_id": menu_item_id,
                    "variant_id": str(variant_id) if variant_id is not None else None,
                }
    return assignments


def _count_order_items_present(conn, order_item_ids: Dict[int, Dict[str, Optional[str]]]) -> int:
    if not order_item_ids:
        return 0
    placeholders = ",".join("?" for _ in order_item_ids)
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM order_items
        WHERE order_item_id IN ({placeholders})
        """,
        list(order_item_ids),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _relink_order_items_from_snapshot(conn, assignments: Dict[int, Dict[str, Optional[str]]]) -> int:
    updated = 0
    for order_item_id, assignment in assignments.items():
        row = conn.execute(
            """
            SELECT menu_item_id, variant_id
            FROM order_items
            WHERE order_item_id = ?
            """,
            (order_item_id,),
        ).fetchone()
        if row is None:
            continue

        current_menu_item_id = row["menu_item_id"]
        current_variant_id = row["variant_id"]
        next_menu_item_id = assignment["menu_item_id"]
        next_variant_id = assignment["variant_id"]
        if current_menu_item_id == next_menu_item_id and current_variant_id == next_variant_id:
            continue

        conn.execute(
            """
            UPDATE order_items
            SET menu_item_id = ?,
                variant_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE order_item_id = ?
            """,
            (next_menu_item_id, next_variant_id, order_item_id),
        )
        updated += 1

    if updated:
        conn.commit()
    return updated


def fetch_latest_menu_bootstrap_snapshot(
    endpoint: str,
    auth: Optional[str] = None,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"

    try:
        import requests

        response = requests.get(endpoint, headers=headers, timeout=60)
        if response.status_code >= 400:
            return {"error": f"HTTP {response.status_code}"}
        data = response.json()
        normalized = _normalize_snapshot_payload(data)
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "id_maps": normalized["id_maps"],
        "cluster_state": normalized["cluster_state"],
        "metadata": normalized["metadata"],
        "error": None,
    }


def apply_menu_bootstrap_snapshot(
    conn,
    id_maps: Dict[str, Any],
    cluster_state: Dict[str, Any],
    apply_mode: str = DEFAULT_MENU_BOOTSTRAP_APPLY_MODE,
) -> Dict[str, Any]:
    if apply_mode not in SUPPORTED_MENU_BOOTSTRAP_APPLY_MODES:
        raise ValueError(
            f"Unsupported apply_mode '{apply_mode}'. Expected one of: {', '.join(sorted(SUPPORTED_MENU_BOOTSTRAP_APPLY_MODES))}"
        )

    assignments = _extract_snapshot_assignments(cluster_state)
    _write_menu_bootstrap_backups(id_maps, cluster_state)

    if not perform_seeding(conn):
        return {
            "items_seeded": 0,
            "variants_seeded": 0,
            "mapping_assignments": len(assignments),
            "order_items_present_in_snapshot": 0,
            "order_items_relinked": 0,
            "apply_mode": apply_mode,
            "warnings": [],
            "error": "Menu bootstrap seeding failed",
        }

    order_items_present = _count_order_items_present(conn, assignments)
    order_items_relinked = 0
    if apply_mode == "seed_and_relink_orders":
        order_items_relinked = _relink_order_items_from_snapshot(conn, assignments)

    return {
        "items_seeded": len(id_maps.get("menu_id_to_str", {})),
        "variants_seeded": len(id_maps.get("variant_id_to_str", {})),
        "mapping_assignments": len(assignments),
        "order_items_present_in_snapshot": order_items_present,
        "order_items_relinked": order_items_relinked,
        "apply_mode": apply_mode,
        "warnings": [
            "Menu bootstrap snapshots do not restore order_item_addons remaps.",
            "Menu bootstrap snapshots do not restore merge_history audit rows.",
        ],
        "error": None,
    }


def fetch_and_apply_menu_bootstrap_snapshot(
    conn,
    endpoint: str,
    auth: Optional[str] = None,
    apply_mode: str = DEFAULT_MENU_BOOTSTRAP_APPLY_MODE,
) -> Dict[str, Any]:
    fetch_result = fetch_latest_menu_bootstrap_snapshot(endpoint, auth=auth)
    if fetch_result.get("error"):
        return {
            "items_seeded": 0,
            "variants_seeded": 0,
            "mapping_assignments": 0,
            "order_items_present_in_snapshot": 0,
            "order_items_relinked": 0,
            "apply_mode": apply_mode,
            "warnings": [],
            "metadata": {},
            "error": fetch_result["error"],
        }

    apply_result = apply_menu_bootstrap_snapshot(
        conn,
        fetch_result["id_maps"],
        fetch_result["cluster_state"],
        apply_mode=apply_mode,
    )
    apply_result["metadata"] = fetch_result.get("metadata", {})
    return apply_result
