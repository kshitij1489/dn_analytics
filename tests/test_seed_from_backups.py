import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.seed_from_backups import export_to_backups, perform_seeding


class SeedFromBackupsExportTests(unittest.TestCase):
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
            """
        )
        self.conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES ('item_family_tub', 'Family Tub', 'Ice Cream', 1)
            """
        )
        self.conn.execute(
            """
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES ('variant_family_tub', 'FAMILY_TUB_500GMS', 1)
            """
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES ('101', 'item_family_tub', 'variant_family_tub', 1)
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_export_to_backups_persists_variant_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir)
            with patch("scripts.seed_from_backups.get_resource_path", return_value=str(data_dir)):
                exported = export_to_backups(self.conn)

            self.assertTrue(exported)

            id_maps = json.loads((data_dir / "id_maps_backup.json").read_text())
            self.assertEqual(
                id_maps["variant_id_to_meta"]["variant_family_tub"],
                {"unit": "GMS", "value": 500},
            )

    def test_perform_seeding_skips_id_maps_items_without_cluster_state_key(self) -> None:
        id_maps = {
            "menu_id_to_str": {
                "item_family_tub": "Family Tub",
                "item_legacy_vanilla": "Old Fashioned Vanilla Ice Cream ( Recommended )",
            },
            "variant_id_to_str": {"variant_family_tub": "FAMILY_TUB_500GMS"},
            "type_id_to_str": {"type_ice_cream": "Ice Cream"},
        }
        cluster_state = {
            "item_family_tub:type_ice_cream": {"101": [["101", "variant_family_tub"]]},
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir)
            (data_dir / "id_maps_backup.json").write_text(json.dumps(id_maps))
            (data_dir / "cluster_state_backup.json").write_text(json.dumps(cluster_state))
            with patch("scripts.seed_from_backups.get_resource_path", return_value=str(data_dir)):
                seeded = perform_seeding(self.conn)

        self.assertTrue(seeded)

        rows = self.conn.execute("SELECT menu_item_id, type FROM menu_items").fetchall()
        types_by_id = {row["menu_item_id"]: row["type"] for row in rows}
        self.assertEqual(types_by_id, {"item_family_tub": "Ice Cream"})
        self.assertNotIn("item_legacy_vanilla", types_by_id)


if __name__ == "__main__":
    unittest.main()
