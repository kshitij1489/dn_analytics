import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.menu_bootstrap_sync import apply_menu_bootstrap_snapshot


class MenuBootstrapPullTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
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
                is_verified BOOLEAN DEFAULT 0,
                updated_at TEXT
            );

            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                menu_item_id TEXT,
                variant_id TEXT,
                updated_at TEXT
            );
            """
        )
        self.conn.execute(
            """
            INSERT INTO order_items (order_item_id, menu_item_id, variant_id)
            VALUES (101, 'item_old', 'variant_old')
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_apply_menu_bootstrap_snapshot_seeds_and_relinks_order_items(self) -> None:
        id_maps = {
            "menu_id_to_str": {"item_cold_coffee": "Cold Coffee"},
            "variant_id_to_str": {"variant_large": "Large"},
            "variant_id_to_meta": {"variant_large": {"unit": "ML", "value": 750}},
            "type_id_to_str": {"type_beverage": "Beverage"},
        }
        cluster_state = {
            "item_cold_coffee:type_beverage": {
                "101": [["101", "variant_large"]],
            }
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir)
            with patch("src.core.menu_bootstrap_sync.get_resource_path", return_value=str(data_dir)), patch(
                "scripts.seed_from_backups.get_resource_path",
                return_value=str(data_dir),
            ):
                result = apply_menu_bootstrap_snapshot(
                    self.conn,
                    id_maps,
                    cluster_state,
                    apply_mode="seed_and_relink_orders",
                )

        self.assertIsNone(result["error"])
        self.assertEqual(result["items_seeded"], 1)
        self.assertEqual(result["variants_seeded"], 1)
        self.assertEqual(result["mapping_assignments"], 1)
        self.assertEqual(result["order_items_present_in_snapshot"], 1)
        self.assertEqual(result["order_items_relinked"], 1)
        self.assertEqual(len(result["warnings"]), 2)

        order_item = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM order_items WHERE order_item_id = 101"
        ).fetchone()
        self.assertEqual(order_item["menu_item_id"], "item_cold_coffee")
        self.assertEqual(order_item["variant_id"], "variant_large")

        mapping = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM menu_item_variants WHERE order_item_id = '101'"
        ).fetchone()
        self.assertEqual(mapping["menu_item_id"], "item_cold_coffee")
        self.assertEqual(mapping["variant_id"], "variant_large")

        variant = self.conn.execute(
            "SELECT unit, value FROM variants WHERE variant_id = 'variant_large'"
        ).fetchone()
        self.assertEqual(variant["unit"], "ML")
        self.assertEqual(variant["value"], 750)

    def test_apply_menu_bootstrap_snapshot_infers_variant_metadata_for_legacy_snapshots(self) -> None:
        id_maps = {
            "menu_id_to_str": {"item_family_tub": "Family Tub"},
            "variant_id_to_str": {"variant_family_tub": "FAMILY_TUB_500GMS"},
            "type_id_to_str": {"type_ice_cream": "Ice Cream"},
        }
        cluster_state = {
            "item_family_tub:type_ice_cream": {
                "101": [["101", "variant_family_tub"]],
            }
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir)
            with patch("src.core.menu_bootstrap_sync.get_resource_path", return_value=str(data_dir)), patch(
                "scripts.seed_from_backups.get_resource_path",
                return_value=str(data_dir),
            ):
                result = apply_menu_bootstrap_snapshot(
                    self.conn,
                    id_maps,
                    cluster_state,
                    apply_mode="seed_and_relink_orders",
                )

        self.assertIsNone(result["error"])

        variant = self.conn.execute(
            "SELECT unit, value FROM variants WHERE variant_id = 'variant_family_tub'"
        ).fetchone()
        self.assertEqual(variant["unit"], "GMS")
        self.assertEqual(variant["value"], 500)


if __name__ == "__main__":
    unittest.main()
