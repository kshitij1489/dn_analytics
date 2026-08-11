import sqlite3
import unittest

from services.clustering_service import OrderItemCluster
from utils.clean_order_item import clean_order_item_name
from utils.id_generator import generate_deterministic_id


class CleanOrderItemTests(unittest.TestCase):
    def _seed_butter_waffle_cones_target(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
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
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                is_verified BOOLEAN DEFAULT 0
            );
            """
        )

        target_menu_item_id = generate_deterministic_id("Butter Waffle Cones", "Extra")
        conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified, suggestion_id)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (target_menu_item_id, "Butter Waffle Cones", "Extra", 1),
        )
        conn.commit()
        return conn

    def test_plain_waffle_cone_normalizes_to_butter_waffle_cones_target(self) -> None:
        result = clean_order_item_name("Waffle Cone")

        self.assertEqual(
            result,
            {
                "name": "Butter Waffle Cones",
                "type": "Extra",
                "variant": "1_PIECE",
            },
        )

    def test_bracketed_butter_waffle_cone_two_pieces_normalizes_to_plural_target(self) -> None:
        result = clean_order_item_name("Butter Waffle Cone [2 Pieces]")

        self.assertEqual(
            result,
            {
                "name": "Butter Waffle Cones",
                "type": "Extra",
                "variant": "2_PIECES",
            },
        )

    def test_standalone_known_size_checks_are_bounded(self) -> None:
        cases = [
            ("Chocolate Ice Cream 1160gm", "UNKNOWN_1160GMS"),
            ("Chocolate Ice Cream 1400gm", "UNKNOWN_1400GMS"),
            ("Chocolate Ice Cream 11kg", "UNKNOWN_11KG"),
        ]

        for raw_name, expected_variant in cases:
            with self.subTest(raw_name=raw_name):
                result = clean_order_item_name(raw_name)

                self.assertEqual(result["name"], "Chocolate Ice Cream")
                self.assertEqual(result["variant"], expected_variant)

    def test_standalone_known_sizes_still_resolve(self) -> None:
        cases = [
            ("Chocolate Ice Cream 160gm", "MINI_TUB_160GMS"),
            ("Chocolate Ice Cream 400gm", "400GMS"),
            ("Chocolate Ice Cream 1kg", "1KG"),
        ]

        for raw_name, expected_variant in cases:
            with self.subTest(raw_name=raw_name):
                result = clean_order_item_name(raw_name)

                self.assertEqual(result["name"], "Chocolate Ice Cream")
                self.assertEqual(result["variant"], expected_variant)

    def test_mini_indulgence_respects_unexpected_explicit_size(self) -> None:
        cases = [
            ("Chocolate Ice Cream (Mini Indulgence)", "MINI_TUB_200ML"),
            ("Chocolate Ice Cream (Mini Indulgence (200ml))", "MINI_TUB_200ML"),
            ("Chocolate Ice Cream (Mini Indulgence (250ml))", "UNKNOWN_250ML"),
            ("Chocolate Ice Cream (Mini Indulgence (1200ml))", "UNKNOWN_1200ML"),
        ]

        for raw_name, expected_variant in cases:
            with self.subTest(raw_name=raw_name):
                result = clean_order_item_name(raw_name)

                self.assertEqual(result["name"], "Chocolate Ice Cream")
                self.assertEqual(result["variant"], expected_variant)

    def test_cluster_add_reuses_existing_butter_waffle_cones_cluster_for_bracketed_label(self) -> None:
        conn = self._seed_butter_waffle_cones_target()
        try:
            target_menu_item_id = generate_deterministic_id("Butter Waffle Cones", "Extra")
            cluster = OrderItemCluster(conn)
            menu_item_id, order_item_id, variant_id, item_type, *_ = cluster.add(
                "Butter Waffle Cone [2 Pieces]",
                "NEW_BRACKET_2PC_ID",
            )

            self.assertEqual(menu_item_id, target_menu_item_id)
            self.assertEqual(order_item_id, "NEW_BRACKET_2PC_ID")
            self.assertEqual(variant_id, generate_deterministic_id("2_PIECES"))
            self.assertEqual(item_type, "Extra")

            mapping_row = conn.execute(
                """
                SELECT menu_item_id, variant_id, is_verified
                FROM menu_item_variants
                WHERE order_item_id = ?
                """,
                ("NEW_BRACKET_2PC_ID",),
            ).fetchone()
            self.assertEqual(mapping_row, (target_menu_item_id, generate_deterministic_id("2_PIECES"), 0))
        finally:
            conn.close()

    def test_cluster_add_reuses_existing_butter_waffle_cones_cluster_for_plain_waffle_cone(self) -> None:
        target_menu_item_id = generate_deterministic_id("Butter Waffle Cones", "Extra")
        expected_variant_id = generate_deterministic_id("1_PIECE")

        for order_item_id in (None, "53392898"):
            conn = self._seed_butter_waffle_cones_target()
            try:
                cluster = OrderItemCluster(conn)
                menu_item_id, resolved_order_item_id, variant_id, item_type, *_ = cluster.add(
                    "Waffle Cone",
                    order_item_id,
                )

                if order_item_id is None:
                    self.assertEqual(resolved_order_item_id, generate_deterministic_id("generated_Waffle Cone"))
                else:
                    self.assertEqual(resolved_order_item_id, order_item_id)

                self.assertEqual(menu_item_id, target_menu_item_id)
                self.assertEqual(variant_id, expected_variant_id)
                self.assertEqual(item_type, "Extra")

                mapping_row = conn.execute(
                    """
                    SELECT menu_item_id, variant_id, is_verified
                    FROM menu_item_variants
                    WHERE order_item_id = ?
                    """,
                    (resolved_order_item_id,),
                ).fetchone()
                self.assertEqual(mapping_row, (target_menu_item_id, expected_variant_id, 0))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
