"""Canonical local/global identity resolution for ``global_menu_v1``.

No other module should walk redirect chains or choose mapping-rule precedence.
The resolver returns a local projection target because existing order tables
retain their local foreign keys during the additive migration.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.core.global_menu_schema import resolve_global_menu_capability
from utils.id_generator import generate_deterministic_id


MAX_REDIRECT_HOPS = 32


class GlobalMenuIdentityError(RuntimeError):
    code = "global_menu_identity_invalid"


@dataclass(frozen=True)
class GlobalIdentityResolution:
    resolved: bool
    local_menu_item_id: Optional[str] = None
    local_variant_id: Optional[str] = None
    global_menu_item_id: Optional[str] = None
    global_variant_id: Optional[str] = None
    canonical_name: Optional[str] = None
    canonical_type: Optional[str] = None
    canonical_variant_name: Optional[str] = None
    provenance: str = "unresolved"
    server_revision: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_alias(value: Any) -> Optional[str]:
    text = str(value or "").strip().casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return normalized or None


def normalize_locator(kind: str, value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return normalize_alias(text) if kind == "alias" else text


def ensure_no_variant_sentinel(conn) -> str:
    """Return the local NOT NULL placeholder for a global no-variant target."""
    variant_id = generate_deterministic_id("UNKNOWN")
    row = conn.execute(
        "SELECT variant_name FROM variants WHERE variant_id=?", (variant_id,)
    ).fetchone()
    if row is not None:
        if str(row[0]) != "UNKNOWN":
            raise GlobalMenuIdentityError(
                "The deterministic no-variant sentinel has incompatible metadata"
            )
        return variant_id
    name_owner = conn.execute(
        "SELECT variant_id FROM variants WHERE variant_name='UNKNOWN'"
    ).fetchone()
    if name_owner is not None and str(name_owner[0]) != variant_id:
        raise GlobalMenuIdentityError(
            "The UNKNOWN variant name is owned by a non-sentinel row"
        )
    conn.execute(
        "INSERT INTO variants (variant_id, variant_name, is_verified) "
        "VALUES (?, 'UNKNOWN', 1)",
        (variant_id,),
    )
    return variant_id


def _redirect_target(conn, entity_type: str, source_id: str) -> Optional[str]:
    if entity_type == "item":
        row = conn.execute(
            """
            SELECT target_global_menu_item_id FROM global_menu_redirects
            WHERE entity_type='item' AND source_global_menu_item_id=?
            """,
            (source_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT target_global_variant_id FROM global_menu_redirects
            WHERE entity_type='variant' AND source_global_variant_id=?
            """,
            (source_id,),
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def resolve_redirect_chain(
    conn,
    entity_type: str,
    entity_id: Optional[str],
    *,
    max_hops: int = MAX_REDIRECT_HOPS,
) -> Optional[str]:
    if entity_id is None:
        return None
    if entity_type not in {"item", "variant"}:
        raise ValueError("entity_type must be item or variant")
    current = str(entity_id)
    visited: set[str] = set()
    for _hop in range(max_hops + 1):
        if current in visited:
            raise GlobalMenuIdentityError(f"Cyclic {entity_type} redirect at {current}")
        visited.add(current)
        target = _redirect_target(conn, entity_type, current)
        if target is None:
            return current
        current = target
    raise GlobalMenuIdentityError(f"{entity_type} redirect chain exceeds {max_hops} hops")


