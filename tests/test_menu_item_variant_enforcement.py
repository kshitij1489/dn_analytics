import sqlite3
import unittest

from utils.menu_item_variant_enforcement import (
    DEFAULT_CATALOG_VARIANT_NAME,
    backfill_addon_only_variant_mappings,
    backfill_menu_items_missing_variant_mappings,
    ensure_menu_item_has_variant_mapping,
    mark_addon_eligible_from_usage,
)
from utils.id_generator import generate_deterministic_id


class MenuItemVariantEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                is_verified BOOLEAN DEFAULT 0
            );
            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL UNIQUE,
                is_verified BOOLEAN DEFAULT 0
            );
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                price DECIMAL(10,2) DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                addon_eligible BOOLEAN DEFAULT 0,
                delivery_eligible BOOLEAN DEFAULT 1,
                is_verified BOOLEAN DEFAULT 0
            );
            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER NOT NULL,
                menu_item_id TEXT,
                variant_id TEXT,
                name_raw TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                price DECIMAL(10,2) NOT NULL DEFAULT 0
            );
            """
        )
        self.stub_vid = "f8b92f1e-8f3b-5a1c-8615-215dd0b3a4cc"
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name) VALUES (?, ?)",
            (self.stub_vid, DEFAULT_CATALOG_VARIANT_NAME),
        )
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type) VALUES (?, ?, ?)",
            ("orphan-mi-1", "Lonely Cone", "Ice Cream"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_ensure_inserts_1_piece_stub_once(self) -> None:
        cur = self.conn.cursor()
        self.assertTrue(ensure_menu_item_has_variant_mapping(self.conn, "orphan-mi-1", cursor=cur))
        self.assertFalse(ensure_menu_item_has_variant_mapping(self.conn, "orphan-mi-1", cursor=cur))
        cur.execute("SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = ?", ("orphan-mi-1",))
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute(
            "SELECT variant_id FROM menu_item_variants WHERE menu_item_id = ?",
            ("orphan-mi-1",),
        )
        self.assertEqual(cur.fetchone()[0], self.stub_vid)
        stub_oid = generate_deterministic_id("catalog_default_variant_stub", "orphan-mi-1")
        cur.execute(
            "SELECT order_item_id FROM menu_item_variants WHERE menu_item_id = ?",
            ("orphan-mi-1",),
        )
        self.assertEqual(cur.fetchone()[0], stub_oid)

    def test_backfill_counts_only_missing(self) -> None:
        cur = self.conn.cursor()
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type) VALUES (?, ?, ?)",
            ("has-map", "Mapped Item", "Ice Cream"),
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id)
            VALUES ('line-1', 'has-map', ?)
            """,
            (self.stub_vid,),
        )
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type) VALUES (?, ?, ?)",
            ("orphan-mi-2", "Another Lonely", "Ice Cream"),
        )
        self.conn.commit()
        n = backfill_menu_items_missing_variant_mappings(self.conn, cursor=cur)
        self.assertEqual(n, 2)
        cur.execute(
            "SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id IN ('orphan-mi-1','orphan-mi-2')"
        )
        self.assertEqual(cur.fetchone()[0], 2)
        cur.execute("SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = 'has-map'")
        self.assertEqual(cur.fetchone()[0], 1)

    def test_mark_addon_eligible_flags_only_sold_pairs(self) -> None:
        cur = self.conn.cursor()
        # Two mappings: one sold as an addon, one never sold as an addon.
        self.conn.executescript(
            f"""
            INSERT INTO menu_items (menu_item_id, name, type) VALUES
                ('addon-mi', 'Waffle Topping', 'Extra'),
                ('plain-mi', 'Plain Scoop', 'Ice Cream');
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id) VALUES
                ('map-addon', 'addon-mi', '{self.stub_vid}'),
                ('map-plain', 'plain-mi', '{self.stub_vid}');
            INSERT INTO order_item_addons (order_item_id, menu_item_id, variant_id, name_raw) VALUES
                (1, 'addon-mi', '{self.stub_vid}', 'Waffle Topping'),
                (2, 'addon-mi', '{self.stub_vid}', 'Waffle Topping'),
                (3, NULL, NULL, 'Unmatched Addon');
            """
        )
        self.conn.commit()

        flagged = mark_addon_eligible_from_usage(self.conn, cursor=cur)
        self.assertEqual(flagged, 1)

        cur.execute("SELECT addon_eligible FROM menu_item_variants WHERE menu_item_id = 'addon-mi'")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT addon_eligible FROM menu_item_variants WHERE menu_item_id = 'plain-mi'")
        self.assertEqual(cur.fetchone()[0], 0)

        # Sticky + idempotent: a second pass flips nothing new.
        self.assertEqual(mark_addon_eligible_from_usage(self.conn, cursor=cur), 0)

    def test_backfill_addon_only_mappings_seeds_missing_pairs(self) -> None:
        cur = self.conn.cursor()
        tub_vid = "aa11bb22-cc33-dd44-ee55-ff6677889900"
        self.conn.executescript(
            f"""
            INSERT INTO variants (variant_id, variant_name) VALUES
                ('{tub_vid}', 'HALF_MINI_TUB_80GMS');
            INSERT INTO menu_items (menu_item_id, name, type) VALUES
                ('flavor-mi', 'Pistachio Ice Cream', 'Ice Cream');
            -- Sold as an addon at a tub size that was never a standalone line,
            -- so it has no mapping row. Plus rows that must be ignored.
            INSERT INTO order_item_addons (order_item_id, menu_item_id, variant_id, name_raw) VALUES
                (1, 'flavor-mi', '{tub_vid}', 'Pistachio (80gm)'),
                (2, 'flavor-mi', 'ghost-variant', 'Bad Variant'),
                (3, 'ghost-item', '{tub_vid}', 'Bad Item'),
                (4, NULL, NULL, 'Unmatched');
            """
        )
        self.conn.commit()

        seeded = backfill_addon_only_variant_mappings(self.conn, cursor=cur)
        self.assertEqual(seeded, 1)

        expected_oid = generate_deterministic_id("addon_seeded_mapping", "flavor-mi", tub_vid)
        cur.execute(
            "SELECT order_item_id, addon_eligible, is_verified FROM menu_item_variants "
            "WHERE menu_item_id = 'flavor-mi' AND variant_id = ?",
            (tub_vid,),
        )
        row = cur.fetchone()
        self.assertEqual(row[0], expected_oid)
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], 0)

        cur.execute(
            """
            SELECT 1
            FROM menu_item_variants mv
            WHERE mv.is_verified = 0
              AND mv.menu_item_id = 'flavor-mi'
              AND mv.variant_id = ?
            GROUP BY mv.menu_item_id, mv.variant_id
            """,
            (tub_vid,),
        )
        self.assertIsNotNone(cur.fetchone())

        # Pairs with a missing menu_item or variant are never seeded.
        cur.execute("SELECT COUNT(*) FROM menu_item_variants WHERE variant_id = 'ghost-variant'")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = 'ghost-item'")
        self.assertEqual(cur.fetchone()[0], 0)

        # Idempotent: re-running seeds nothing new.
        self.assertEqual(backfill_addon_only_variant_mappings(self.conn, cursor=cur), 0)


if __name__ == "__main__":
    unittest.main()
