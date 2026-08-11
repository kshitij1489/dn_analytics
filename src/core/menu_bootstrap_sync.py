"""
Menu bootstrap cloud pull/apply helpers.

This consumes the latest menu bootstrap snapshot from Dachnona, seeds the
catalog directly from the in-memory payload, and optionally relinks historical
order_items from the seeded snapshot mappings.
"""

import os
from typing import Any, Dict, Optional

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.menu_catalog_seed import seed_catalog
from src.core.sync_identity import extract_menu_scope_state


# Since Phase C4 (sync conflict plan) the routine pull only seeds the catalog:
# per-order-item relinking is owned by the assignment sync stream. The relink
# mode stays available behind an explicit flag for support/restore flows.
DEFAULT_MENU_BOOTSTRAP_APPLY_MODE = "seed_only"
SUPPORTED_MENU_BOOTSTRAP_APPLY_MODES = {
    "seed_only",
    "seed_and_relink_orders",
}
MENU_BOOTSTRAP_APPLY_MODE_CONFIG_KEY = "menu_bootstrap_apply_mode"
MENU_BOOTSTRAP_APPLY_MODE_ENV = "MENU_BOOTSTRAP_APPLY_MODE"


def get_menu_bootstrap_apply_mode(conn) -> str:
    """
    Resolve the apply mode for routine bootstrap pulls: env override first,
    then the system_config flag, else the seed-only default (plan C4.1).
    """
    for raw in (
        os.environ.get(MENU_BOOTSTRAP_APPLY_MODE_ENV),
        _get_config_apply_mode(conn),
    ):
        mode = str(raw or "").strip()
        if mode in SUPPORTED_MENU_BOOTSTRAP_APPLY_MODES:
            return mode
    return DEFAULT_MENU_BOOTSTRAP_APPLY_MODE


def _get_config_apply_mode(conn) -> Optional[str]:
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM system_config WHERE key = ? LIMIT 1",
            (MENU_BOOTSTRAP_APPLY_MODE_CONFIG_KEY,),
        ).fetchone()
    except Exception:
        return None
    return str(row[0]) if row and row[0] else None


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
    conn,
    endpoint: str,
    auth: Optional[str] = None,
) -> Dict[str, Any]:
    from src.core.central_api import response_error_text, scoped_headers

    headers = scoped_headers(conn, auth_kind="sync", credential=auth)

    try:
        import requests

        response = requests.get(endpoint, headers=headers, timeout=60)
        if response.status_code >= 400:
            return {"error": response_error_text(response, conn=conn)}
        data = response.json()
        scope_state = extract_menu_scope_state(data)
        normalized = _normalize_snapshot_payload(data)
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "id_maps": normalized["id_maps"],
        "cluster_state": normalized["cluster_state"],
        "metadata": normalized["metadata"],
        "scope_state": scope_state,
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

    # seed_only pulls seed the catalog only: menu_item_variants rows are per-
    # order-item assignments owned by the assignment sync stream, and a frozen
    # snapshot must never roll them back past the assignment cursor (I6).
    seed_mappings = apply_mode == "seed_and_relink_orders"
    try:
        seed_counts = seed_catalog(
            conn,
            id_maps,
            cluster_state,
            seed_mappings=seed_mappings,
        )
    except Exception as exc:
        return {
            "items_seeded": 0,
            "variants_seeded": 0,
            "mapping_assignments": len(assignments),
            "order_items_present_in_snapshot": 0,
            "order_items_relinked": 0,
            "apply_mode": apply_mode,
            "warnings": [],
            "error": f"Menu bootstrap seeding failed: {exc}",
        }

    order_items_present = _count_order_items_present(conn, assignments)
    order_items_relinked = 0
    if apply_mode == "seed_and_relink_orders":
        order_items_relinked = _relink_order_items_from_snapshot(conn, assignments)
        # Contingency restore rewrote assignments; refresh the derived
        # itemcode projection from them (plan §8; caller commits).
        from src.core.itemcode_mapping import rebuild_itemcode_mappings_best_effort

        rebuild_itemcode_mappings_best_effort(conn)

    return {
        "items_seeded": seed_counts["items_seeded"],
        "variants_seeded": seed_counts["variants_seeded"],
        "mappings_seeded": seed_counts["mappings_seeded"],
        "stub_count": seed_counts["stub_count"],
        "skipped_unmapped": seed_counts["skipped_unmapped"],
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
    fetch_result = fetch_latest_menu_bootstrap_snapshot(conn, endpoint, auth=auth)
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
    # menu_revision is deliberately not mirrored here: the bootstrap does not
    # drain the menu event streams, so advancing it would break the invariant
    # "menu_state_revision is current => pull cursors are current" and let a
    # later commit be accepted while peer events are still unapplied (plan
    # §12.5). The revision is owned by pull_latest_menu_state and the
    # assignment snapshot, which keep the cursors consistent.
    conn.commit()
    apply_result["metadata"] = fetch_result.get("metadata", {})
    return apply_result
