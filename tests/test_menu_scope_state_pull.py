import sqlite3
import unittest
from unittest.mock import patch

from src.core.menu_merge_sync import pull_and_apply_menu_merge_events
from src.core.sync_identity import get_menu_state_revision, get_menu_strict_mode_enabled


class MenuMergeScopeStatePullTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.commit()

    @patch("src.core.menu_merge_sync._fetch_remote_events")
    @patch("src.core.menu_merge_sync.retry_quarantined_menu_merge_events", return_value={"attempted": 0, "resolved": 0})
    @patch("src.core.menu_merge_sync.is_assignment_apply_enabled", return_value=False)
    def test_pull_stores_menu_revision_and_strict_flag(
        self,
        _assignment_enabled,
        _retry,
        fetch_remote,
    ) -> None:
        fetch_remote.return_value = {
            "events": [],
            "next_cursor": "cursor-1",
            "scope_state": {"menu_revision": 99, "strict_mode_enabled": True},
            "error": None,
        }

        result = pull_and_apply_menu_merge_events(
            self.conn,
            "https://cloud.example/menu-merges",
            auth="secret",
        )

        self.assertIsNone(result.get("error"))
        self.assertIsNone(get_menu_state_revision(self.conn))
        self.assertTrue(get_menu_strict_mode_enabled(self.conn))


if __name__ == "__main__":
    unittest.main()
