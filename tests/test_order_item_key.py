import sqlite3
import unittest

from src.core.order_item_key import (
    AssignmentKeyIndex,
    has_local_pos_backing,
    local_addon_pks_for_assignment_key,
    local_order_item_pks_for_assignment_key,
    normalized_generated_name_key,
    update_local_order_rows_for_assignment_key,
)


class AssignmentKeyIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                petpooja_itemid INTEGER,
                name_raw TEXT
            )
            """
        )
        self.conn.executemany(
            "INSERT INTO order_items (order_item_id, petpooja_itemid, name_raw) VALUES (?, ?, ?)",
            [
                (1, 101, "Orange Ice Cream Scoop"),
                (2, 101, "Orange Ice Cream Scoop"),
                (3, None, "Mango Kulfi"),
                (4, None, None),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_index_matches_per_key_lookup_for_petpooja_key(self) -> None:
        index = AssignmentKeyIndex(self.conn)
        self.assertEqual(
            local_order_item_pks_for_assignment_key(self.conn, "101", key_index=index),
            local_order_item_pks_for_assignment_key(self.conn, "101"),
        )
        self.assertEqual(
            local_order_item_pks_for_assignment_key(self.conn, "101", key_index=index),
            [1, 2],
        )

    def test_index_matches_per_key_lookup_for_generated_name_key(self) -> None:
        key = normalized_generated_name_key("Mango Kulfi")
        index = AssignmentKeyIndex(self.conn)
        self.assertEqual(
            local_order_item_pks_for_assignment_key(self.conn, key, key_index=index),
            local_order_item_pks_for_assignment_key(self.conn, key),
        )
        self.assertEqual(
            local_order_item_pks_for_assignment_key(self.conn, key, key_index=index),
            [3],
        )

    def test_index_misses_return_empty(self) -> None:
        index = AssignmentKeyIndex(self.conn)
        self.assertEqual(
            local_order_item_pks_for_assignment_key(self.conn, "999", key_index=index),
            [],
        )

    def test_fallback_schema_without_pos_columns_uses_pk(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE order_items (order_item_id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO order_items (order_item_id) VALUES (7)")
        index = AssignmentKeyIndex(conn)
        self.assertEqual(
            local_order_item_pks_for_assignment_key(conn, "7", key_index=index),
            [7],
        )
        conn.close()


class AddonAssignmentKeyTests(unittest.TestCase):
    """Addon rows resolve by their OWN key, never by parent-line membership."""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                petpooja_itemid INTEGER,
                name_raw TEXT,
                menu_item_id TEXT,
                variant_id TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER NOT NULL,
                petpooja_addonid TEXT,
                name_raw TEXT,
                menu_item_id TEXT,
                variant_id TEXT
            )
            """
        )
        # Combo line (POS itemid 555) with two flavor addons + one cone addon.
        self.conn.execute(
            "INSERT INTO order_items VALUES (1, 555, 'Scoop Regular Half In Half Combo', 'combo-old', 'v-old')"
        )
        self.conn.executemany(
            "INSERT INTO order_item_addons VALUES (?, ?, ?, ?, ?, ?)",
            [
                (10, 1, None, "Cherry & Chocolate (60gm)", "flavor-cherry", "v-60"),
                (11, 1, None, "Salted Caramel (60gm)", "flavor-caramel", "v-60"),
                (12, 1, "777", "Waffle Cone", "cone", "v-piece"),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_parent_key_update_leaves_addons_alone(self) -> None:
        update_local_order_rows_for_assignment_key(
            self.conn,
            "555",
            menu_item_id="combo-new",
            variant_id="v-new",
            variant_specified=True,
        )
        row = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM order_items WHERE order_item_id = 1"
        ).fetchone()
        self.assertEqual((row[0], row[1]), ("combo-new", "v-new"))
        addons = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM order_item_addons ORDER BY order_item_addon_id"
        ).fetchall()
        self.assertEqual(
            [(r[0], r[1]) for r in addons],
            [("flavor-cherry", "v-60"), ("flavor-caramel", "v-60"), ("cone", "v-piece")],
        )

    def test_addon_name_key_updates_matching_addons_only(self) -> None:
        key = normalized_generated_name_key("Cherry & Chocolate (60gm)")
        update_local_order_rows_for_assignment_key(
            self.conn,
            key,
            menu_item_id="flavor-cherry-new",
            variant_id="v-60-new",
            variant_specified=True,
        )
        addons = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM order_item_addons ORDER BY order_item_addon_id"
        ).fetchall()
        self.assertEqual(
            [(r[0], r[1]) for r in addons],
            [
                ("flavor-cherry-new", "v-60-new"),
                ("flavor-caramel", "v-60"),
                ("cone", "v-piece"),
            ],
        )
        combo = self.conn.execute(
            "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
        ).fetchone()
        self.assertEqual(combo[0], "combo-old")

    def test_addon_pos_id_key_matches(self) -> None:
        self.assertEqual(
            local_addon_pks_for_assignment_key(self.conn, "777"),
            [12],
        )

    def test_addon_index_matches_per_key_lookup(self) -> None:
        key = normalized_generated_name_key("Salted Caramel (60gm)")
        index = AssignmentKeyIndex(self.conn)
        self.assertEqual(
            local_addon_pks_for_assignment_key(self.conn, key, key_index=index),
            local_addon_pks_for_assignment_key(self.conn, key),
        )
        self.assertEqual(
            local_addon_pks_for_assignment_key(self.conn, key, key_index=index),
            [11],
        )

    def test_addon_only_key_counts_as_pos_backing(self) -> None:
        key = normalized_generated_name_key("Waffle Cone")
        self.assertTrue(has_local_pos_backing(self.conn, key))
        self.assertFalse(has_local_pos_backing(self.conn, "no-such-key"))


if __name__ == "__main__":
    unittest.main()
