"""Helpers for translating synced assignment keys to local POS rows."""

from __future__ import annotations

import re
from typing import Any, List

from utils.id_generator import generate_deterministic_id


def normalized_generated_name_key(name_raw: Any) -> str | None:
    text = str(name_raw or "").strip()
    if not text:
        return None
    normalized_name = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    if not normalized_name:
        return None
    return generate_deterministic_id(f"generated_{normalized_name}")


def _table_columns(conn, table_name: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    except Exception:
        return set()


class AssignmentKeyIndex:
    """
    Lazily built key → order_items PK map for batch lookups.

    Resolving a name-derived key otherwise costs a full order_items scan with
    one uuid5 per name_raw; done per key across a flush batch or pull page that
    multiplies into tens of millions of hash calls. This index pays the scan
    once (on first lookup) and serves every key in the batch from memory. Scoped
    to one batch/transaction: it does not see order_items rows inserted after
    the first lookup.
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self._by_key: dict[str, list[int]] | None = None
        self._addons_by_key: dict[str, list[int]] | None = None

    def _build(self) -> dict[str, list[int]]:
        columns = _table_columns(self._conn, "order_items")
        by_key: dict[str, list[int]] = {}
        if not columns:
            return by_key

        def note(key: str | None, pk: int) -> None:
            if not key:
                return
            pks = by_key.setdefault(key, [])
            if pk not in pks:
                pks.append(pk)

        select_columns = ["order_item_id"]
        has_petpooja = "petpooja_itemid" in columns
        has_name = "name_raw" in columns
        if has_petpooja:
            select_columns.append("petpooja_itemid")
        if has_name:
            select_columns.append("name_raw")
        for row in self._conn.execute(
            f"SELECT {', '.join(select_columns)} FROM order_items"
        ).fetchall():
            pk = int(row[0])
            if has_petpooja and row[1] is not None:
                note(str(row[1]).strip(), pk)
            if has_name:
                name_raw = row[2] if has_petpooja else row[1]
                if name_raw is not None:
                    note(normalized_generated_name_key(name_raw), pk)
        return by_key

    def _build_addons(self) -> dict[str, list[int]]:
        columns = _table_columns(self._conn, "order_item_addons")
        by_key: dict[str, list[int]] = {}
        if not columns:
            return by_key

        def note(key: str | None, pk: int) -> None:
            if not key:
                return
            pks = by_key.setdefault(key, [])
            if pk not in pks:
                pks.append(pk)

        select_columns = ["order_item_addon_id"]
        has_petpooja = "petpooja_addonid" in columns
        has_name = "name_raw" in columns
        if has_petpooja:
            select_columns.append("petpooja_addonid")
        if has_name:
            select_columns.append("name_raw")
        for row in self._conn.execute(
            f"SELECT {', '.join(select_columns)} FROM order_item_addons"
        ).fetchall():
            pk = int(row[0])
            if has_petpooja and row[1] is not None and str(row[1]).strip():
                note(str(row[1]).strip(), pk)
            if has_name:
                name_raw = row[2] if has_petpooja else row[1]
                if name_raw is not None:
                    note(normalized_generated_name_key(name_raw), pk)
        return by_key

    def pks_for_key(self, order_item_key: Any) -> List[int]:
        key = str(order_item_key or "").strip()
        if not key:
            return []
        if self._by_key is None:
            self._by_key = self._build()
        return list(self._by_key.get(key, ()))

    def addon_pks_for_key(self, order_item_key: Any) -> List[int]:
        key = str(order_item_key or "").strip()
        if not key:
            return []
        if self._addons_by_key is None:
            self._addons_by_key = self._build_addons()
        return list(self._addons_by_key.get(key, ()))


def local_order_item_pks_for_assignment_key(
    conn,
    order_item_key: Any,
    *,
    key_index: AssignmentKeyIndex | None = None,
) -> List[int]:
    """
    Return local order_items PKs backed by this synced assignment key.

    The synced key is the POS itemid as text, or uuid5("generated_"+normalized
    name) when POS itemid is absent. It is not the local autoincrement PK.

    Pass a shared AssignmentKeyIndex when resolving many keys in one batch —
    the fallback path below rescans order_items per key.
    """
    key = str(order_item_key or "").strip()
    if not key:
        return []

    columns = _table_columns(conn, "order_items")
    if not columns:
        return []

    pks: list[int] = []
    seen: set[int] = set()

    if key_index is not None:
        for pk in key_index.pks_for_key(key):
            if pk not in seen:
                seen.add(pk)
                pks.append(pk)
    else:
        if "petpooja_itemid" in columns:
            for row in conn.execute(
                """
                SELECT order_item_id
                FROM order_items
                WHERE petpooja_itemid IS NOT NULL
                  AND CAST(petpooja_itemid AS TEXT) = ?
                """,
                (key,),
            ).fetchall():
                pk = int(row[0])
                if pk not in seen:
                    seen.add(pk)
                    pks.append(pk)

        if "name_raw" in columns:
            for row in conn.execute(
                "SELECT order_item_id, name_raw FROM order_items WHERE name_raw IS NOT NULL"
            ).fetchall():
                generated_key = normalized_generated_name_key(row[1])
                if generated_key == key:
                    pk = int(row[0])
                    if pk not in seen:
                        seen.add(pk)
                        pks.append(pk)

    # Tiny unit-test schemas may predate the POS-key columns. Production has at
    # least petpooja_itemid/name_raw, so this fallback cannot mask numeric POS
    # itemids that collide with unrelated local PKs.
    if not pks and "petpooja_itemid" not in columns:
        row = conn.execute(
            "SELECT order_item_id FROM order_items WHERE CAST(order_item_id AS TEXT) = ?",
            (key,),
        ).fetchone()
        if row is not None:
            pks.append(int(row[0]))

    return pks


def local_addon_pks_for_assignment_key(
    conn,
    order_item_key: Any,
    *,
    key_index: AssignmentKeyIndex | None = None,
) -> List[int]:
    """
    Return order_item_addons PKs whose OWN identity matches this synced key.

    An addon's identity is its POS addonid as text, or uuid5("generated_"+
    normalized name) when the addonid is absent — the same key the clustering
    path hands to menu_item_variants. Membership in a parent order line is
    deliberately NOT a match: a combo line's addons are separate products.
    """
    key = str(order_item_key or "").strip()
    if not key:
        return []

    if key_index is not None:
        return key_index.addon_pks_for_key(key)

    columns = _table_columns(conn, "order_item_addons")
    if not columns:
        return []

    pks: list[int] = []
    seen: set[int] = set()

    if "petpooja_addonid" in columns:
        for row in conn.execute(
            """
            SELECT order_item_addon_id
            FROM order_item_addons
            WHERE petpooja_addonid IS NOT NULL
              AND TRIM(CAST(petpooja_addonid AS TEXT)) = ?
            """,
            (key,),
        ).fetchall():
            pk = int(row[0])
            if pk not in seen:
                seen.add(pk)
                pks.append(pk)

    if "name_raw" in columns:
        for row in conn.execute(
            "SELECT order_item_addon_id, name_raw FROM order_item_addons WHERE name_raw IS NOT NULL"
        ).fetchall():
            generated_key = normalized_generated_name_key(row[1])
            if generated_key == key:
                pk = int(row[0])
                if pk not in seen:
                    seen.add(pk)
                    pks.append(pk)

    return pks


def has_local_pos_backing(
    conn,
    order_item_key: Any,
    *,
    key_index: AssignmentKeyIndex | None = None,
) -> bool:
    if local_order_item_pks_for_assignment_key(conn, order_item_key, key_index=key_index):
        return True
    return bool(
        local_addon_pks_for_assignment_key(conn, order_item_key, key_index=key_index)
    )


def update_local_order_rows_for_assignment_key(
    conn,
    order_item_key: Any,
    *,
    menu_item_id: str,
    variant_id: Any = None,
    variant_specified: bool = False,
    key_index: AssignmentKeyIndex | None = None,
) -> None:
    pks = local_order_item_pks_for_assignment_key(conn, order_item_key, key_index=key_index)
    addon_pks = local_addon_pks_for_assignment_key(conn, order_item_key, key_index=key_index)
    if not pks and not addon_pks:
        return

    order_columns = _table_columns(conn, "order_items")
    timestamp_sql = ", updated_at = CURRENT_TIMESTAMP" if "updated_at" in order_columns else ""
    if pks:
        placeholders = ", ".join("?" for _ in pks)
        if variant_specified:
            conn.execute(
                f"""
                UPDATE order_items
                SET menu_item_id = ?, variant_id = ?{timestamp_sql}
                WHERE order_item_id IN ({placeholders})
                """,
                [menu_item_id, variant_id, *pks],
            )
        else:
            conn.execute(
                f"""
                UPDATE order_items
                SET menu_item_id = ?{timestamp_sql}
                WHERE order_item_id IN ({placeholders})
                """,
                [menu_item_id, *pks],
            )
    if addon_pks:
        addon_placeholders = ", ".join("?" for _ in addon_pks)
        if variant_specified:
            conn.execute(
                f"""
                UPDATE order_item_addons
                SET menu_item_id = ?, variant_id = ?
                WHERE order_item_addon_id IN ({addon_placeholders})
                """,
                [menu_item_id, variant_id, *addon_pks],
            )
        else:
            conn.execute(
                f"""
                UPDATE order_item_addons
                SET menu_item_id = ?
                WHERE order_item_addon_id IN ({addon_placeholders})
                """,
                [menu_item_id, *addon_pks],
            )
