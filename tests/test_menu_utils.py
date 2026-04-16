import json
import sqlite3
import unittest
from unittest.mock import patch

from utils import menu_utils


class MenuUtilsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                order_status TEXT NOT NULL
            );

            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                suggestion_id TEXT,
                total_sold INTEGER DEFAULT 0,
                total_revenue REAL DEFAULT 0,
                sold_as_item INTEGER DEFAULT 0,
                sold_as_addon INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            );

            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                updated_at TEXT
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

            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                order_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT,
                quantity INTEGER DEFAULT 1,
                total_price REAL DEFAULT 0,
                name_raw TEXT,
                updated_at TEXT
            );

            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT,
                quantity INTEGER DEFAULT 0,
                price REAL DEFAULT 0
            );

            CREATE TABLE merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                affected_order_items TEXT NOT NULL,
                merged_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE item_forecast_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL
            );

            CREATE TABLE item_backtest_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL
            );

            CREATE TABLE volume_forecast_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL
            );

            CREATE TABLE volume_backtest_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL
            );
            """
        )
        self.conn.execute("INSERT INTO orders (order_id, order_status) VALUES (1, 'Success')")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _insert_cache_rows(self, *item_ids: str) -> None:
        for item_id in item_ids:
            self.conn.execute("INSERT INTO item_forecast_cache (item_id) VALUES (?)", (item_id,))
            self.conn.execute("INSERT INTO item_backtest_cache (item_id) VALUES (?)", (item_id,))
            self.conn.execute("INSERT INTO volume_forecast_cache (item_id) VALUES (?)", (item_id,))
            self.conn.execute("INSERT INTO volume_backtest_cache (item_id) VALUES (?)", (item_id,))
        self.conn.commit()

    def _seed_basic_merge_fixture(self) -> None:
        self.conn.executemany(
            """
            INSERT INTO menu_items (
                menu_item_id, name, type, is_verified, suggestion_id,
                total_sold, total_revenue, sold_as_item, sold_as_addon
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("item_source", "Iced Coffee", "Beverage", 1, None, 2, 210.0, 2, 1),
                ("item_target", "Cold Coffee", "Beverage", 1, None, 5, 500.0, 5, 0),
                ("item_dependent", "Coffee Suggestion", "Beverage", 0, "item_source", 0, 0, 0, 0),
            ],
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES ('variant_small', 'SMALL', 1)"
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES ('variant_1_piece', '1_PIECE', 1)"
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (1, 'item_source', 'variant_small', 1)
            """
        )
        self.conn.execute(
            """
            INSERT INTO order_items (order_item_id, order_id, menu_item_id, variant_id, quantity, total_price, name_raw)
            VALUES (1, 1, 'item_source', 'variant_small', 2, 200.0, 'Iced Coffee')
            """
        )
        self.conn.execute(
            """
            INSERT INTO order_item_addons (order_item_addon_id, order_item_id, menu_item_id, variant_id, quantity, price)
            VALUES (1, 1, 'item_source', 'variant_small', 1, 10.0)
            """
        )
        self.conn.commit()

    def _seed_resolution_fixture(self) -> None:
        self.conn.executemany(
            """
            INSERT INTO menu_items (
                menu_item_id, name, type, is_verified, suggestion_id,
                total_sold, total_revenue, sold_as_item, sold_as_addon
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("item_source", "Cookie Dough Scoop", "Dessert", 0, None, 1, 95.0, 1, 0),
                ("item_target", "Cookie Dough Sundae", "Dessert", 1, None, 3, 270.0, 3, 0),
                ("item_dependent", "Dessert Suggestion", "Dessert", 0, "item_source", 0, 0, 0, 0),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES (?, ?, 1)
            """,
            [
                ("variant_single", "1_PIECE"),
                ("variant_double", "2_PIECES"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (?, ?, ?, ?)
            """,
            [
                (1, "item_source", "variant_single", 0),
                (2, "item_target", "variant_double", 1),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO order_items (order_item_id, order_id, menu_item_id, variant_id, quantity, total_price, name_raw)
            VALUES (?, 1, ?, ?, ?, ?, ?)
            """,
            [
                (1, "item_source", "variant_single", 1, 90.0, "Cookie Dough Scoop"),
                (2, "item_target", "variant_double", 2, 180.0, "Cookie Dough Sundae"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO order_item_addons (order_item_addon_id, order_item_id, menu_item_id, variant_id, quantity, price)
            VALUES (1, 1, 'item_source', 'variant_single', 1, 5.0)
            """
        )
        self.conn.commit()

    def _seed_multi_variant_resolution_fixture(self) -> None:
        self.conn.executemany(
            """
            INSERT INTO menu_items (
                menu_item_id, name, type, is_verified, suggestion_id,
                total_sold, total_revenue, sold_as_item, sold_as_addon
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("item_source", "Strawberry Sundae", "Dessert", 0, None, 3, 270.0, 3, 0),
                ("item_target", "Berry Sundae", "Dessert", 1, None, 2, 180.0, 2, 0),
                ("item_dependent", "Suggested Berry", "Dessert", 0, "item_source", 0, 0, 0, 0),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES (?, ?, 1)
            """,
            [
                ("variant_single", "1_PIECE"),
                ("variant_double", "2_PIECES"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (?, ?, ?, ?)
            """,
            [
                (1, "item_source", "variant_single", 0),
                (2, "item_source", "variant_double", 0),
                (3, "item_target", "variant_single", 1),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO order_items (order_item_id, order_id, menu_item_id, variant_id, quantity, total_price, name_raw)
            VALUES (?, 1, ?, ?, ?, ?, ?)
            """,
            [
                (1, "item_source", "variant_single", 1, 90.0, "Strawberry Sundae"),
                (2, "item_source", "variant_double", 2, 180.0, "Strawberry Sundae"),
                (3, "item_target", "variant_single", 1, 95.0, "Berry Sundae"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO order_item_addons (order_item_addon_id, order_item_id, menu_item_id, variant_id, quantity, price)
            VALUES (1, 2, 'item_source', 'variant_double', 1, 10.0)
            """
        )
        self.conn.commit()

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_merge_menu_items_retargets_suggestions_and_clears_caches(self, _mock_models, _mock_export) -> None:
        self._seed_basic_merge_fixture()
        self._insert_cache_rows("item_source", "item_target")

        result = menu_utils.merge_menu_items(
            self.conn,
            "item_source",
            "item_target",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            self.conn.execute(
                "SELECT suggestion_id FROM menu_items WHERE menu_item_id = 'item_dependent'"
            ).fetchone()[0],
            "item_target",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()[0],
            "item_target",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM item_forecast_cache"
            ).fetchone()[0],
            0,
        )
        payload = json.loads(
            self.conn.execute(
                "SELECT affected_order_items FROM merge_history WHERE merge_id = ?",
                (result["merge_id"],),
            ).fetchone()[0]
        )
        self.assertEqual(payload["kind"], "basic_merge_v1")
        self.assertEqual(payload["suggestion_refs"][0]["menu_item_id"], "item_dependent")

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_undo_merge_restores_suggestions_and_clears_caches(self, _mock_models, _mock_export) -> None:
        self._seed_basic_merge_fixture()
        merge_result = menu_utils.merge_menu_items(
            self.conn,
            "item_source",
            "item_target",
            emit_sync_event=False,
        )
        self._insert_cache_rows("item_source", "item_target")

        undo_result = menu_utils.undo_merge(
            self.conn,
            merge_result["merge_id"],
            emit_sync_event=False,
        )

        self.assertEqual(undo_result["status"], "success")
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT suggestion_id FROM menu_items WHERE menu_item_id = 'item_dependent'"
            ).fetchone()[0],
            "item_source",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()[0],
            "item_source",
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM item_forecast_cache").fetchone()[0],
            0,
        )

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_resolve_menu_item_variant_retargets_suggestions_and_clears_caches(self, _mock_models, _mock_export) -> None:
        self._seed_resolution_fixture()
        self._insert_cache_rows("item_source", "item_target")

        result = menu_utils.resolve_menu_item_variant(
            self.conn,
            source_menu_item_id="item_source",
            source_variant_id="variant_single",
            target_menu_item_id="item_target",
            target_variant_id="variant_double",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "success")
        self.assertIsNone(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
        )
        mapping_row = self.conn.execute(
            """
            SELECT menu_item_id, variant_id, is_verified
            FROM menu_item_variants
            WHERE order_item_id = 1
            """
        ).fetchone()
        self.assertEqual(tuple(mapping_row), ("item_target", "variant_double", 1))
        self.assertEqual(
            self.conn.execute(
                "SELECT suggestion_id FROM menu_items WHERE menu_item_id = 'item_dependent'"
            ).fetchone()[0],
            "item_target",
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM item_forecast_cache").fetchone()[0],
            0,
        )
        payload = json.loads(
            self.conn.execute(
                "SELECT affected_order_items FROM merge_history WHERE merge_id = ?",
                (result["merge_id"],),
            ).fetchone()[0]
        )
        self.assertEqual(payload["kind"], "resolution_variant_v1")
        self.assertEqual(payload["suggestion_refs"][0]["menu_item_id"], "item_dependent")

    def test_preview_merge_allows_same_item_when_source_variant_is_selected(self) -> None:
        self.conn.execute(
            """
            INSERT INTO menu_items (
                menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon
            )
            VALUES ('item_target', 'Brownie Box', 'Dessert', 1, 3, 280.0, 3, 0)
            """
        )
        self.conn.executemany(
            """
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES (?, ?, 1)
            """,
            [
                ("variant_single", "1_PIECE"),
                ("variant_double", "2_PIECES"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (?, 'item_target', ?, 1)
            """,
            [
                (1, "variant_single"),
                (2, "variant_double"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO order_items (order_item_id, order_id, menu_item_id, variant_id, quantity, total_price, name_raw)
            VALUES (?, 1, 'item_target', ?, ?, ?, ?)
            """,
            [
                (1, "variant_single", 1, 90.0, "Brownie Box"),
                (2, "variant_double", 2, 190.0, "Brownie Box"),
            ],
        )
        self.conn.commit()

        preview = menu_utils.preview_merge_menu_items(
            self.conn,
            "item_target",
            "item_target",
            "variant_single",
        )

        self.assertEqual(preview["status"], "success")
        self.assertEqual(preview["source_variants"][0]["variant_id"], "variant_single")

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_resolve_menu_item_variant_keeps_source_parent_when_other_variants_remain(
        self,
        _mock_models,
        _mock_export,
    ) -> None:
        self._seed_multi_variant_resolution_fixture()
        self._insert_cache_rows("item_source", "item_target")

        result = menu_utils.resolve_menu_item_variant(
            self.conn,
            source_menu_item_id="item_source",
            source_variant_id="variant_single",
            target_menu_item_id="item_target",
            target_variant_id="variant_single",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
        )
        remaining_source_mapping = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM menu_item_variants
            WHERE menu_item_id = 'item_source' AND variant_id = 'variant_double'
            """
        ).fetchone()[0]
        self.assertEqual(remaining_source_mapping, 1)
        target_single_mappings = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM menu_item_variants
            WHERE menu_item_id = 'item_target' AND variant_id = 'variant_single'
            """
        ).fetchone()[0]
        self.assertEqual(target_single_mappings, 2)
        self.assertEqual(
            self.conn.execute(
                "SELECT suggestion_id FROM menu_items WHERE menu_item_id = 'item_dependent'"
            ).fetchone()[0],
            "item_source",
        )

    @patch("utils.menu_utils.export_to_backups", return_value=True)
    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_resolve_menu_item_variant_deletes_source_parent_after_last_variant_moves_into_same_target_variant(
        self,
        _mock_models,
        _mock_export,
    ) -> None:
        self._seed_resolution_fixture()
        self._insert_cache_rows("item_source", "item_target")

        self.conn.execute(
            """
            UPDATE menu_item_variants
            SET variant_id = 'variant_single'
            WHERE order_item_id = 2
            """
        )
        self.conn.execute(
            """
            UPDATE order_items
            SET variant_id = 'variant_single'
            WHERE order_item_id = 2
            """
        )
        self.conn.commit()

        result = menu_utils.resolve_menu_item_variant(
            self.conn,
            source_menu_item_id="item_source",
            source_variant_id="variant_single",
            target_menu_item_id="item_target",
            target_variant_id="variant_single",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "success")
        self.assertIsNone(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
        )
        target_single_mappings = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM menu_item_variants
            WHERE menu_item_id = 'item_target' AND variant_id = 'variant_single'
            """
        ).fetchone()[0]
        self.assertEqual(target_single_mappings, 2)


if __name__ == "__main__":
    unittest.main()