def validate_redirect_graph(conn, pending: Sequence[Dict[str, Any]] = ()) -> None:
    graphs: Dict[str, Dict[str, str]] = {"item": {}, "variant": {}}
    for row in conn.execute(
        """
        SELECT entity_type, source_global_menu_item_id, target_global_menu_item_id,
               source_global_variant_id, target_global_variant_id
        FROM global_menu_redirects
        """
    ).fetchall():
        entity_type = str(row[0])
        source = row[1] if entity_type == "item" else row[3]
        target = row[2] if entity_type == "item" else row[4]
        if source and target:
            graphs[entity_type][str(source)] = str(target)
    for redirect in pending:
        entity_type = str(redirect.get("entity_type") or "")
        if entity_type == "item":
            source = redirect.get("source_global_menu_item_id")
            target = redirect.get("target_global_menu_item_id")
        elif entity_type == "variant":
            source = redirect.get("source_global_variant_id")
            target = redirect.get("target_global_variant_id")
        else:
            raise GlobalMenuIdentityError("Redirect entity_type must be item or variant")
        source = str(source or "").strip()
        target = str(target or "").strip()
        if not source or not target or source == target:
            raise GlobalMenuIdentityError("Redirect source/target must be nonblank and distinct")
        graphs[entity_type][source] = target

    for entity_type, graph in graphs.items():
        for start in graph:
            current = start
            visited: set[str] = set()
            for _hop in range(MAX_REDIRECT_HOPS + 1):
                if current in visited:
                    raise GlobalMenuIdentityError(
                        f"Cyclic {entity_type} redirect involving {current}"
                    )
                visited.add(current)
                current = graph.get(current)
                if current is None:
                    break
            else:
                raise GlobalMenuIdentityError(
                    f"{entity_type} redirect chain exceeds {MAX_REDIRECT_HOPS} hops"
                )


