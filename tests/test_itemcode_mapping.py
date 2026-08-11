"""
Tests for the itemcode -> parent projection (src.core.itemcode_mapping).

Covers normalization, incremental observation during ingest (evidence,
restaurant scoping, deterministic conflict JSON), the full rebuild (authority
of menu_item_variants, idempotence, stale-row removal, conflict resolution via
merge-shaped assignment updates), FK cascade, and the no-commit contract.
"""

import json
import sqlite3
import unittest

from src.core.itemcode_mapping import (
    ItemcodeRebuildResult,
    ensure_itemcode_mapping_schema,
    get_active_itemcode_mapping,
    normalize_itemcode,
    observe_itemcode_assignment,
    rebuild_itemcode_mappings,
)


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    # Minimal slices of the real schema; only the columns the module touches.
    conn.executescript(
        """
        CREATE TABLE restaurants (restaurant_id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE menu_items (menu_item_id TEXT PRIMARY KEY, name TEXT, type TEXT);
        CREATE TABLE variants (variant_id TEXT PRIMARY KEY, variant_name TEXT);
        CREATE TABLE menu_item_variants (
            order_item_id TEXT PRIMARY KEY,
            menu_item_id TEXT NOT NULL REFERENCES menu_items(menu_item_id),
            variant_id TEXT,
            is_verified INTEGER DEFAULT 0,
            assignment_seq INTEGER,
            verification_seq INTEGER
        );
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER REFERENCES restaurants(restaurant_id)
        );
        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(order_id),
            menu_item_id TEXT,
            petpooja_itemid INTEGER,
            itemcode TEXT,
            name_raw TEXT
        );
        """
    )
    ensure_itemcode_mapping_schema(conn)
    conn.execute("INSERT INTO restaurants (restaurant_id, name) VALUES (1, 'R1')")
    conn.execute("INSERT INTO restaurants (restaurant_id, name) VALUES (2, 'R2')")
    for mid in ("m1", "m2", "m3"):
        conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type) VALUES (?, ?, 'item')",
            (mid, mid.upper()),
        )
    conn.commit()
    return conn


class TestNormalizeItemcode(unittest.TestCase):
    def test_null_and_blank_collapse_to_none(self):
        self.assertIsNone(normalize_itemcode(None))
        self.assertIsNone(normalize_itemcode(""))
        self.assertIsNone(normalize_itemcode("   "))
        self.assertIsNone(normalize_itemcode("\t\n"))

    def test_numeric_like_values_stringified(self):
        self.assertEqual(normalize_itemcode(123), "123")
        self.assertEqual(normalize_itemcode(0), "0")

    def test_surrounding_whitespace_trimmed(self):
        self.assertEqual(normalize_itemcode("  CHOCO70ICE  "), "CHOCO70ICE")

    def test_case_preserved(self):
        self.assertEqual(normalize_itemcode("EgglessBanoffee"), "EgglessBanoffee")
        self.assertNotEqual(normalize_itemcode("water"), normalize_itemcode("Water"))


