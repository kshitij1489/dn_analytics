"""
Itemcode -> parent menu item projection (per restaurant).

The POS (Petpooja) itemcode is a human-assigned product code shared by all
size/packaging variants of one product, while each product+variant combination
carries its own Petpooja itemid. itemcode_mappings records, per restaurant,
which analytics parent (menu_items row) currently owns each itemcode so that an
unseen itemid variant can be routed to the right parent without name parsing.

This table is a DERIVED LOCAL PROJECTION, not a source of truth:

- Authoritative cross-install state stays in the central menu catalog and the
  synchronized per-itemid assignments (menu_item_variants).
- The projection is reconstructed from local raw POS order rows joined to those
  assignments (rebuild_itemcode_mappings), so a fresh install, a merge/undo/
  remap, or a force-reseed can always rebuild it.
- It is a local projection, rebuilt from SQLite state, and never leaves the
  machine.

Semantics:

- One itemcode maps to at most one active parent per restaurant.
- Many itemcodes may map to the same parent (UI merges preserve business-level
  consolidation, e.g. Egg + Eggless products reported together).
- A split itemcode (observed under two parents) becomes status='conflict' and
  is never auto-resolved; conflicted codes fall back to the normal parser until
  a human merges/remaps the parents, after which the next rebuild returns the
  code to 'active'.
- A mapping only *auto-routes* an unseen itemid when route_eligible=1. That flag
  is set by rebuild once the parent's ownership is server-backed (some
  menu_item_variants row for the parent carries assignment_seq or
  verification_seq, i.e. the server acknowledged it). first_seen observations
  record the row route_eligible=0, so purely local ingest never crowns a
  fleet-wide parent for a brand-new itemid before a sync/flush establishes it on
  the server (plan Phase 9.2). Conflicted codes are never route-eligible.

None of these helpers commit or roll back; the caller owns the transaction.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def ensure_itemcode_mapping_schema(conn) -> None:
    """Create the projection table if missing. Idempotent; safe every call.

    Needed because the standalone order-loader schema guard checks only a
    subset of table names and can skip the canonical schema script on an
    existing database. The canonical CREATE TABLE also lives in
    database/schema_sqlite.sql for fresh databases.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS itemcode_mappings (
            restaurant_id INTEGER NOT NULL
                REFERENCES restaurants(restaurant_id) ON DELETE CASCADE,
            itemcode TEXT NOT NULL,
            menu_item_id TEXT
                REFERENCES menu_items(menu_item_id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'conflict')),
            source TEXT NOT NULL DEFAULT 'first_seen'
                CHECK (source IN ('first_seen', 'rebuild')),
            evidence_count INTEGER NOT NULL DEFAULT 1
                CHECK (evidence_count >= 0),
            conflict_menu_item_ids TEXT,
            route_eligible INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (restaurant_id, itemcode),
            CHECK (
                (status = 'active' AND menu_item_id IS NOT NULL AND conflict_menu_item_ids IS NULL)
                OR
                (status = 'conflict' AND menu_item_id IS NULL AND conflict_menu_item_ids IS NOT NULL)
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_itemcode_mappings_menu_item
        ON itemcode_mappings(menu_item_id)
        """
    )
    # route_eligible (plan Phase 9.2) is additive on databases whose table
    # predates the auto-route gate; CREATE TABLE IF NOT EXISTS never alters an
    # existing table, so backfill the column explicitly. Constant DEFAULT 0 is a
    # legal SQLite ADD COLUMN default, and 0 is the safe value (a pre-gate row is
    # not route-eligible until the next rebuild re-evaluates it).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(itemcode_mappings)").fetchall()}
    if "route_eligible" not in cols:
        conn.execute(
            "ALTER TABLE itemcode_mappings ADD COLUMN route_eligible INTEGER NOT NULL DEFAULT 0"
        )


def normalize_itemcode(value: Any) -> Optional[str]:
    """Stringify and trim surrounding whitespace; collapse null/blank to None.

    Case is preserved: itemcodes are human-assigned identifiers and must not be
    silently case-folded.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _conflict_json(menu_item_ids) -> str:
    """Sorted JSON array for deterministic diagnostics."""
    return json.dumps(sorted({str(m) for m in menu_item_ids}))


def get_active_itemcode_mapping(
    conn, restaurant_id, itemcode, cursor=None
) -> Optional[str]:
    """Return the auto-routable parent menu_item_id for this code, or None.

    "Routable" is stricter than "active": the mapping must also be
    route_eligible=1, i.e. rebuild has confirmed the parent's ownership is
    server-backed. A purely local first_seen mapping is active but not yet
    eligible, so this returns None for it and ingest falls through to the parser
    (plan Phase 9.2).
    """
    code = normalize_itemcode(itemcode)
    if code is None or restaurant_id is None:
        return None
    # Once the server advertises global_menu_v1, this local derived projection
    # is no longer allowed to crown a cross-store identity. The clustering
    # service resolves approved group itemcode rules through the canonical
    # global resolver before reaching this legacy helper.
    try:
        from src.core.global_menu_schema import resolve_global_menu_capability

        if resolve_global_menu_capability(conn).resolution_ready:
            return None
    except Exception:
        # Partial focused-test schemas and standalone tooling retain the exact
        # legacy lookup behavior.
        pass
    own_cursor = cursor is None
    cur = conn.cursor() if own_cursor else cursor
    try:
        cur.execute(
            """
            SELECT menu_item_id FROM itemcode_mappings
            WHERE restaurant_id = ? AND itemcode = ?
              AND status = 'active' AND route_eligible = 1
            """,
            (int(restaurant_id), code),
        )
        row = cur.fetchone()
        return str(row[0]) if row and row[0] is not None else None
    finally:
        if own_cursor:
            cur.close()


def observe_itemcode_assignment(
    conn, restaurant_id, itemcode, menu_item_id, cursor=None
) -> str:
    """
    Teach the projection from the final parent selected for one regular item.

    Returns one of:
      'ignored'   — blank/absent code, restaurant, or parent; nothing recorded.
      'created'   — first observation; active row inserted.
      'confirmed' — active row already points at this parent; evidence bumped.
      'conflict'  — contradictory parent; row is (now) in conflict state.

    Never chooses a winner between contradictory parents; the merge/remap UI is
    the human decision point. Does NOT commit; the caller owns the transaction.
    """
    code = normalize_itemcode(itemcode)
    if code is None or restaurant_id is None or menu_item_id is None:
        return "ignored"
    rid = int(restaurant_id)
    mid = str(menu_item_id)

    own_cursor = cursor is None
    cur = conn.cursor() if own_cursor else cursor
    try:
        cur.execute(
            """
            SELECT status, menu_item_id, conflict_menu_item_ids
            FROM itemcode_mappings
            WHERE restaurant_id = ? AND itemcode = ?
            """,
            (rid, code),
        )
        row = cur.fetchone()

        if row is None:
            cur.execute(
                """
                INSERT INTO itemcode_mappings (
                    restaurant_id, itemcode, menu_item_id, status, source, evidence_count
                ) VALUES (?, ?, ?, 'active', 'first_seen', 1)
                """,
                (rid, code, mid),
            )
            return "created"

        status, current_mid, conflict_json = row[0], row[1], row[2]

        if status == "active":
            if str(current_mid) == mid:
                cur.execute(
                    """
                    UPDATE itemcode_mappings
                    SET evidence_count = evidence_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE restaurant_id = ? AND itemcode = ?
                    """,
                    (rid, code),
                )
                return "confirmed"
            cur.execute(
                """
                UPDATE itemcode_mappings
                SET status = 'conflict',
                    menu_item_id = NULL,
                    conflict_menu_item_ids = ?,
                    route_eligible = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE restaurant_id = ? AND itemcode = ?
                """,
                (_conflict_json([current_mid, mid]), rid, code),
            )
            logger.warning(
                "itemcode %r split across parents %s and %s (restaurant %s); marked conflict",
                code, current_mid, mid, rid,
            )
            return "conflict"

        # Already conflicted: union the observed parent into the candidate list.
        try:
            candidates = set(json.loads(conflict_json) if conflict_json else [])
        except (TypeError, ValueError):
            candidates = set()
        candidates.add(mid)
        cur.execute(
            """
            UPDATE itemcode_mappings
            SET conflict_menu_item_ids = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE restaurant_id = ? AND itemcode = ?
            """,
            (_conflict_json(candidates), rid, code),
        )
        return "conflict"
    finally:
        if own_cursor:
            cur.close()


def rebuild_itemcode_mappings_best_effort(
    conn, restaurant_id=None, cursor=None
) -> Optional["ItemcodeRebuildResult"]:
    """
    Lifecycle-hook variant of rebuild_itemcode_mappings: never raises.

    Assignment-changing paths (merge/undo/remap/resolution, assignment pulls,
    bootstrap, Sync DB) must not fail because this derived projection could not
    be refreshed — it is rebuildable at the next sync, and stale rows pointing
    at deleted parents are removed by the FK cascade. Focused tests also build
    partial schemas without the POS order columns this rebuild reads.

    Projection writes run inside a savepoint so a mid-rebuild failure leaves
    the caller's transaction (and the projection) exactly as it was. Does NOT
    commit; the caller owns the transaction.
    """
    own_cursor = cursor is None
    cur = conn.cursor() if own_cursor else cursor
    try:
        cur.execute("SAVEPOINT itemcode_rebuild")
        try:
            result = rebuild_itemcode_mappings(conn, restaurant_id=restaurant_id, cursor=cur)
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT itemcode_rebuild")
            cur.execute("RELEASE SAVEPOINT itemcode_rebuild")
            logger.warning("itemcode projection rebuild skipped", exc_info=True)
            return None
        cur.execute("RELEASE SAVEPOINT itemcode_rebuild")
        return result
    finally:
        if own_cursor:
            cur.close()


@dataclass
class ItemcodeRebuildResult:
    restaurants_scanned: int = 0
    codes_scanned: int = 0
    active_written: int = 0
    conflicts_written: int = 0
    stale_removed: int = 0
    # [{restaurant_id, itemcode, menu_item_ids}] for logs/tests
    conflicts: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"restaurants={self.restaurants_scanned} codes={self.codes_scanned} "
            f"active={self.active_written} conflicts={self.conflicts_written} "
            f"stale_removed={self.stale_removed}"
        )


