import sqlite3
import unittest

from src.core.sync_identity import (
    apply_customer_scope_state,
    apply_menu_scope_state,
    get_customer_state_revision,
    get_customer_strict_mode_enabled,
    get_menu_state_revision,
    get_menu_strict_mode_enabled,
    set_customer_state_revision,
    set_menu_state_revision,
    set_menu_strict_mode_enabled,
)


class SyncIdentityMenuScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def test_menu_state_revision_starts_unseen(self) -> None:
        self.assertIsNone(get_menu_state_revision(self.conn))

    def test_apply_menu_scope_state_stores_revision_and_strict_flag(self) -> None:
        apply_menu_scope_state(
            self.conn,
            {"menu_revision": 42, "strict_mode_enabled": True},
        )
        self.assertEqual(get_menu_state_revision(self.conn), 42)
        self.assertTrue(get_menu_strict_mode_enabled(self.conn))

    def test_menu_state_revision_advances_monotonically(self) -> None:
        set_menu_state_revision(self.conn, 10)
        set_menu_state_revision(self.conn, 7)
        self.assertEqual(get_menu_state_revision(self.conn), 10)
        set_menu_state_revision(self.conn, 15)
        self.assertEqual(get_menu_state_revision(self.conn), 15)

    def test_apply_menu_scope_state_does_not_rewind_revision(self) -> None:
        set_menu_state_revision(self.conn, 20)
        apply_menu_scope_state(self.conn, {"menu_revision": 12, "strict_mode_enabled": False})
        self.assertEqual(get_menu_state_revision(self.conn), 20)
        self.assertFalse(get_menu_strict_mode_enabled(self.conn))

    def test_strict_mode_flag_is_overwritten(self) -> None:
        set_menu_strict_mode_enabled(self.conn, True)
        apply_menu_scope_state(self.conn, {"menu_revision": 1, "strict_mode_enabled": False})
        self.assertFalse(get_menu_strict_mode_enabled(self.conn))


class SyncIdentityCustomerScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def test_customer_state_revision_starts_unseen(self) -> None:
        self.assertIsNone(get_customer_state_revision(self.conn))

    def test_apply_customer_scope_state_stores_revision_and_strict_flag(self) -> None:
        apply_customer_scope_state(
            self.conn,
            {"customer_revision": 42, "strict_mode_enabled": True},
        )
        self.assertEqual(get_customer_state_revision(self.conn), 42)
        self.assertTrue(get_customer_strict_mode_enabled(self.conn))

    def test_customer_state_revision_advances_monotonically(self) -> None:
        set_customer_state_revision(self.conn, 10)
        set_customer_state_revision(self.conn, 7)
        self.assertEqual(get_customer_state_revision(self.conn), 10)
        set_customer_state_revision(self.conn, 15)
        self.assertEqual(get_customer_state_revision(self.conn), 15)

    def test_apply_customer_scope_state_does_not_rewind_revision(self) -> None:
        set_customer_state_revision(self.conn, 20)
        apply_customer_scope_state(
            self.conn,
            {"customer_revision": 12, "strict_mode_enabled": False},
        )
        self.assertEqual(get_customer_state_revision(self.conn), 20)
        self.assertFalse(get_customer_strict_mode_enabled(self.conn))

if __name__ == "__main__":
    unittest.main()