def _projection_owner(conn, entity_type: str, global_id: Optional[str]) -> Optional[str]:
    if not global_id:
        return None
    if entity_type == "item":
        row = conn.execute(
            """
            SELECT l.local_menu_item_id
            FROM menu_item_global_links l
            JOIN menu_items m ON m.menu_item_id=l.local_menu_item_id
            WHERE l.global_menu_item_id=?
            ORDER BY l.is_projection_owner DESC, l.local_menu_item_id
            LIMIT 1
            """,
            (global_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT l.local_variant_id
            FROM variant_global_links l
            JOIN variants v ON v.variant_id=l.local_variant_id
            WHERE l.global_variant_id=?
            ORDER BY l.is_projection_owner DESC, l.local_variant_id
            LIMIT 1
            """,
            (global_id,),
        ).fetchone()
    return str(row[0]) if row else None


def ensure_item_projection_owner(conn, global_menu_item_id: str) -> str:
    canonical_id = resolve_redirect_chain(conn, "item", global_menu_item_id)
    row = conn.execute(
        """
        SELECT canonical_name, canonical_type, is_verified, server_revision
        FROM global_menu_items
        WHERE global_menu_item_id=? AND lifecycle_state='active'
        """,
        (canonical_id,),
    ).fetchone()
    if row is None:
        raise GlobalMenuIdentityError(f"Unknown active global menu item: {canonical_id}")
    owner = _projection_owner(conn, "item", canonical_id)
    if owner:
        local_name = unique_local_item_name(
            conn,
            str(row[0]),
            str(row[1]),
            identity_key=canonical_id,
            exclude_menu_item_id=owner,
        )
        conn.execute(
            """
            UPDATE menu_items
            SET name=?, type=?, is_verified=?, updated_at=CURRENT_TIMESTAMP
            WHERE menu_item_id=?
            """,
            (local_name, row[1], 1 if row[2] else 0, owner),
        )
        conn.execute(
            "UPDATE menu_item_global_links SET is_projection_owner=0 WHERE global_menu_item_id=?",
            (canonical_id,),
        )
        conn.execute(
            """
            UPDATE menu_item_global_links
            SET is_projection_owner=1,
                server_revision=MAX(server_revision, ?),
                linked_at=CURRENT_TIMESTAMP
            WHERE local_menu_item_id=?
            """,
            (int(row[3]), owner),
        )
        return owner
    local_id = generate_deterministic_id("global-menu-item", canonical_id)
    local_name = unique_local_item_name(
        conn,
        str(row[0]),
        str(row[1]),
        identity_key=canonical_id,
        exclude_menu_item_id=local_id,
    )
    conn.execute(
        """
        INSERT INTO menu_items (menu_item_id, name, type, is_verified)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(menu_item_id) DO UPDATE SET
            name=excluded.name,
            type=excluded.type,
            is_verified=excluded.is_verified,
            updated_at=CURRENT_TIMESTAMP
        """,
        (local_id, local_name, row[1], 1 if row[2] else 0),
    )
    conn.execute(
        "UPDATE menu_item_global_links SET is_projection_owner=0 WHERE global_menu_item_id=?",
        (canonical_id,),
    )
    conn.execute(
        """
        INSERT INTO menu_item_global_links (
            local_menu_item_id, global_menu_item_id, provenance,
            server_revision, is_projection_owner
        ) VALUES (?, ?, 'projection', ?, 1)
        ON CONFLICT(local_menu_item_id) DO UPDATE SET
            global_menu_item_id=excluded.global_menu_item_id,
            provenance='projection',
            server_revision=excluded.server_revision,
            is_projection_owner=1,
            linked_at=CURRENT_TIMESTAMP
        """,
        (local_id, canonical_id, int(row[3])),
    )
    return local_id


def ensure_variant_projection_owner(conn, global_variant_id: str) -> str:
    canonical_id = resolve_redirect_chain(conn, "variant", global_variant_id)
    row = conn.execute(
        """
        SELECT canonical_name, description, unit, value, is_verified, server_revision
        FROM global_variants
        WHERE global_variant_id=? AND lifecycle_state='active'
        """,
        (canonical_id,),
    ).fetchone()
    if row is None:
        raise GlobalMenuIdentityError(f"Unknown active global variant: {canonical_id}")
    owner = _projection_owner(conn, "variant", canonical_id)
    if owner:
        local_name = unique_local_variant_name(
            conn,
            str(row[0]),
            unit=row[2],
            value=row[3],
            identity_key=canonical_id,
            exclude_variant_id=owner,
        )
        conn.execute(
            """
            UPDATE variants
            SET variant_name=?, description=COALESCE(?, description),
                unit=COALESCE(?, unit), value=COALESCE(?, value),
                is_verified=?, updated_at=CURRENT_TIMESTAMP
            WHERE variant_id=?
            """,
            (local_name, row[1], row[2], row[3], 1 if row[4] else 0, owner),
        )
        conn.execute(
            "UPDATE variant_global_links SET is_projection_owner=0 WHERE global_variant_id=?",
            (canonical_id,),
        )
        conn.execute(
            """
            UPDATE variant_global_links
            SET is_projection_owner=1,
                server_revision=MAX(server_revision, ?),
                linked_at=CURRENT_TIMESTAMP
            WHERE local_variant_id=?
            """,
            (int(row[5]), owner),
        )
        return owner
    local_id = generate_deterministic_id("global-variant", canonical_id)
    local_name = unique_local_variant_name(
        conn,
        str(row[0]),
        unit=row[2],
        value=row[3],
        identity_key=canonical_id,
        exclude_variant_id=local_id,
    )
    conn.execute(
        """
        INSERT INTO variants (
            variant_id, variant_name, description, unit, value, is_verified
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(variant_id) DO UPDATE SET
            variant_name=excluded.variant_name,
            description=COALESCE(excluded.description, variants.description),
            unit=COALESCE(excluded.unit, variants.unit),
            value=COALESCE(excluded.value, variants.value),
            is_verified=excluded.is_verified,
            updated_at=CURRENT_TIMESTAMP
        """,
        (local_id, local_name, row[1], row[2], row[3], 1 if row[4] else 0),
    )
    conn.execute(
        "UPDATE variant_global_links SET is_projection_owner=0 WHERE global_variant_id=?",
        (canonical_id,),
    )
    conn.execute(
        """
        INSERT INTO variant_global_links (
            local_variant_id, global_variant_id, provenance,
            server_revision, is_projection_owner
        ) VALUES (?, ?, 'projection', ?, 1)
        ON CONFLICT(local_variant_id) DO UPDATE SET
            global_variant_id=excluded.global_variant_id,
            provenance='projection',
            server_revision=excluded.server_revision,
            is_projection_owner=1,
            linked_at=CURRENT_TIMESTAMP
        """,
        (local_id, canonical_id, int(row[5])),
    )
    return local_id


def unique_local_item_name(
    conn,
    canonical_name: str,
    item_type: str,
    *,
    identity_key: str,
    exclude_menu_item_id: Optional[str] = None,
) -> str:
    """Keep projection owners distinct from same-labelled store-local rows."""

    base = str(canonical_name or "").strip() or "UNKNOWN"

    def available(candidate: str) -> bool:
        row = conn.execute(
            "SELECT menu_item_id FROM menu_items WHERE name=? AND type=?",
            (candidate, item_type),
        ).fetchone()
        return row is None or (
            exclude_menu_item_id is not None
            and str(row[0]) == str(exclude_menu_item_id)
        )

    if available(base):
        return base
    candidate = f"{base} (Global)"
    if available(candidate):
        return candidate
    digest = hashlib.sha256(str(identity_key).encode("utf-8")).hexdigest()[:8]
    return f"{candidate} [{digest}]"


def unique_local_variant_name(
    conn,
    canonical_name: str,
    *,
    unit: Any = None,
    value: Any = None,
    identity_key: str,
    exclude_variant_id: Optional[str] = None,
) -> str:
    """Return a readable name that satisfies the legacy global UNIQUE column.

    The central identity includes name + dimension, so two valid global
    variants may share a canonical label. The local legacy schema cannot. Keep
    the canonical label when it is free and add the dimension only on collision;
    All Stores still displays the canonical metadata from ``global_variants``.
    """

    base = str(canonical_name or "").strip() or "UNKNOWN"

    def available(candidate: str) -> bool:
        row = conn.execute(
            "SELECT variant_id FROM variants WHERE variant_name=?",
            (candidate,),
        ).fetchone()
        return row is None or (
            exclude_variant_id is not None and str(row[0]) == str(exclude_variant_id)
        )

    if available(base):
        return base
    dimension_parts = []
    if value not in (None, ""):
        value_text = str(value)
        if "." in value_text:
            value_text = value_text.rstrip("0").rstrip(".")
        dimension_parts.append(value_text)
    if str(unit or "").strip():
        dimension_parts.append(str(unit).strip().upper())
    suffix = " ".join(dimension_parts) or "distinct"
    candidate = f"{base} ({suffix})"
    if available(candidate):
        return candidate
    digest = hashlib.sha256(str(identity_key).encode("utf-8")).hexdigest()[:8]
    return f"{candidate} [{digest}]"


def plan_local_projection(conn) -> Dict[str, Any]:
    """Return a deterministic, side-effect-free canonical-owner plan.

    Reassignment of order rows is intentionally absent: only a versioned
    server assignment row may move restaurant usage. This planner materializes
    the stable join targets that those assignments and mapping rules use.
    """
    items = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT global_menu_item_id FROM global_menu_items
            WHERE lifecycle_state='active'
            ORDER BY global_menu_item_id
            """
        ).fetchall()
    ]
    variants = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT global_variant_id FROM global_variants
            WHERE lifecycle_state='active'
            ORDER BY global_variant_id
            """
        ).fetchall()
    ]
    return {
        "items": items,
        "variants": variants,
        "catalog_revision": int(
            conn.execute(
                "SELECT catalog_revision FROM global_menu_state WHERE singleton_id=1"
            ).fetchone()[0]
            or 0
        ),
    }


def apply_local_projection_plan(conn, plan: Dict[str, Any]) -> Dict[str, int]:
    items = sorted({str(value) for value in (plan.get("items") or []) if str(value)})
    variants = sorted(
        {str(value) for value in (plan.get("variants") or []) if str(value)}
    )
    for global_id in items:
        ensure_item_projection_owner(conn, global_id)
    for global_id in variants:
        ensure_variant_projection_owner(conn, global_id)
    return {"items_materialized": len(items), "variants_materialized": len(variants)}


def global_ids_for_local(
    conn,
    local_menu_item_id: str,
    local_variant_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    item_row = conn.execute(
        "SELECT global_menu_item_id FROM menu_item_global_links WHERE local_menu_item_id=?",
        (str(local_menu_item_id),),
    ).fetchone()
    item_id = resolve_redirect_chain(conn, "item", str(item_row[0])) if item_row else None
    variant_id = None
    if local_variant_id:
        variant_row = conn.execute(
            "SELECT global_variant_id FROM variant_global_links WHERE local_variant_id=?",
            (str(local_variant_id),),
        ).fetchone()
        variant_id = (
            resolve_redirect_chain(conn, "variant", str(variant_row[0]))
            if variant_row
            else None
        )
    return item_id, variant_id


def _resolution_for_global_ids(
    conn,
    item_id: str,
    variant_id: Optional[str],
    *,
    provenance: str,
    revision: Optional[int],
    materialize_no_variant: bool = False,
) -> GlobalIdentityResolution:
    item_id = resolve_redirect_chain(conn, "item", item_id) or item_id
    variant_id = resolve_redirect_chain(conn, "variant", variant_id)
    local_item_id = ensure_item_projection_owner(conn, item_id)
    if variant_id:
        local_variant_id = ensure_variant_projection_owner(conn, variant_id)
    elif materialize_no_variant:
        local_variant_id = ensure_no_variant_sentinel(conn)
    else:
        local_variant_id = None
    item = conn.execute(
        "SELECT canonical_name, canonical_type FROM global_menu_items WHERE global_menu_item_id=?",
        (item_id,),
    ).fetchone()
    variant = (
        conn.execute(
            "SELECT canonical_name FROM global_variants WHERE global_variant_id=?", (variant_id,)
        ).fetchone()
        if variant_id
        else None
    )
    return GlobalIdentityResolution(
        True,
        local_menu_item_id=local_item_id,
        local_variant_id=local_variant_id,
        global_menu_item_id=item_id,
        global_variant_id=variant_id,
        canonical_name=str(item[0]) if item else None,
        canonical_type=str(item[1]) if item else None,
        canonical_variant_name=str(variant[0]) if variant else None,
        provenance=provenance,
        server_revision=revision,
    )


def _rule_resolution(
    conn,
    *,
    group_id: str,
    locator_scope: str,
    locator_kind: str,
    locator_value: Any,
    restaurant_id: Optional[str] = None,
) -> Optional[GlobalIdentityResolution]:
    normalized = normalize_locator(locator_kind, locator_value)
    if not normalized:
        return None
    if locator_scope == "restaurant":
        row = conn.execute(
            """
            SELECT target_global_menu_item_id, target_global_variant_id, server_revision
            FROM global_menu_mapping_rules
            WHERE menu_group_id=? AND locator_scope='restaurant' AND restaurant_id=?
              AND locator_kind=? AND normalized_locator=? AND lifecycle_state='active'
              AND is_verified=1
            LIMIT 1
            """,
            (group_id, restaurant_id, locator_kind, normalized),
        ).fetchone()
        provenance = "restaurant-pos"
    else:
        row = conn.execute(
            """
            SELECT target_global_menu_item_id, target_global_variant_id, server_revision
            FROM global_menu_mapping_rules
            WHERE menu_group_id=? AND locator_scope='group'
              AND locator_kind=? AND normalized_locator=? AND lifecycle_state='active'
              AND is_verified=1
            LIMIT 1
            """,
            (group_id, locator_kind, normalized),
        ).fetchone()
        if locator_kind in {"pos-item", "pos-addon"}:
            provenance = "group-pos"
        else:
            provenance = "group-itemcode" if locator_kind == "itemcode" else "global-alias"
    if row is None:
        return None
    return _resolution_for_global_ids(
        conn,
        str(row[0]),
        str(row[1]) if row[1] else None,
        provenance=provenance,
        revision=int(row[2]),
        materialize_no_variant=(provenance == "group-pos" and not row[1]),
    )


def resolve_global_identity_for_ingest(
    conn,
    *,
    order_item_id: Any,
    raw_name: Any,
    itemcode: Any = None,
    is_addon: bool = False,
) -> GlobalIdentityResolution:
    capability = resolve_global_menu_capability(conn)
    if (
        not capability.resolution_ready
        or not capability.menu_group_id
        or not capability.restaurant_id
    ):
        return GlobalIdentityResolution(False)

    # Shared Petpooja locators are group authority and intentionally outrank a
    # conflicting restaurant assignment. The complete shared snapshot normally
    # materializes this same row before ingest; this lookup is the fail-closed
    # resolver path for a newly observed line.
    if capability.shared_pos_catalog_advertised:
        shared_pos = _rule_resolution(
            conn,
            group_id=capability.menu_group_id,
            locator_scope="group",
            locator_kind="pos-addon" if is_addon else "pos-item",
            locator_value=order_item_id,
        )
        if shared_pos:
            return shared_pos

    # Existing restaurant-qualified local assignment, but only when it is
    # linked to server-issued global identity.
    assignment = conn.execute(
        """
        SELECT mv.menu_item_id, mv.variant_id, l.global_menu_item_id,
               vl.global_variant_id, MAX(l.server_revision, COALESCE(vl.server_revision, 0))
        FROM menu_item_variants mv
        JOIN menu_item_global_links l ON l.local_menu_item_id=mv.menu_item_id
        LEFT JOIN variant_global_links vl ON vl.local_variant_id=mv.variant_id
        WHERE mv.order_item_id=?
        """,
        (str(order_item_id),),
    ).fetchone()
    if assignment:
        return _resolution_for_global_ids(
            conn,
            str(assignment[2]),
            str(assignment[3]) if assignment[3] else None,
            provenance="restaurant-pos",
            revision=int(assignment[4] or 0),
        )

    # Normal menu groups retain restaurant-qualified POS ownership. Shared-POS
    # groups must never fall back to a cached restaurant-scoped rule.
    if not capability.shared_pos_catalog_advertised:
        pos = _rule_resolution(
            conn,
            group_id=capability.menu_group_id,
            locator_scope="restaurant",
            restaurant_id=capability.restaurant_id,
            locator_kind="pos-addon" if is_addon else "pos-item",
            locator_value=order_item_id,
        )
        if pos:
            return pos

    # 3. Approved group-wide itemcode applies only to regular items.
    if not is_addon:
        code = _rule_resolution(
            conn,
            group_id=capability.menu_group_id,
            locator_scope="group",
            locator_kind="itemcode",
            locator_value=itemcode,
        )
        if code:
            return code

    # 4. Approved normalized alias. Fuzzy similarity never enters this table.
    alias = _rule_resolution(
        conn,
        group_id=capability.menu_group_id,
        locator_scope="group",
        locator_kind="alias",
        locator_value=raw_name,
    )
    return alias or GlobalIdentityResolution(False)


def annotate_global_identity_rows(
    conn,
    rows: Iterable[Dict[str, Any]],
    *,
    item_field: str,
    variant_field: Optional[str] = None,
    allow_federated_read: bool = False,
) -> List[Dict[str, Any]]:
    capability = resolve_global_menu_capability(
        conn, allow_federated_read=allow_federated_read
    )
    annotated: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        item_id, variant_id = global_ids_for_local(
            conn,
            str(row.get(item_field) or ""),
            str(row.get(variant_field) or "") if variant_field and row.get(variant_field) else None,
        )
        if capability.active and item_id:
            item = conn.execute(
                """
                SELECT canonical_name, canonical_type FROM global_menu_items
                WHERE global_menu_item_id=? AND lifecycle_state='active'
                """,
                (item_id,),
            ).fetchone()
            variant = (
                conn.execute(
                    "SELECT canonical_name, unit, value FROM global_variants WHERE global_variant_id=?",
                    (variant_id,),
                ).fetchone()
                if variant_id
                else None
            )
            row.update(
                {
                    "global_menu_item_id": item_id,
                    "global_variant_id": variant_id,
                    "canonical_name": item[0] if item else None,
                    "canonical_type": item[1] if item else None,
                    "canonical_variant_name": variant[0] if variant else None,
                    "canonical_variant_unit": variant[1] if variant else None,
                    "canonical_variant_value": variant[2] if variant else None,
                    "identity_unlinked": False,
                }
            )
        elif capability.active:
            row["identity_unlinked"] = True
        annotated.append(row)
    return annotated