def rebuild_itemcode_mappings(
    conn, restaurant_id=None, cursor=None
) -> ItemcodeRebuildResult:
    """
    Reconstruct the projection from raw POS order rows joined to the current
    per-itemid assignments. menu_item_variants is the assignment authority; an
    old order_items.menu_item_id is never trusted, and rows without a current
    assignment are ignored.

    Per (restaurant_id, normalized itemcode):
      exactly one distinct current parent -> active row (source='rebuild');
      more than one distinct current parent -> conflict row with all candidates;
      no usable assignment -> no row.

    evidence_count is the number of distinct Petpooja itemids supporting the
    row, not the number of historical order lines, so replays and popular
    products do not distort it.

    Replaces rows only within the rebuilt scope (one restaurant, or all when
    restaurant_id is None): upserts desired state, then deletes stale rows.
    Does NOT commit; the caller owns the transaction.
    """
    result = ItemcodeRebuildResult()
    own_cursor = cursor is None
    cur = conn.cursor() if own_cursor else cursor
    try:
        ensure_itemcode_mapping_schema(conn)

        # Route-eligibility evidence: a parent is server-backed when any of its
        # assignment rows carries a server sequence (merge or verification
        # stream). Only such parents may auto-route an unseen sibling itemid;
        # a purely local first_seen/C1 parent stays route_eligible=0 until a
        # flush/verify round-trip stamps a seq (plan Phase 9.2). Built from
        # whichever seq columns the local schema actually has so focused test
        # schemas (and any pre-assignment-sync DB) degrade to "not backed"
        # rather than raising.
        mv_cols = {
            row[1] for row in cur.execute("PRAGMA table_info(menu_item_variants)").fetchall()
        }
        seq_cols = [c for c in ("assignment_seq", "verification_seq") if c in mv_cols]
        server_backed: set = set()
        if seq_cols:
            cond = " OR ".join(f"{c} IS NOT NULL" for c in seq_cols)
            cur.execute(
                f"""
                SELECT DISTINCT menu_item_id FROM menu_item_variants
                WHERE menu_item_id IS NOT NULL AND ({cond})
                """
            )
            server_backed = {str(r[0]) for r in cur.fetchall()}

        params: List[Any] = []
        scope_sql = ""
        if restaurant_id is not None:
            scope_sql = "AND o.restaurant_id = ?"
            params.append(int(restaurant_id))
        cur.execute(
            f"""
            SELECT o.restaurant_id,
                   oi.itemcode,
                   CAST(oi.petpooja_itemid AS TEXT) AS itemid,
                   mv.menu_item_id
            FROM order_items oi
            JOIN orders o ON o.order_id = oi.order_id
            JOIN menu_item_variants mv
              ON CAST(oi.petpooja_itemid AS TEXT) = mv.order_item_id
            WHERE oi.itemcode IS NOT NULL
              AND o.restaurant_id IS NOT NULL
              {scope_sql}
            """,
            params,
        )

        # (restaurant_id, code) -> {menu_item_id -> set(itemids)}
        observed: Dict[tuple, Dict[str, set]] = {}
        for row in cur.fetchall():
            code = normalize_itemcode(row[1])
            if code is None or row[3] is None:
                continue
            key = (int(row[0]), code)
            parents = observed.setdefault(key, {})
            parents.setdefault(str(row[3]), set()).add(str(row[2]))

        result.codes_scanned = len(observed)
        result.restaurants_scanned = len({rid for rid, _ in observed})

        desired_keys = set()
        for (rid, code), parents in sorted(observed.items()):
            desired_keys.add((rid, code))
            evidence = len({iid for iids in parents.values() for iid in iids})
            if len(parents) == 1:
                (mid,) = parents.keys()
                route_eligible = 1 if mid in server_backed else 0
                cur.execute(
                    """
                    INSERT INTO itemcode_mappings (
                        restaurant_id, itemcode, menu_item_id, status, source,
                        evidence_count, conflict_menu_item_ids, route_eligible
                    ) VALUES (?, ?, ?, 'active', 'rebuild', ?, NULL, ?)
                    ON CONFLICT(restaurant_id, itemcode) DO UPDATE SET
                        menu_item_id = excluded.menu_item_id,
                        status = 'active',
                        source = 'rebuild',
                        evidence_count = excluded.evidence_count,
                        conflict_menu_item_ids = NULL,
                        route_eligible = excluded.route_eligible,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (rid, code, mid, evidence, route_eligible),
                )
                result.active_written += 1
            else:
                conflict = _conflict_json(parents.keys())
                cur.execute(
                    """
                    INSERT INTO itemcode_mappings (
                        restaurant_id, itemcode, menu_item_id, status, source,
                        evidence_count, conflict_menu_item_ids, route_eligible
                    ) VALUES (?, ?, NULL, 'conflict', 'rebuild', ?, ?, 0)
                    ON CONFLICT(restaurant_id, itemcode) DO UPDATE SET
                        menu_item_id = NULL,
                        status = 'conflict',
                        source = 'rebuild',
                        evidence_count = excluded.evidence_count,
                        conflict_menu_item_ids = excluded.conflict_menu_item_ids,
                        route_eligible = 0,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (rid, code, evidence, conflict),
                )
                result.conflicts_written += 1
                result.conflicts.append(
                    {
                        "restaurant_id": rid,
                        "itemcode": code,
                        "menu_item_ids": sorted(parents.keys()),
                    }
                )

        # Remove stale projection rows within the rebuilt scope.
        stale_params: List[Any] = []
        stale_scope = ""
        if restaurant_id is not None:
            stale_scope = "WHERE restaurant_id = ?"
            stale_params.append(int(restaurant_id))
        cur.execute(
            f"SELECT restaurant_id, itemcode FROM itemcode_mappings {stale_scope}",
            stale_params,
        )
        existing = [(int(r[0]), str(r[1])) for r in cur.fetchall()]
        for rid, code in existing:
            if (rid, code) not in desired_keys:
                cur.execute(
                    "DELETE FROM itemcode_mappings WHERE restaurant_id = ? AND itemcode = ?",
                    (rid, code),
                )
                result.stale_removed += 1

        if result.conflicts:
            for detail in result.conflicts:
                logger.warning(
                    "itemcode rebuild: %r (restaurant %s) split across %s",
                    detail["itemcode"], detail["restaurant_id"], detail["menu_item_ids"],
                )
        logger.info("itemcode rebuild: %s", result.summary())
        return result
    finally:
        if own_cursor:
            cur.close()
