"""Deterministic revision-1.7 global-menu rebuild diagnostics."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Sequence

from src.core.global_menu_schema import resolve_global_menu_capability


def _rows(conn, sql: str, params: Sequence[Any]) -> list[Dict[str, Any]]:
    cursor = conn.execute(sql, params)
    columns = [str(column[0]) for column in cursor.description or ()]
    return [
        {columns[index]: row[index] for index in range(len(columns))}
        for row in cursor.fetchall()
    ]


def _digest(sections: Iterable[Any]) -> str:
    encoded = json.dumps(
        list(sections),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fetch_global_menu_diagnostics(conn) -> Dict[str, Any]:
    """Expose the stop-gate/exit-gate facts for one physical profile cache."""
    capability = resolve_global_menu_capability(conn, allow_profile_sync=True)
    state_cursor = conn.execute(
        "SELECT * FROM global_menu_state WHERE singleton_id=1"
    )
    state = state_cursor.fetchone()
    state_columns = [str(column[0]) for column in state_cursor.description or ()]
    state_values = (
        {state_columns[index]: state[index] for index in range(len(state_columns))}
        if state is not None
        else {}
    )
    menu_group_id = (
        str(state_values.get("menu_group_id") or capability.menu_group_id or "").strip()
        or None
    )

    if menu_group_id is None:
        mapping_count = price_count = history_count = 0
        catalog_sections: list[Any] = []
        matrix_rows: list[Any] = []
        history_rows: list[Any] = []
    else:
        mapping_count, price_count = conn.execute(
            """
            SELECT COUNT(*), COUNT(price)
            FROM global_menu_mapping_rules
            WHERE menu_group_id=?
              AND lifecycle_state='active'
            """,
            (menu_group_id,),
        ).fetchone()
        history_count = conn.execute(
            "SELECT COUNT(*) FROM global_menu_history WHERE menu_group_id=?",
            (menu_group_id,),
        ).fetchone()[0]
        catalog_sections = [
            _rows(
                conn,
                """
                SELECT global_menu_item_id, canonical_name, canonical_type,
                       is_verified, lifecycle_state, server_revision
                FROM global_menu_items
                WHERE menu_group_id=? AND lifecycle_state='active'
                ORDER BY global_menu_item_id
                """,
                (menu_group_id,),
            ),
            _rows(
                conn,
                """
                SELECT global_variant_id, canonical_name, description, unit,
                       CAST(value AS TEXT) AS value, is_verified, lifecycle_state,
                       server_revision
                FROM global_variants
                WHERE menu_group_id=? AND lifecycle_state='active'
                ORDER BY global_variant_id
                """,
                (menu_group_id,),
            ),
            _rows(
                conn,
                """
                SELECT redirect_id, entity_type, source_global_menu_item_id,
                       target_global_menu_item_id, source_global_variant_id,
                       target_global_variant_id, server_revision
                FROM global_menu_redirects
                WHERE menu_group_id=?
                ORDER BY redirect_id
                """,
                (menu_group_id,),
            ),
        ]
        matrix_rows = _rows(
            conn,
            """
            SELECT rule_id, locator_scope, restaurant_id, locator_kind,
                   normalized_locator, target_global_menu_item_id,
                   target_global_variant_id,
                   provenance, is_verified, server_revision
            FROM global_menu_mapping_rules
            WHERE menu_group_id=?
              AND lifecycle_state='active'
            ORDER BY rule_id
            """,
            (menu_group_id,),
        )
        history_rows = _rows(
            conn,
            """
            SELECT history_id, source_event_id, source_kind, event_type,
                   origin_restaurant_id, actor, attribution, occurred_at,
                   server_ingested_at, source, target, mutation_id,
                   is_undoable, detail
            FROM global_menu_history
            WHERE menu_group_id=?
            ORDER BY occurred_at DESC, source_kind ASC, source_event_id DESC
            """,
            (menu_group_id,),
        )

    linked = int(state_values.get("coverage_linked") or 0)
    total = int(state_values.get("coverage_total") or 0)
    quarantine_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM global_menu_sync_quarantine WHERE resolved_at IS NULL"
        ).fetchone()[0]
        or 0
    )
    bootstrap_status = str(state_values.get("bootstrap_status") or "not_started")
    return {
        "menu_group_id": menu_group_id,
        "bootstrap_state": bootstrap_status,
        "catalog_revision": int(state_values.get("catalog_revision") or 0),
        "mapping_count": int(mapping_count),
        "price_count": int(price_count),
        "assignment_coverage": {
            "linked": linked,
            "total": total,
            "complete": bootstrap_status == "complete" and linked == total,
        },
        "history_count": int(history_count),
        "history_cursor": state_values.get("history_cursor"),
        "quarantine_count": quarantine_count,
        "catalog_digest": _digest(catalog_sections),
        "matrix_digest": _digest(matrix_rows),
        "history_digest": _digest(history_rows),
    }
