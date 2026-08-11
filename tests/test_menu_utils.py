import json
import sqlite3
import unittest
from unittest.mock import patch

from src.core.menu_mutation_commit import (
    CommitResult,
    MUTATION_TYPE_CATALOG_UPDATE,
    apply_accepted,
)
from src.core.sync_identity import set_menu_state_revision
from utils import menu_utils
from utils.id_generator import generate_deterministic_id


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

    def _enable_strict_commit_fixture(self) -> None:
        set_menu_state_revision(self.conn, 41)
        self.conn.commit()

    def _accept_catalog_plan(self, captured_plan: dict, before_apply=None):
        def _fake_commit(conn, plan):
            captured_plan["plan"] = plan
            if before_apply:
                before_apply(conn, plan)
            accepted = {
                "status": "accepted",
                "mutation_id": plan.mutation_id,
                "menu_revision": 42,
                "accepted_events": [],
                "assignment_rows": [],
                "catalog_delta": plan.catalog_delta,
                "merge_cursor": "0",
                "verification_cursor": "0",
            }
            apply_accepted(conn, accepted, plan)
            return CommitResult(status="ok")

        return _fake_commit

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

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_merge_menu_items_retargets_suggestions_and_clears_caches(self, _mock_models) -> None:
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

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_undo_merge_restores_suggestions_and_clears_caches(self, _mock_models) -> None:
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

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_resolve_menu_item_variant_retargets_suggestions_and_clears_caches(self, _mock_models) -> None:
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

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_resolve_menu_item_variant_keeps_source_parent_when_other_variants_remain(
        self,
        _mock_models,
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

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_resolve_menu_item_variant_deletes_source_parent_after_last_variant_moves_into_same_target_variant(
        self,
        _mock_models,
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

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_retype_menu_item_moves_item_into_new_typed_identity(self, _mock_models) -> None:
        self._seed_basic_merge_fixture()
        self._insert_cache_rows("item_source")

        result = menu_utils.retype_menu_item(
            self.conn,
            "item_source",
            "Food",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["message"],
            "Changed type of 'Iced Coffee' from 'Beverage' to 'Food'",
        )

        expected_target_id = generate_deterministic_id("Iced Coffee", "Food")
        target = self.conn.execute(
            "SELECT name, type, is_verified FROM menu_items WHERE menu_item_id = ?",
            (expected_target_id,),
        ).fetchone()
        self.assertIsNotNone(target)
        self.assertEqual(target[0], "Iced Coffee")
        self.assertEqual(target[1], "Food")
        self.assertEqual(target[2], 1)

        # Source identity is gone; every linked row follows the new identity.
        self.assertIsNone(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()[0],
            expected_target_id,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM order_item_addons WHERE order_item_addon_id = 1"
            ).fetchone()[0],
            expected_target_id,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = 1"
            ).fetchone()[0],
            expected_target_id,
        )
        # Variant is untouched by a type change.
        self.assertEqual(
            self.conn.execute(
                "SELECT variant_id FROM menu_item_variants WHERE order_item_id = 1"
            ).fetchone()[0],
            "variant_small",
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM item_forecast_cache").fetchone()[0],
            0,
        )

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_retype_menu_item_consolidates_into_existing_same_name_item(self, _mock_models) -> None:
        self._seed_basic_merge_fixture()
        existing_target_id = generate_deterministic_id("Iced Coffee", "Food")
        self.conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon)
            VALUES (?, 'Iced Coffee', 'Food', 1, 3, 300.0, 3, 0)
            """,
            (existing_target_id,),
        )
        self.conn.commit()

        result = menu_utils.retype_menu_item(
            self.conn,
            "item_source",
            "Food",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "success")
        target_stats = self.conn.execute(
            "SELECT total_sold, total_revenue FROM menu_items WHERE menu_item_id = ?",
            (existing_target_id,),
        ).fetchone()
        self.assertEqual(target_stats[0], 5)
        self.assertEqual(target_stats[1], 510.0)
        self.assertIsNone(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
        )

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_retype_menu_item_is_undoable(self, _mock_models) -> None:
        self._seed_basic_merge_fixture()

        result = menu_utils.retype_menu_item(
            self.conn,
            "item_source",
            "Food",
            emit_sync_event=False,
        )
        self.assertEqual(result["status"], "success")

        undo_result = menu_utils.undo_merge(
            self.conn,
            result["merge_id"],
            emit_sync_event=False,
        )

        self.assertEqual(undo_result["status"], "success")
        restored = self.conn.execute(
            "SELECT name, type FROM menu_items WHERE menu_item_id = 'item_source'"
        ).fetchone()
        self.assertIsNotNone(restored)
        self.assertEqual(restored[0], "Iced Coffee")
        self.assertEqual(restored[1], "Beverage")
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()[0],
            "item_source",
        )

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_retype_menu_item_flips_type_in_place_when_id_already_matches_target_identity(
        self,
        _mock_models,
    ) -> None:
        # Simulate label drift: the row's ID encodes (name, 'Ice Cream') but an
        # earlier in-place edit left the type column saying 'Dessert'.
        drifted_id = generate_deterministic_id("Coffee With Almonds", "Ice Cream")
        self.conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES (?, 'Coffee With Almonds', 'Dessert', 1)
            """,
            (drifted_id,),
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES ('variant_small', 'SMALL', 1)"
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (1, ?, 'variant_small', 1)
            """,
            (drifted_id,),
        )
        self.conn.commit()

        result = menu_utils.retype_menu_item(
            self.conn,
            drifted_id,
            "Ice Cream",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["message"],
            "Changed type of 'Coffee With Almonds' from 'Dessert' to 'Ice Cream'",
        )
        row = self.conn.execute(
            "SELECT type FROM menu_items WHERE menu_item_id = ?",
            (drifted_id,),
        ).fetchone()
        self.assertEqual(row[0], "Ice Cream")
        # In-place flip: no merge recorded, mapping untouched.
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = 1"
            ).fetchone()[0],
            drifted_id,
        )

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    def test_strict_retype_in_place_uses_catalog_update_before_local_write(self, _cloud_config) -> None:
        drifted_id = generate_deterministic_id("Coffee With Almonds", "Ice Cream")
        self.conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES (?, 'Coffee With Almonds', 'Dessert', 1)
            """,
            (drifted_id,),
        )
        self._enable_strict_commit_fixture()
        captured: dict = {}

        def _before_apply(conn, plan):
            self.assertEqual(plan.mutation_type, MUTATION_TYPE_CATALOG_UPDATE)
            self.assertEqual(plan.order_item_ids, [])
            row = conn.execute(
                "SELECT type FROM menu_items WHERE menu_item_id = ?",
                (drifted_id,),
            ).fetchone()
            self.assertEqual(row["type"], "Dessert")

        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=self._accept_catalog_plan(captured, _before_apply),
        ):
            result = menu_utils.retype_menu_item(self.conn, drifted_id, "Ice Cream")

        self.assertEqual(result["status"], "success", result)
        plan = captured["plan"]
        self.assertEqual(plan.catalog_delta["items"][0]["type"], "Ice Cream")
        row = self.conn.execute(
            "SELECT type FROM menu_items WHERE menu_item_id = ?",
            (drifted_id,),
        ).fetchone()
        self.assertEqual(row["type"], "Ice Cream")

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    def test_strict_catalog_only_verify_uses_catalog_update(self, _cloud_config) -> None:
        self.conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES ('item_empty', 'Mystery Scoop', 'Dessert', 0)
            """
        )
        self._enable_strict_commit_fixture()
        captured: dict = {}

        def _before_apply(conn, plan):
            self.assertEqual(plan.mutation_type, MUTATION_TYPE_CATALOG_UPDATE)
            self.assertEqual(plan.verification_events, [])
            self.assertEqual(plan.order_item_ids, [])
            row = conn.execute(
                "SELECT is_verified FROM menu_items WHERE menu_item_id = 'item_empty'"
            ).fetchone()
            self.assertEqual(int(row["is_verified"]), 0)

        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=self._accept_catalog_plan(captured, _before_apply),
        ):
            result = menu_utils.verify_item(self.conn, "item_empty")

        self.assertEqual(result["status"], "success", result)
        self.assertTrue(captured["plan"].catalog_delta["items"][0]["is_verified"])
        row = self.conn.execute(
            "SELECT is_verified FROM menu_items WHERE menu_item_id = 'item_empty'"
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    def test_strict_variant_create_uses_catalog_update(self, _cloud_config) -> None:
        self.conn.execute("ALTER TABLE variants ADD COLUMN description TEXT")
        self.conn.execute("ALTER TABLE variants ADD COLUMN unit TEXT")
        self.conn.execute("ALTER TABLE variants ADD COLUMN value REAL")
        self._enable_strict_commit_fixture()
        captured: dict = {}

        def _before_apply(conn, plan):
            self.assertEqual(plan.mutation_type, MUTATION_TYPE_CATALOG_UPDATE)
            self.assertEqual(plan.order_item_ids, [])
            self.assertIsNone(
                conn.execute(
                    "SELECT variant_id FROM variants WHERE variant_name = 'FAMILY_TUB'"
                ).fetchone()
            )

        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=self._accept_catalog_plan(captured, _before_apply),
        ):
            result = menu_utils.create_variant_type(
                self.conn,
                "Family Tub",
                description="Share pack",
                unit="GMS",
                value=500,
            )

        self.assertEqual(result["status"], "success", result)
        variant_delta = captured["plan"].catalog_delta["variants"][0]
        self.assertEqual(variant_delta["variant_name"], "FAMILY_TUB")
        self.assertEqual(variant_delta["description"], "Share pack")
        self.assertEqual(variant_delta["unit"], "GMS")
        self.assertEqual(variant_delta["value"], 500)
        row = self.conn.execute(
            "SELECT variant_name, description, unit, value, is_verified FROM variants WHERE variant_id = ?",
            (result["variant_id"],),
        ).fetchone()
        self.assertEqual(row["variant_name"], "FAMILY_TUB")
        self.assertEqual(row["description"], "Share pack")
        self.assertEqual(row["unit"], "GMS")
        self.assertEqual(row["value"], 500)
        self.assertEqual(int(row["is_verified"]), 1)

    def test_retype_menu_item_rejects_same_type(self) -> None:
        self._seed_basic_merge_fixture()

        result = menu_utils.retype_menu_item(
            self.conn,
            "item_source",
            "Beverage",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("matches the current type", result["message"])

    def test_retype_menu_item_rejects_missing_item(self) -> None:
        result = menu_utils.retype_menu_item(
            self.conn,
            "item_missing",
            "Food",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("not found", result["message"])

    def test_retype_menu_item_rejects_blank_type(self) -> None:
        self._seed_basic_merge_fixture()

        result = menu_utils.retype_menu_item(
            self.conn,
            "item_source",
            "   ",
            emit_sync_event=False,
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("required", result["message"])


if __name__ == "__main__":
    unittest.main()