class TestObserve(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _row(self, rid, code):
        return self.conn.execute(
            """
            SELECT status, menu_item_id, evidence_count, source, conflict_menu_item_ids
            FROM itemcode_mappings WHERE restaurant_id = ? AND itemcode = ?
            """,
            (rid, code),
        ).fetchone()

    def test_blank_code_or_missing_parts_ignored(self):
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, None, "m1"), "ignored")
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, "   ", "m1"), "ignored")
        self.assertEqual(observe_itemcode_assignment(self.conn, None, "C1", "m1"), "ignored")
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, "C1", None), "ignored")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM itemcode_mappings").fetchone()[0], 0
        )

    def test_first_observation_creates_active(self):
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, " C1 ", "m1"), "created")
        status, mid, evidence, source, conflict = self._row(1, "C1")
        self.assertEqual((status, mid, evidence, source), ("active", "m1", 1, "first_seen"))
        self.assertIsNone(conflict)
        # Active but not yet route-eligible: a first_seen row must not auto-route
        # until a rebuild confirms the parent is server-backed (Phase 9.2).
        self.assertEqual(
            self.conn.execute(
                "SELECT route_eligible FROM itemcode_mappings WHERE restaurant_id=1 AND itemcode='C1'"
            ).fetchone()[0],
            0,
        )
        self.assertIsNone(get_active_itemcode_mapping(self.conn, 1, "C1"))

    def test_same_triple_increments_evidence(self):
        observe_itemcode_assignment(self.conn, 1, "C1", "m1")
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, "C1", "m1"), "confirmed")
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, "C1", "m1"), "confirmed")
        self.assertEqual(self._row(1, "C1")[2], 3)

    def test_same_code_different_restaurants_independent(self):
        observe_itemcode_assignment(self.conn, 1, "C1", "m1")
        observe_itemcode_assignment(self.conn, 2, "C1", "m2")
        # Independent active rows per restaurant (first_seen, not yet routable).
        self.assertEqual(self._row(1, "C1")[:2], ("active", "m1"))
        self.assertEqual(self._row(2, "C1")[:2], ("active", "m2"))

    def test_different_codes_same_parent(self):
        observe_itemcode_assignment(self.conn, 1, "EgglessBanoffee", "m1")
        observe_itemcode_assignment(self.conn, 1, "BANICE", "m1")
        # Both codes learn parent m1 (first_seen); routability waits for rebuild.
        self.assertEqual(self._row(1, "EgglessBanoffee")[:2], ("active", "m1"))
        self.assertEqual(self._row(1, "BANICE")[:2], ("active", "m1"))

    def test_contradiction_creates_sorted_conflict(self):
        observe_itemcode_assignment(self.conn, 1, "C1", "m2")
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, "C1", "m1"), "conflict")
        status, mid, _, _, conflict = self._row(1, "C1")
        self.assertEqual(status, "conflict")
        self.assertIsNone(mid)
        self.assertEqual(json.loads(conflict), ["m1", "m2"])  # sorted, deterministic
        self.assertIsNone(get_active_itemcode_mapping(self.conn, 1, "C1"))

    def test_further_contradictions_union_candidates(self):
        observe_itemcode_assignment(self.conn, 1, "C1", "m2")
        observe_itemcode_assignment(self.conn, 1, "C1", "m1")
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, "C1", "m3"), "conflict")
        self.assertEqual(observe_itemcode_assignment(self.conn, 1, "C1", "m1"), "conflict")
        self.assertEqual(json.loads(self._row(1, "C1")[4]), ["m1", "m2", "m3"])

    def test_no_commit_inside_helper(self):
        # The helper must leave transaction ownership to the caller: an explicit
        # rollback after observing must erase the observation.
        self.conn.execute("BEGIN")
        observe_itemcode_assignment(self.conn, 1, "C1", "m1")
        self.conn.rollback()
        self.assertIsNone(self._row(1, "C1"))


