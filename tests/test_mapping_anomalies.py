"""
Tests for the silent-reuse mapping guard.

Covers the pure core-normalization contract (real reuse vs benign relabel), the
detection state machine (baseline seeding, verified-only flagging, dedup,
dismissal), and an end-to-end pass through OrderItemCluster.add().
"""

import sqlite3
import unittest

from utils.mapping_core import mapping_core_key
from src.core.mapping_anomalies import (
    ensure_mapping_anomaly_schema,
    record_core_and_flag,
    list_open_anomalies,
    dismiss_anomaly,
    close_anomalies_for_order_item,
)


class TestMappingCoreKey(unittest.TestCase):
    def _same(self, a, b):
        self.assertEqual(mapping_core_key(a), mapping_core_key(b), f"{a!r} vs {b!r} should collapse")

    def _diff(self, a, b):
        self.assertNotEqual(mapping_core_key(a), mapping_core_key(b), f"{a!r} vs {b!r} should differ")

    def test_benign_relabels_collapse(self):
        self._same("Banoffee Ice Cream (Regular Tub (220gms))", "Eggless Banoffee Ice Cream (Regular Tub (300ml))")
        self._same("Fig & Orange 200ml", "Eggless Fig & Orange Ice Cream")
        self._same("Fig &amp; Orange Ice Cream Small Scoop", "Fig & Orange (60gm)")
        self._same("Coconut &amp; Pineapple (160gm)", "Eggless Coconut & Pineapple Ice Cream")

    def test_real_reuse_differs(self):
        self._diff(
            "Eggless Chocolate Ice Cream (Regular Tub (300ml))",
            "Just Chocolate (andra) Ice Cream (Regular Tub (220gms))",
        )
        self._diff("Go Bananas Ice Cream (Perfect Plenty)", "Eggless Banoffee Ice Cream")
        # egg-based vs eggless is a genuine recipe difference
        self._diff("Egg Based Paan & Gulkand Ice Cream", "Eggless Paan & Gulkand Ice Cream")

    def test_empty_name(self):
        self.assertIsNone(mapping_core_key(""))
        self.assertIsNone(mapping_core_key(None))


class TestDetection(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        # list_open_anomalies is built for the real analytics DB and LEFT JOINs the
        # mapping tables; empty stubs are enough to exercise the detection logic.
        self.conn.executescript(
            """
            CREATE TABLE menu_items (menu_item_id TEXT PRIMARY KEY, name TEXT, type TEXT);
            CREATE TABLE variants (variant_id TEXT PRIMARY KEY, variant_name TEXT);
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY, menu_item_id TEXT, variant_id TEXT, is_verified INTEGER
            );
            """
        )
        ensure_mapping_anomaly_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _rec(self, oid, raw, verified, is_addon=False):
        return record_core_and_flag(
            self.conn,
            order_item_id=oid,
            name_raw=raw,
            is_addon=is_addon,
            mapped_menu_item_id="m1",
            mapped_name="Eggless Chocolate Ice Cream",
            is_verified=verified,
        )

    def test_baseline_does_not_flag(self):
        self.assertIsNone(self._rec("id1", "Eggless Chocolate Ice Cream", True))
        self.assertEqual(len(list_open_anomalies(self.conn)), 0)

    def test_new_core_on_verified_flags_once(self):
        self._rec("id1", "Eggless Chocolate Ice Cream", True)          # baseline
        first = self._rec("id1", "Just Chocolate Ice Cream", True)     # reuse
        self.assertIsNotNone(first)
        # same divergent core again -> no duplicate
        second = self._rec("id1", "Just Chocolate Ice Cream (Mini Tub)", True)
        self.assertIsNone(second)
        self.assertEqual(len(list_open_anomalies(self.conn)), 1)

    def test_new_core_on_unverified_does_not_flag(self):
        self._rec("id2", "Eggless Chocolate Ice Cream", False)         # baseline, unverified
        self.assertIsNone(self._rec("id2", "Just Chocolate Ice Cream", False))
        self.assertEqual(len(list_open_anomalies(self.conn)), 0)

    def test_benign_relabel_does_not_flag(self):
        self._rec("id3", "Eggless Banoffee Ice Cream", True)
        self.assertIsNone(self._rec("id3", "Banoffee (60gm)", True))
        self.assertEqual(len(list_open_anomalies(self.conn)), 0)

    def test_dismiss_then_no_refire(self):
        self._rec("id4", "Eggless Chocolate Ice Cream", True)
        self._rec("id4", "Just Chocolate Ice Cream", True)
        anomaly_id = list_open_anomalies(self.conn)[0]["anomaly_id"]
        res = dismiss_anomaly(self.conn, anomaly_id)
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(list_open_anomalies(self.conn)), 0)
        # the same divergent core arriving again must not re-open it
        self.assertIsNone(self._rec("id4", "Just Chocolate Ice Cream (Family Tub)", True))
        self.assertEqual(len(list_open_anomalies(self.conn)), 0)

    def test_close_for_order_item(self):
        self._rec("id5", "Eggless Chocolate Ice Cream", True)
        self._rec("id5", "Just Chocolate Ice Cream", True)
        self.assertEqual(len(list_open_anomalies(self.conn)), 1)
        closed = close_anomalies_for_order_item(self.conn, "id5")
        self.assertEqual(closed, 1)
        self.assertEqual(len(list_open_anomalies(self.conn)), 0)


