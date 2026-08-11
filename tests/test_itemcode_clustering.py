"""
Phase 2 tests: itemcode routing inside OrderItemCluster.add().

Covers the resolution priority (existing itemid > active itemcode > parser
fallback), split detection on existing-itemid disagreement, variant creation
and C1 verification inheritance on an itemcode hit, fallback behavior for
conflicted/unknown/blank codes, fuzzy staying suggestion-only, the addon path
producing no itemcode rows, and rollback removing all partial itemcode effects.
"""

import sqlite3
import unittest

from services.clustering_service import OrderItemCluster
from src.core.itemcode_mapping import (
    get_active_itemcode_mapping,
    observe_itemcode_assignment,
)
from utils.clean_order_item import clean_order_item_name
from utils.id_generator import generate_deterministic_id


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE restaurants (restaurant_id INTEGER PRIMARY KEY, name TEXT);

        CREATE TABLE menu_items (
            menu_item_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            is_verified BOOLEAN DEFAULT 0,
            suggestion_id TEXT
        );

        CREATE TABLE variants (
            variant_id TEXT PRIMARY KEY,
            variant_name TEXT NOT NULL,
            unit TEXT,
            value DECIMAL(10,2),
            is_verified BOOLEAN DEFAULT 0
        );

        CREATE TABLE menu_item_variants (
            order_item_id TEXT PRIMARY KEY,
            menu_item_id TEXT NOT NULL REFERENCES menu_items(menu_item_id),
            variant_id TEXT,
            price DECIMAL(10,2) DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            addon_eligible BOOLEAN DEFAULT 0,
            delivery_eligible BOOLEAN DEFAULT 1,
            is_verified BOOLEAN DEFAULT 0,
            assignment_seq INTEGER,
            verification_seq INTEGER,
            pending_local INTEGER DEFAULT 0
        );
        """
    )
    conn.execute("INSERT INTO restaurants (restaurant_id, name) VALUES (1, 'R1')")
    conn.commit()
    return conn


class ItemcodeClusteringBase(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()
        self.cluster = OrderItemCluster(self.conn)

    def tearDown(self):
        self.conn.close()

    def _seed_parent(self, menu_item_id, name, item_type="Ice Cream", verified=1):
        self.conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES (?, ?, ?, ?)
            """,
            (menu_item_id, name, item_type, verified),
        )
        return menu_item_id

    def _seed_mapping(
        self,
        order_item_id,
        menu_item_id,
        variant_id="v1",
        verified=0,
        *,
        assignment_seq=None,
        verification_seq=None,
    ):
        self.conn.execute(
            """
            INSERT INTO variants (variant_id, variant_name)
            VALUES (?, ?) ON CONFLICT (variant_id) DO NOTHING
            """,
            (variant_id, variant_id),
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, verification_seq
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(order_item_id),
                menu_item_id,
                variant_id,
                verified,
                assignment_seq,
                verification_seq,
            ),
        )

    def _code_row(self, code, rid=1):
        return self.conn.execute(
            """
            SELECT status, menu_item_id, evidence_count, conflict_menu_item_ids
            FROM itemcode_mappings WHERE restaurant_id = ? AND itemcode = ?
            """,
            (rid, code),
        ).fetchone()

    def _mark_route_eligible(self, code, rid=1):
        # Simulate the post-sync rebuild promoting a mapping to route_eligible
        # once its parent became server-backed (Phase 9.2). Without this a
        # first_seen mapping is active but never auto-routes.
        self.conn.execute(
            "UPDATE itemcode_mappings SET route_eligible = 1 "
            "WHERE restaurant_id = ? AND itemcode = ?",
            (rid, code),
        )


