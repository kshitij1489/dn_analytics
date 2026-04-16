import sqlite3
import unittest

from utils.menu_item_variant_enforcement import (
    DEFAULT_CATALOG_VARIANT_NAME,
    backfill_menu_items_missing_variant_mappings,
    ensure_menu_item_has_variant_mapping,
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


if __name__ == "__main__":
    unittest.main()