class TestClusterIntegration(unittest.TestCase):
    """Drive detection through the real OrderItemCluster.add() path."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY, name TEXT, type TEXT,
                is_verified INTEGER DEFAULT 0, suggestion_id TEXT,
                total_sold REAL DEFAULT 0, sold_as_item REAL DEFAULT 0,
                sold_as_addon REAL DEFAULT 0, total_revenue REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT
            );
            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY, variant_name TEXT UNIQUE,
                unit TEXT, value REAL, is_verified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT
            );
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY, menu_item_id TEXT, variant_id TEXT,
                price REAL DEFAULT 0, is_active INTEGER DEFAULT 1,
                addon_eligible INTEGER DEFAULT 0, delivery_eligible INTEGER DEFAULT 1,
                is_verified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT
            );
            """
        )
        # Seed the default 1_PIECE variant with the same deterministic id the
        # clustering pipeline derives, so ensure_menu_item_has_variant_mapping and
        # the cluster's own INSERT reuse one row instead of colliding on the name.
        from utils.id_generator import generate_deterministic_id

        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name) VALUES (?, '1_PIECE')",
            (generate_deterministic_id("1_PIECE"),),
        )
        self.conn.commit()
        from services.clustering_service import OrderItemCluster

        self.cluster = OrderItemCluster(self.conn)

    def tearDown(self):
        self.conn.close()

    def _verify_all_mappings(self):
        self.conn.execute("UPDATE menu_item_variants SET is_verified = 1")
        self.conn.commit()

    def test_reuse_surfaces_after_verify(self):
        # First order establishes and (simulated) verifies the id's mapping.
        self.cluster.add("Eggless Chocolate Ice Cream (Regular Tub (300ml))", "pid_reuse")
        self._verify_all_mappings()
        # Same id later carries a different product -> anomaly.
        self.cluster.add("Just Chocolate Ice Cream (Regular Tub (220gms))", "pid_reuse")
        anomalies = list_open_anomalies(self.conn)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["order_item_id"], "pid_reuse")

    def test_benign_relabel_no_anomaly(self):
        self.cluster.add("Eggless Banoffee Ice Cream (Regular Tub (300ml))", "pid_benign")
        self._verify_all_mappings()
        self.cluster.add("Banoffee Ice Cream (Regular Tub (220gms))", "pid_benign")
        self.assertEqual(len(list_open_anomalies(self.conn)), 0)

    def test_addon_flag_marks_is_addon(self):
        self.cluster.add("Eggless Chocolate Ice Cream", "pid_addon", is_addon=True)
        self._verify_all_mappings()
        self.cluster.add("Just Chocolate Ice Cream", "pid_addon", is_addon=True)
        anomalies = list_open_anomalies(self.conn)
        self.assertEqual(len(anomalies), 1)
        self.assertTrue(anomalies[0]["is_addon"])


if __name__ == "__main__":
    unittest.main()