class TestExistingItemidPriority(ItemcodeClusteringBase):
    def test_existing_itemid_hit_stays_first_priority_and_confirms_code(self):
        parent = self._seed_parent("mA", "Chocolate Ice Cream")
        self._seed_mapping("IT1", parent)
        observe_itemcode_assignment(self.conn, 1, "CHOC", parent)

        result = self.cluster.add(
            "Chocolate Ice Cream 160gm", "IT1", itemcode="CHOC", restaurant_id=1
        )

        self.assertEqual(result.match_method, "existing-id-hit")
        self.assertEqual(result.menu_item_id, parent)
        # Agreeing observation bumps evidence on the active row.
        self.assertEqual(self._code_row("CHOC"), ("active", parent, 2, None))

    def test_existing_itemid_disagreement_marks_conflict_without_remap(self):
        parent_a = self._seed_parent("mA", "Chocolate Ice Cream")
        parent_b = self._seed_parent("mB", "Vanilla Ice Cream")
        self._seed_mapping("IT1", parent_a)
        observe_itemcode_assignment(self.conn, 1, "CHOC", parent_b)

        result = self.cluster.add(
            "Chocolate Ice Cream 160gm", "IT1", itemcode="CHOC", restaurant_id=1
        )

        # itemid assignment wins and is never rewritten by itemcode.
        self.assertEqual(result.match_method, "existing-id-hit")
        self.assertEqual(result.menu_item_id, parent_a)
        row = self.conn.execute(
            "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = 'IT1'"
        ).fetchone()
        self.assertEqual(row[0], parent_a)
        # ...but the split is recorded: the code is now conflicted.
        status, mid, _, conflict = self._code_row("CHOC")
        self.assertEqual((status, mid), ("conflict", None))
        self.assertIn(parent_a, conflict)
        self.assertIn(parent_b, conflict)
        self.assertIsNone(get_active_itemcode_mapping(self.conn, 1, "CHOC"))


class TestItemcodeHit(ItemcodeClusteringBase):
    def test_active_itemcode_routes_unseen_itemid_to_mapped_parent(self):
        # Mapped parent differs from what the parser would produce, proving the
        # itemcode (not the name) chose the parent.
        parent = self._seed_parent("mFancy", "Bean-to-Bar Dark Chocolate Ice Cream")
        observe_itemcode_assignment(self.conn, 1, "CHOCO70ICE", parent)
        self._mark_route_eligible("CHOCO70ICE")

        result = self.cluster.add(
            "Chocolate Ice Cream 400gm", "IT2", itemcode="CHOCO70ICE", restaurant_id=1
        )

        self.assertEqual(result.match_method, "itemcode-hit")
        self.assertEqual(result.match_confidence, 100.0)
        self.assertEqual(result.menu_item_id, parent)
        self.assertEqual(result.item_type, "Ice Cream")
        # Parsed variant is created with inferred metadata.
        expected_variant_id = generate_deterministic_id("400GMS")
        self.assertEqual(result.variant_id, expected_variant_id)
        unit, value = self.conn.execute(
            "SELECT unit, value FROM variants WHERE variant_id = ?",
            (expected_variant_id,),
        ).fetchone()
        self.assertEqual((unit, value), ("GMS", 400))
        # Mapping row exists, unverified (no verified pair to inherit from).
        mapping = self.conn.execute(
            "SELECT menu_item_id, variant_id, is_verified FROM menu_item_variants "
            "WHERE order_item_id = 'IT2'"
        ).fetchone()
        self.assertEqual(mapping, (parent, expected_variant_id, 0))
        # No parser-derived parent was created as a side effect.
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0], 1
        )

    def test_same_code_different_itemids_share_parent_distinct_variants(self):
        # Cappuccino-shaped scenario: first sighting teaches the projection via
        # the parser, the second (unseen itemid, same code) routes by itemcode.
        first = self.cluster.add(
            "Chocolate Ice Cream 160gm", "IT1", itemcode="CHOC", restaurant_id=1
        )
        self.assertEqual(self._code_row("CHOC")[0], "active")
        # A sync/flush + rebuild has since promoted the code to route-eligible.
        self._mark_route_eligible("CHOC")

        second = self.cluster.add(
            "Chocolate Ice Cream 400gm", "IT2", itemcode="CHOC", restaurant_id=1
        )

        self.assertEqual(second.match_method, "itemcode-hit")
        self.assertEqual(second.menu_item_id, first.menu_item_id)
        self.assertNotEqual(second.variant_id, first.variant_id)

    def test_two_codes_can_map_into_one_parent(self):
        # UI-style merge outcome: both codes active on the same parent.
        parent = self._seed_parent("mA", "Eggless Banoffee Ice Cream")
        observe_itemcode_assignment(self.conn, 1, "EgglessBanoffee", parent)
        observe_itemcode_assignment(self.conn, 1, "BANICE", parent)
        self._mark_route_eligible("EgglessBanoffee")
        self._mark_route_eligible("BANICE")

        r1 = self.cluster.add(
            "Eggless Banoffee Ice Cream 160gm", "IT1",
            itemcode="EgglessBanoffee", restaurant_id=1,
        )
        r2 = self.cluster.add(
            "Go Bananas Ice Cream 400gm", "IT2", itemcode="BANICE", restaurant_id=1
        )

        self.assertEqual(r1.match_method, "itemcode-hit")
        self.assertEqual(r2.match_method, "itemcode-hit")
        self.assertEqual(r1.menu_item_id, parent)
        self.assertEqual(r2.menu_item_id, parent)

    def test_c1_local_only_verified_row_does_not_seed_inheritance(self):
        parent = self._seed_parent("mA", "Chocolate Ice Cream")
        # A local-only C1 cascade is verified but has no server sequence, so it
        # must not mint another verified row.
        verified_variant = generate_deterministic_id("400GMS")
        self._seed_mapping("OLD", parent, variant_id=verified_variant, verified=1)
        observe_itemcode_assignment(self.conn, 1, "CHOC", parent)
        self._mark_route_eligible("CHOC")

        result = self.cluster.add(
            "Chocolate Ice Cream 400gm", "IT2", itemcode="CHOC", restaurant_id=1
        )

        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id = 'IT2'"
        ).fetchone()
        self.assertEqual(result.match_method, "itemcode-hit")
        self.assertEqual(row[0], 0)

    def test_c1_verification_stream_row_seeds_inheritance(self):
        parent = self._seed_parent("mA", "Chocolate Ice Cream")
        verified_variant = generate_deterministic_id("400GMS")
        self._seed_mapping(
            "OLD",
            parent,
            variant_id=verified_variant,
            verified=1,
            verification_seq=42,
        )
        observe_itemcode_assignment(self.conn, 1, "CHOC", parent)
        self._mark_route_eligible("CHOC")

        same_pair = self.cluster.add(
            "Chocolate Ice Cream 400gm", "IT2", itemcode="CHOC", restaurant_id=1
        )
        other_pair = self.cluster.add(
            "Chocolate Ice Cream 160gm", "IT3", itemcode="CHOC", restaurant_id=1
        )

        rows = dict(
            self.conn.execute(
                "SELECT order_item_id, is_verified FROM menu_item_variants "
                "WHERE order_item_id IN ('IT2', 'IT3')"
            ).fetchall()
        )
        self.assertEqual(same_pair.match_method, "itemcode-hit")
        self.assertEqual(rows["IT2"], 1)  # inherits: verified pair exists
        self.assertEqual(rows["IT3"], 0)  # new variant pair: stays unverified

    def test_c1_snapshot_assignment_row_seeds_inheritance(self):
        parent = self._seed_parent("mA", "Chocolate Ice Cream")
        verified_variant = generate_deterministic_id("400GMS")
        self._seed_mapping(
            "SNAPSHOT",
            parent,
            variant_id=verified_variant,
            verified=1,
            assignment_seq=371,
        )
        observe_itemcode_assignment(self.conn, 1, "CHOC", parent)
        self._mark_route_eligible("CHOC")

        result = self.cluster.add(
            "Chocolate Ice Cream 400gm", "IT2", itemcode="CHOC", restaurant_id=1
        )

        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id = 'IT2'"
        ).fetchone()
        self.assertEqual(result.match_method, "itemcode-hit")
        self.assertEqual(row[0], 1)


