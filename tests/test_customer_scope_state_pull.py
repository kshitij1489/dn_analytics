import sqlite3
import unittest
from unittest.mock import patch

from src.core.customer_merge_sync import pull_and_apply_customer_merge_events
from src.core.sync_identity import get_customer_state_revision, get_customer_strict_mode_enabled


class CustomerMergeScopeStatePullTests(unittest.TestCase):
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

    @patch("src.core.customer_merge_sync._fetch_remote_events")
    def test_pull_stores_customer_revision_and_strict_flag(self, fetch_remote) -> None:
        fetch_remote.return_value = {
            "events": [],
            "next_cursor": "cursor-1",
            "has_more": False,
            "scope_state": {"customer_revision": 99, "strict_mode_enabled": True},
            "error": None,
        }

        result = pull_and_apply_customer_merge_events(
            self.conn,
            "https://cloud.example/customer-merges",
            auth="secret",
        )

        self.assertIsNone(result.get("error"))
        self.assertEqual(get_customer_state_revision(self.conn), 99)
        self.assertTrue(get_customer_strict_mode_enabled(self.conn))

    @patch("src.core.customer_merge_sync._fetch_remote_events")
    def test_pull_does_not_store_revision_while_page_has_more(self, fetch_remote) -> None:
        fetch_remote.return_value = {
            "events": [{"event_type": "customer_merge.applied", "remote_event_id": "e1"}],
            "next_cursor": "cursor-1",
            "has_more": True,
            "scope_state": {"customer_revision": 99, "strict_mode_enabled": True},
            "error": None,
        }

        with patch(
            "src.core.customer_merge_sync._apply_remote_merge_event",
            return_value={"status": "skipped"},
        ):
            result = pull_and_apply_customer_merge_events(
                self.conn,
                "https://cloud.example/customer-merges",
                auth="secret",
            )

        self.assertIsNone(result.get("error"))
        self.assertIsNone(get_customer_state_revision(self.conn))
        self.assertTrue(get_customer_strict_mode_enabled(self.conn))


if __name__ == "__main__":
    unittest.main()
