import sqlite3
import unittest

from src.core.menu_catalog_seed import build_cluster_state, build_id_maps, seed_catalog
from utils.id_generator import generate_deterministic_id


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE menu_items (
            menu_item_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            is_verified BOOLEAN DEFAULT 0
        );

        CREATE TABLE variants (
            variant_id TEXT PRIMARY KEY,
            variant_name TEXT NOT NULL,
            unit TEXT,
            value REAL,
            is_verified BOOLEAN DEFAULT 0
        );

        CREATE TABLE menu_item_variants (
            order_item_id TEXT PRIMARY KEY,
            menu_item_id TEXT NOT NULL,
            variant_id TEXT,
            price REAL DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            addon_eligible BOOLEAN DEFAULT 0,
            delivery_eligible BOOLEAN DEFAULT 1,
            is_verified BOOLEAN DEFAULT 0,
            updated_at TEXT
        );
        """
    )
    return conn


class MenuCatalogSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _make_conn()

    def tearDown(self) -> None:
        self.conn.close()

    def _snapshot(self):
        return (
            {
                "menu_id_to_str": {
                    "item_family_tub": "Family Tub",
                    "item_legacy_vanilla": "Old Fashioned Vanilla Ice Cream",
                },
                "variant_id_to_str": {
                    "variant_family_tub": "FAMILY_TUB_500GMS",
                    "variant_default": "1_PIECE",
                },
                "variant_id_to_meta": {
                    "variant_family_tub": {"unit": "GMS", "value": 500},
                },
                "type_id_to_str": {
                    "type_ice_cream": "Ice Cream",
                    "type_default": "Dessert",
                },
            },
            {
                "item_family_tub:type_ice_cream": {
                    "101": [["101", "variant_family_tub"]],
                },
            },
        )

    def test_seed_catalog_defaults_to_catalog_only_and_skips_stale_items(self) -> None:
        id_maps, cluster_state = self._snapshot()

        counts = seed_catalog(self.conn, id_maps, cluster_state)

        self.assertEqual(counts["items_seeded"], 1)
        self.assertEqual(counts["variants_seeded"], 2)
        self.assertEqual(counts["mappings_seeded"], 0)
        self.assertEqual(counts["skipped_unmapped"], 1)

        rows = self.conn.execute("SELECT menu_item_id, type FROM menu_items").fetchall()
        self.assertEqual(
            {row["menu_item_id"]: row["type"] for row in rows},
            {"item_family_tub": "Ice Cream"},
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM menu_item_variants WHERE order_item_id = '101'"
            ).fetchone()
        )

    def test_seed_catalog_can_seed_mappings_for_restore_flow(self) -> None:
        id_maps, cluster_state = self._snapshot()

        counts = seed_catalog(self.conn, id_maps, cluster_state, seed_mappings=True)

        self.assertEqual(counts["mappings_seeded"], 1)
        row = self.conn.execute(
            "SELECT menu_item_id, variant_id, is_verified FROM menu_item_variants WHERE order_item_id = '101'"
        ).fetchone()
        self.assertEqual(row["menu_item_id"], "item_family_tub")
        self.assertEqual(row["variant_id"], "variant_family_tub")
        self.assertEqual(row["is_verified"], 1)

    def test_seed_catalog_upserts_existing_name_type_and_variant_metadata(self) -> None:
        self.conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES ('item_family_tub', 'Old Name', 'Dessert', 0)
            """
        )
        self.conn.execute(
            """
            INSERT INTO variants (variant_id, variant_name, unit, value, is_verified)
            VALUES ('variant_family_tub', 'Old Variant', NULL, NULL, 0)
            """
        )
        self.conn.commit()
        id_maps, cluster_state = self._snapshot()

        seed_catalog(self.conn, id_maps, cluster_state)

        item = self.conn.execute(
            "SELECT name, type, is_verified FROM menu_items WHERE menu_item_id = 'item_family_tub'"
        ).fetchone()
        self.assertEqual(dict(item), {"name": "Family Tub", "type": "Ice Cream", "is_verified": 1})
        variant = self.conn.execute(
            "SELECT variant_name, unit, value, is_verified FROM variants WHERE variant_id = 'variant_family_tub'"
        ).fetchone()
        self.assertEqual(variant["variant_name"], "FAMILY_TUB_500GMS")
        self.assertEqual(variant["unit"], "GMS")
        self.assertEqual(variant["value"], 500)
        self.assertEqual(variant["is_verified"], 1)

    def test_builders_emit_export_compatible_payload_and_round_trip(self) -> None:
        ice_cream_type_id = generate_deterministic_id("Ice Cream")
        self.conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES ('item_family_tub', 'Family Tub', 'Ice Cream', 1)
            """
        )
        self.conn.execute(
            """
            INSERT INTO variants (variant_id, variant_name, unit, value, is_verified)
            VALUES ('variant_family_tub', 'FAMILY_TUB_500GMS', 'GMS', 500, 1)
            """
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES ('101', 'item_family_tub', 'variant_family_tub', 1)
            """
        )
        self.conn.commit()

        id_maps = build_id_maps(self.conn)
        cluster_state = build_cluster_state(self.conn)

        self.assertEqual(
            id_maps,
            {
                "menu_id_to_str": {"item_family_tub": "Family Tub"},
                "variant_id_to_str": {"variant_family_tub": "FAMILY_TUB_500GMS"},
                "variant_id_to_meta": {
                    "variant_family_tub": {"unit": "GMS", "value": 500},
                },
                "type_id_to_str": {ice_cream_type_id: "Ice Cream"},
            },
        )
        self.assertEqual(
            cluster_state,
            {
                f"item_family_tub:{ice_cream_type_id}": {
                    "101": [["101", "variant_family_tub"]],
                },
            },
        )

        fresh = _make_conn()
        self.addCleanup(fresh.close)
        counts = seed_catalog(fresh, id_maps, cluster_state, seed_mappings=True)
        self.assertEqual(counts["items_seeded"], 1)
        self.assertEqual(counts["variants_seeded"], 1)
        self.assertEqual(counts["mappings_seeded"], 1)
        self.assertEqual(
            fresh.execute(
                "SELECT name, type FROM menu_items WHERE menu_item_id = 'item_family_tub'"
            ).fetchone()["type"],
            "Ice Cream",
        )


if __name__ == "__main__":
    unittest.main()
