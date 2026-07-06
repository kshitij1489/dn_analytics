"""Phase 6 rollout: legacy customer outbox drain and non-strict client behavior."""

import sqlite3
import unittest
from unittest.mock import patch

from src.core.customer_mutation_commit import strict_mode_active
from src.core.customer_outbox_drain import drain_customer_outbox, get_customer_outbox_status
from src.core.queries.customer_merge_queries import merge_customers
from src.core.sync_identity import set_customer_state_revision, set_customer_strict_mode_enabled
from tests.test_customer_mutation_commit import _customer_merge_db


def _conn_with_outbox_table() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE customer_merge_sync_events (
            event_id TEXT PRIMARY KEY,
            merge_id INTEGER,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            upload_attempted_at TEXT,
            uploaded_at TEXT,
            last_error TEXT
        );
        """
    )
    conn.commit()
    return conn


class CustomerOutboxDrainTests(unittest.TestCase):
    def test_status_reports_unsent_counts(self) -> None:
        conn = _conn_with_outbox_table()
        conn.execute(
            """
            INSERT INTO customer_merge_sync_events (event_id, event_type, payload, occurred_at)
            VALUES ('evt-1', 'customer_merge.applied', '{"remote_event_id":"evt-1"}', '2026-07-06T10:00:00Z')
            """
        )
        conn.commit()
        status = get_customer_outbox_status(conn)
        self.assertEqual(status["customer_merge_unsent"], 1)
        self.assertFalse(status["outbox_drained"])
        conn.close()

    @patch("src.core.customer_outbox_drain.get_cloud_sync_config", return_value=(None, None))
    def test_drain_errors_without_cloud_config(self, _cfg) -> None:
        conn = _conn_with_outbox_table()
        result = drain_customer_outbox(conn)
        self.assertEqual(result["status"], "error")
        self.assertIn("Cloud sync URL", result["message"])
        conn.close()

    @patch("src.core.customer_outbox_drain.upload_customer_merge_events")
    @patch(
        "src.core.customer_outbox_drain.get_cloud_sync_config",
        return_value=("https://cloud.example", "secret"),
    )
    def test_drain_uploads_until_empty(self, _mock_cfg, mock_upload) -> None:
        conn = _conn_with_outbox_table()
        conn.execute(
            """
            INSERT INTO customer_merge_sync_events (event_id, event_type, payload, occurred_at)
            VALUES ('evt-1', 'customer_merge.applied', '{"remote_event_id":"evt-1"}', '2026-07-06T10:00:00Z')
            """
        )
        conn.commit()

        def _upload(conn, **kwargs):
            conn.execute(
                """
                UPDATE customer_merge_sync_events
                SET uploaded_at = '2026-07-06T11:00:00Z'
                WHERE event_id = 'evt-1'
                """
            )
            conn.commit()
            return {
                "events_sent": 1,
                "backfilled_applied": 0,
                "backfilled_undone": 0,
                "error": None,
            }

        mock_upload.side_effect = _upload
        result = drain_customer_outbox(conn)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["outbox_drained"])
        self.assertEqual(result["customer_merges_sent"], 1)
        conn.close()


class NonStrictCustomerLegacyBehaviorTests(unittest.TestCase):
    """Phase 6.3: mirrored revision + cloud config but strict flag off → legacy path."""

    @patch("src.core.customer_mutation_commit.commit_mutation")
    def test_merge_uses_legacy_path_when_strict_flag_off(self, mock_commit) -> None:
        conn = _customer_merge_db()
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example')"
        )
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_api_key', 'secret')"
        )
        set_customer_state_revision(conn, 42)
        set_customer_strict_mode_enabled(conn, False)
        conn.commit()
        try:
            self.assertFalse(strict_mode_active(conn))
            result = merge_customers(
                conn,
                "1",
                "2",
                similarity_score=0.95,
                model_name="duplicate_matcher_v1",
                reasons=["same phone"],
            )
            self.assertEqual(result["status"], "success")
            mock_commit.assert_not_called()
            outbox_count = conn.execute(
                "SELECT COUNT(*) FROM customer_merge_sync_events"
            ).fetchone()[0]
            self.assertEqual(outbox_count, 1)
            self.assertEqual(
                conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                2,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
