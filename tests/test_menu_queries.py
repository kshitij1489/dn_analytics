import sqlite3
import unittest

from src.core.queries import menu_queries


class MenuQueriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL
            );

            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL
            );

            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                price REAL DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                addon_eligible BOOLEAN DEFAULT 0,
                delivery_eligible BOOLEAN DEFAULT 1,
                is_verified BOOLEAN DEFAULT 0
            );

            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                menu_item_id TEXT,
                variant_id TEXT
            );

            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                menu_item_id TEXT,
                variant_id TEXT
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_fetch_menu_matrix_groups_duplicate_mapping_rows(self) -> None:
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type) VALUES ('item_1', 'Bean-to-Bar Dark Chocolate Ice Cream', 'Ice Cream')"
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name) VALUES ('variant_mini', 'MINI_TUB_200ML')"
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name) VALUES ('variant_regular', 'REGULAR_TUB_220GMS')"
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, price, is_active, addon_eligible, delivery_eligible
            )
            VALUES (?, 'item_1', ?, 0, 1, 0, 1)
            """,
            [
                ("51217617", "variant_mini"),
                ("51217635", "variant_mini"),
                ("51217674", "variant_mini"),
                ("1282581599", "variant_regular"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO order_items (menu_item_id, variant_id) VALUES ('item_1', ?)",
            [("variant_mini",), ("variant_mini",)],
        )
        self.conn.execute(
            "INSERT INTO order_item_addons (menu_item_id, variant_id) VALUES ('item_1', 'variant_mini')"
        )
        self.conn.commit()

        df = menu_queries.fetch_menu_matrix(self.conn)

        self.assertEqual(len(df), 2)

        mini_row = df[df["variant_name"] == "MINI_TUB_200ML"].iloc[0]
        regular_row = df[df["variant_name"] == "REGULAR_TUB_220GMS"].iloc[0]

        self.assertEqual(mini_row["name"], "Bean-to-Bar Dark Chocolate Ice Cream")
        self.assertEqual(mini_row["mapping_count"], 3)
        self.assertEqual(regular_row["mapping_count"], 1)
        self.assertEqual(mini_row["order_count"], 3)
        self.assertEqual(regular_row["order_count"], 0)


if __name__ == "__main__":
    unittest.main()