class TestFallbackPaths(ItemcodeClusteringBase):
    def test_conflicted_code_uses_parser_and_unions_candidates(self):
        parent_a = self._seed_parent("mA", "Orange (Contains Alcohol)")
        parent_b = self._seed_parent("mB", "Orange & Biscuits (Contains Alcohol)")
        observe_itemcode_assignment(self.conn, 1, "SPLIT", parent_a)
        observe_itemcode_assignment(self.conn, 1, "SPLIT", parent_b)
        self.assertEqual(self._code_row("SPLIT")[0], "conflict")

        result = self.cluster.add(
            "Chocolate Ice Cream 160gm", "IT9", itemcode="SPLIT", restaurant_id=1
        )

        # Conflicted code never picks a winner: normal parser path runs.
        self.assertIn(result.match_method, ("new", "name-hit", "fuzzy-suggested"))
        self.assertNotIn(result.menu_item_id, (parent_a, parent_b))
        # The observed fallback parent joins the conflict candidate list.
        status, _, _, conflict = self._code_row("SPLIT")
        self.assertEqual(status, "conflict")
        self.assertIn(result.menu_item_id, conflict)

    def test_missing_or_blank_itemcode_matches_legacy_behavior(self):
        legacy = self.cluster.add("Chocolate Ice Cream 160gm", "IT1")

        blank_conn = _make_db()
        try:
            blank_cluster = OrderItemCluster(blank_conn)
            blank = blank_cluster.add(
                "Chocolate Ice Cream 160gm", "IT1", itemcode="   ", restaurant_id=1
            )
            self.assertEqual(legacy, blank)
            for table in ("menu_items", "variants", "menu_item_variants"):
                self.assertEqual(
                    self.conn.execute(f"SELECT * FROM {table}").fetchall(),
                    blank_conn.execute(f"SELECT * FROM {table}").fetchall(),
                    table,
                )
            # Blank code records nothing in the projection.
            self.assertEqual(
                blank_conn.execute(
                    "SELECT COUNT(*) FROM itemcode_mappings"
                ).fetchone()[0],
                0,
            )
        finally:
            blank_conn.close()

    def test_fuzzy_match_stays_suggestion_and_projection_learns_new_parent(self):
        parsed = clean_order_item_name("Chocolate Ice Cream 160gm")
        # Verified near-miss (one char off) of the parsed clean name, same type:
        # close enough for difflib, not an exact name-hit.
        near_name = parsed["name"][:-1]
        suggested = self._seed_parent("mSug", near_name, item_type=parsed["type"])

        result = self.cluster.add(
            "Chocolate Ice Cream 160gm", "IT1", itemcode="NEWC", restaurant_id=1
        )

        self.assertEqual(result.match_method, "fuzzy-suggested")
        # Suggestion recorded, but assignment goes to the new unverified parent.
        self.assertNotEqual(result.menu_item_id, suggested)
        name_row = self.conn.execute(
            "SELECT is_verified, suggestion_id FROM menu_items WHERE menu_item_id = ?",
            (result.menu_item_id,),
        ).fetchone()
        self.assertEqual(name_row, (0, suggested))
        # Projection learns the actually-assigned parent, not the suggestion.
        # It is a first_seen row (route_eligible=0), so it is not yet routable
        # via get_active_itemcode_mapping; assert the learned row directly.
        learned = self._code_row("NEWC")
        self.assertEqual((learned[0], learned[1]), ("active", result.menu_item_id))
        self.assertIsNone(get_active_itemcode_mapping(self.conn, 1, "NEWC"))

    def test_first_seen_mapping_not_route_eligible_falls_through(self):
        # A mapping taught only by local first_seen ingest (route_eligible=0)
        # must NOT auto-route an unseen itemid: ingest falls through to the
        # parser instead of crowning a fleet-wide parent before sync (Phase 9.2).
        parent = self._seed_parent("mFancy", "Bean-to-Bar Dark Chocolate Ice Cream")
        observe_itemcode_assignment(self.conn, 1, "CHOCO70ICE", parent)
        # Deliberately NOT promoted to route-eligible.

        result = self.cluster.add(
            "Chocolate Ice Cream 400gm", "IT2", itemcode="CHOCO70ICE", restaurant_id=1
        )

        # The gate held: the unseen itemid did NOT route to the first_seen parent.
        self.assertNotEqual(result.match_method, "itemcode-hit")
        self.assertNotEqual(result.menu_item_id, parent)
        # The projection never became a route authority (the fall-through parser
        # even picked a different parent, tipping the code into conflict).
        self.assertEqual(
            self.conn.execute(
                "SELECT route_eligible FROM itemcode_mappings WHERE itemcode = 'CHOCO70ICE'"
            ).fetchone()[0],
            0,
        )
        self.assertIsNone(get_active_itemcode_mapping(self.conn, 1, "CHOCO70ICE"))

    def test_addon_path_produces_no_itemcode_rows(self):
        result = self.cluster.add("Choco Chips", "AD1", is_addon=True)
        self.assertIsNotNone(result.menu_item_id)
        # Even a (mis)supplied code on an addon must be ignored.
        self.cluster.add(
            "Roasted Almonds", "AD2", is_addon=True, itemcode="ADDON", restaurant_id=1
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM itemcode_mappings").fetchone()[0],
            0,
        )


class TestTransactionality(ItemcodeClusteringBase):
    def test_rollback_removes_all_partial_itemcode_effects(self):
        self.conn.commit()
        self.conn.execute("BEGIN")
        result = self.cluster.add(
            "Chocolate Ice Cream 160gm", "IT1", itemcode="CHOC", restaurant_id=1
        )
        self.assertIsNotNone(result.menu_item_id)
        self.assertIsNotNone(self._code_row("CHOC"))
        self.conn.rollback()

        # Order-level rollback (a later item/addon/tax failure) leaves nothing:
        # no mapping, no parent, no projection row.
        self.assertIsNone(self._code_row("CHOC"))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM menu_item_variants").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0], 0
        )


if __name__ == "__main__":
    unittest.main()
