import json
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.seed_from_backups import export_to_backups, main, perform_seeding


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
            exported = export_to_backups(self.conn, out_dir=data_dir)

            self.assertTrue(exported)

            id_maps = json.loads((data_dir / "id_maps_backup.json").read_text())
            self.assertEqual(
                id_maps["variant_id_to_meta"]["variant_family_tub"],
                {"unit": "GMS", "value": 500},
            )

    def test_perform_seeding_returns_false_when_backup_files_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertFalse(perform_seeding(self.conn, archive_dir=tmp_dir))

    def test_perform_seeding_loads_json_and_delegates_to_seed_catalog(self) -> None:
        id_maps = {
            "menu_id_to_str": {"item_family_tub": "Family Tub"},
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
            with patch(
                "scripts.seed_from_backups.seed_catalog",
                return_value={
                    "items_seeded": 1,
                    "variants_seeded": 1,
                    "mappings_seeded": 0,
                    "stub_count": 0,
                    "skipped_unmapped": 0,
                },
            ) as seed_catalog:
                seeded = perform_seeding(self.conn, archive_dir=data_dir)

        self.assertTrue(seeded)
        seed_catalog.assert_called_once_with(
            self.conn,
            id_maps,
            cluster_state,
            seed_mappings=False,
        )

    def test_full_restore_rebuilds_itemcode_projection(self) -> None:
        id_maps = {
            "menu_id_to_str": {"item_family_tub": "Family Tub"},
            "variant_id_to_str": {"variant_family_tub": "FAMILY_TUB_500GMS"},
            "type_id_to_str": {"type_ice_cream": "Ice Cream"},
        }
        cluster_state = {
            "item_family_tub:type_ice_cream": {"101": [["101", "variant_family_tub"]]},
        }

        class RebuildResult:
            def summary(self) -> str:
                return "restaurants=0 codes=0 active=0 conflicts=0 stale_removed=0"

        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir)
            (data_dir / "id_maps_backup.json").write_text(json.dumps(id_maps))
            (data_dir / "cluster_state_backup.json").write_text(json.dumps(cluster_state))
            with patch(
                "scripts.seed_from_backups.seed_catalog",
                return_value={
                    "items_seeded": 1,
                    "variants_seeded": 1,
                    "mappings_seeded": 1,
                    "stub_count": 0,
                    "skipped_unmapped": 0,
                },
            ), patch(
                "src.core.itemcode_mapping.rebuild_itemcode_mappings_best_effort",
                return_value=RebuildResult(),
            ) as rebuild:
                seeded = perform_seeding(
                    self.conn,
                    seed_mappings=True,
                    archive_dir=data_dir,
                )

        self.assertTrue(seeded)
        rebuild.assert_called_once_with(self.conn)

    def test_cli_no_args_is_not_an_implicit_restore(self) -> None:
        with patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as raised:
                main([])

        self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