class TestRebuild(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()
        self._next_itemid = 1000

    def tearDown(self):
        self.conn.close()

    def _order(self, restaurant_id=1):
        cur = self.conn.execute(
            "INSERT INTO orders (restaurant_id) VALUES (?)", (restaurant_id,)
        )
        return cur.lastrowid

    def _line(self, order_id, itemcode, itemid=None, menu_item_id=None):
        if itemid is None:
            self._next_itemid += 1
            itemid = self._next_itemid
        self.conn.execute(
            """
            INSERT INTO order_items (order_id, menu_item_id, petpooja_itemid, itemcode, name_raw)
            VALUES (?, ?, ?, ?, 'raw')
            """,
            (order_id, menu_item_id, itemid, itemcode),
        )
        return itemid

    def _assign(self, itemid, menu_item_id, backed=True):
        # backed=True stamps assignment_seq so the parent counts as
        # server-backed and its rebuilt mapping is route_eligible; backed=False
        # simulates a purely local (unsynced) assignment.
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, assignment_seq)
            VALUES (?, ?, 'v1', ?)
            ON CONFLICT(order_item_id) DO UPDATE SET
                menu_item_id = excluded.menu_item_id,
                assignment_seq = excluded.assignment_seq
            """,
            (str(itemid), menu_item_id, 1 if backed else None),
        )

    def _rows(self):
        return self.conn.execute(
            """
            SELECT restaurant_id, itemcode, status, menu_item_id, evidence_count,
                   source, conflict_menu_item_ids
            FROM itemcode_mappings ORDER BY restaurant_id, itemcode
            """
        ).fetchall()

    def test_rebuild_writes_active_and_conflict_rows(self):
        oid = self._order(1)
        i1 = self._line(oid, "C1")
        i2 = self._line(oid, "C1")
        self._assign(i1, "m1")
        self._assign(i2, "m1")
        s1 = self._line(oid, "SPLIT")
        s2 = self._line(oid, "SPLIT")
        self._assign(s1, "m1")
        self._assign(s2, "m2")

        result = rebuild_itemcode_mappings(self.conn)
        self.assertIsInstance(result, ItemcodeRebuildResult)
        self.assertEqual(result.restaurants_scanned, 1)
        self.assertEqual(result.codes_scanned, 2)
        self.assertEqual(result.active_written, 1)
        self.assertEqual(result.conflicts_written, 1)
        self.assertEqual(
            result.conflicts,
            [{"restaurant_id": 1, "itemcode": "SPLIT", "menu_item_ids": ["m1", "m2"]}],
        )

        rows = {(r[0], r[1]): r for r in self._rows()}
        active = rows[(1, "C1")]
        self.assertEqual((active[2], active[3], active[4], active[5]), ("active", "m1", 2, "rebuild"))
        conflict = rows[(1, "SPLIT")]
        self.assertEqual((conflict[2], conflict[3]), ("conflict", None))
        self.assertEqual(json.loads(conflict[6]), ["m1", "m2"])

    def test_rebuild_ignores_blank_codes_and_unassigned_rows(self):
        oid = self._order(1)
        self._line(oid, None)
        self._line(oid, "   ")
        self._line(oid, "NOASSIGN")  # itemid never assigned in menu_item_variants
        result = rebuild_itemcode_mappings(self.conn)
        self.assertEqual(result.codes_scanned, 0)
        self.assertEqual(self._rows(), [])

    def test_rebuild_trusts_assignments_not_order_rows(self):
        # order_items.menu_item_id says m2 (stale), assignment authority says m1.
        oid = self._order(1)
        itemid = self._line(oid, "C1", menu_item_id="m2")
        self._assign(itemid, "m1")
        rebuild_itemcode_mappings(self.conn)
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C1"), "m1")

    def test_evidence_counts_distinct_itemids_not_order_lines(self):
        oid = self._order(1)
        itemid = self._line(oid, "C1")
        self._assign(itemid, "m1")
        for _ in range(5):  # replays / popular product: same itemid many lines
            self._line(self._order(1), "C1", itemid=itemid)
        rebuild_itemcode_mappings(self.conn)
        self.assertEqual(self._rows()[0][4], 1)

    def test_same_code_two_restaurants_stay_independent(self):
        i1 = self._line(self._order(1), "C1")
        i2 = self._line(self._order(2), "C1")
        self._assign(i1, "m1")
        self._assign(i2, "m2")
        result = rebuild_itemcode_mappings(self.conn)
        self.assertEqual(result.restaurants_scanned, 2)
        self.assertEqual(result.conflicts_written, 0)
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "C1"), "m1")
        self.assertEqual(get_active_itemcode_mapping(self.conn, 2, "C1"), "m2")

    def test_rebuild_is_idempotent_and_removes_stale(self):
        itemid = self._line(self._order(1), "C1")
        self._assign(itemid, "m1")
        # Stale first_seen row whose code no longer appears in any order line.
        observe_itemcode_assignment(self.conn, 1, "GONE", "m2")

        first = rebuild_itemcode_mappings(self.conn)
        self.assertEqual(first.stale_removed, 1)
        rows_after_first = self._rows()

        second = rebuild_itemcode_mappings(self.conn)
        self.assertEqual(second.stale_removed, 0)
        self.assertEqual(second.active_written, 1)
        self.assertEqual(self._rows(), rows_after_first)
        self.assertEqual([r[1] for r in rows_after_first], ["C1"])

    def test_scoped_rebuild_leaves_other_restaurants_alone(self):
        i1 = self._line(self._order(1), "C1")
        self._assign(i1, "m1")
        observe_itemcode_assignment(self.conn, 2, "OTHER", "m2")  # not in scope
        result = rebuild_itemcode_mappings(self.conn, restaurant_id=1)
        self.assertEqual(result.stale_removed, 0)
        codes = {(r[0], r[1]) for r in self._rows()}
        self.assertEqual(codes, {(1, "C1"), (2, "OTHER")})

    def test_merge_shaped_update_resolves_conflict(self):
        s1 = self._line(self._order(1), "SPLIT")
        s2 = self._line(self._order(1), "SPLIT")
        self._assign(s1, "m1")
        self._assign(s2, "m2")
        rebuild_itemcode_mappings(self.conn)
        self.assertIsNone(get_active_itemcode_mapping(self.conn, 1, "SPLIT"))

        # UI merge converges both assignments onto one target parent.
        self._assign(s2, "m1")
        result = rebuild_itemcode_mappings(self.conn)
        self.assertEqual(result.conflicts_written, 0)
        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "SPLIT"), "m1")
        row = self._rows()[0]
        self.assertEqual((row[2], row[4]), ("active", 2))

    def test_route_eligibility_tracks_server_backing(self):
        # A server-backed assignment (assignment_seq set) makes the rebuilt
        # mapping route_eligible; a purely local assignment does not, so its code
        # stays active-but-unroutable (Phase 9.2 auto-route gate).
        oid = self._order(1)
        backed = self._line(oid, "BACKED")
        local = self._line(oid, "LOCAL")
        self._assign(backed, "m1", backed=True)
        self._assign(local, "m2", backed=False)
        rebuild_itemcode_mappings(self.conn)

        self.assertEqual(get_active_itemcode_mapping(self.conn, 1, "BACKED"), "m1")
        self.assertIsNone(get_active_itemcode_mapping(self.conn, 1, "LOCAL"))
        eligibility = dict(
            self.conn.execute(
                "SELECT itemcode, route_eligible FROM itemcode_mappings"
            ).fetchall()
        )
        self.assertEqual(eligibility, {"BACKED": 1, "LOCAL": 0})
        # Both are active rows — only routability differs.
        statuses = dict(
            self.conn.execute(
                "SELECT itemcode, status FROM itemcode_mappings"
            ).fetchall()
        )
        self.assertEqual(statuses, {"BACKED": "active", "LOCAL": "active"})

    def test_rebuild_does_not_commit(self):
        itemid = self._line(self._order(1), "C1")
        self._assign(itemid, "m1")
        self.conn.commit()
        self.conn.execute("BEGIN")
        rebuild_itemcode_mappings(self.conn)
        self.conn.rollback()
        self.assertEqual(self._rows(), [])


class TestSchemaAndCascade(unittest.TestCase):
    def test_ensure_schema_idempotent(self):
        conn = _make_db()
        try:
            ensure_itemcode_mapping_schema(conn)
            ensure_itemcode_mapping_schema(conn)
            observe_itemcode_assignment(conn, 1, "C1", "m1")
            ensure_itemcode_mapping_schema(conn)
            row = conn.execute(
                "SELECT status, menu_item_id, route_eligible FROM itemcode_mappings "
                "WHERE restaurant_id=1 AND itemcode='C1'"
            ).fetchone()
            self.assertEqual(row, ("active", "m1", 0))
        finally:
            conn.close()

    def test_parent_deletion_cascades(self):
        conn = _make_db()
        try:
            observe_itemcode_assignment(conn, 1, "C1", "m1")
            observe_itemcode_assignment(conn, 1, "C2", "m2")
            conn.execute("DELETE FROM menu_items WHERE menu_item_id = 'm1'")
            codes = [
                r[0] for r in conn.execute("SELECT itemcode FROM itemcode_mappings").fetchall()
            ]
            self.assertEqual(codes, ["C2"])
        finally:
            conn.close()

    def test_restaurant_deletion_cascades(self):
        conn = _make_db()
        try:
            observe_itemcode_assignment(conn, 1, "C1", "m1")
            observe_itemcode_assignment(conn, 2, "C1", "m1")
            conn.execute("DELETE FROM restaurants WHERE restaurant_id = 1")
            rows = conn.execute(
                "SELECT restaurant_id FROM itemcode_mappings"
            ).fetchall()
            self.assertEqual([r[0] for r in rows], [2])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
