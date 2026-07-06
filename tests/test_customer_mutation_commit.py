import hashlib
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.customer_merge_sync import (
    apply_remote_customer_merge_event,
    ensure_customer_merge_pull_tables,
    pull_and_apply_customer_merge_events,
    set_customer_merge_pull_cursor,
)
from src.core.customer_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    SCHEMA_VERSION,
    build_merge_applied_event_payload,
    record_merge_applied_event,
)
from src.core.customer_mutation_commit import (
    LOCAL_APPLY_FAILED_MESSAGE,
    CustomerStatePullError,
    MutationPlan,
    apply_accepted,
    build_plan,
    commit_mutation,
    extract_customer_keys_from_event,
    plan_overlaps_pulled_changes,
    pull_latest_customer_state,
    strict_mode_active,
    strict_mode_ready,
)
from src.core.queries.customer_merge_queries import merge_customers, undo_customer_merge
from src.core.sync_identity import (
    get_customer_state_revision,
    set_customer_state_revision,
    set_customer_strict_mode_enabled,
)


def _conn_with_cloud_config(url=None, api_key="k"):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    if url:
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', ?)", (url,)
        )
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_api_key', ?)",
            (api_key,),
        )
    conn.commit()
    return conn


def _customer_merge_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            customer_identity_key TEXT,
            name TEXT,
            name_normalized TEXT,
            phone TEXT,
            address TEXT,
            gstin TEXT,
            total_orders INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0,
            first_order_date TEXT,
            last_order_date TEXT,
            is_verified BOOLEAN NOT NULL DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE customer_addresses (
            address_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            label TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            country TEXT,
            is_default BOOLEAN DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            petpooja_order_id TEXT,
            stream_id INTEGER,
            event_id TEXT,
            aggregate_id TEXT,
            total REAL NOT NULL DEFAULT 0,
            created_on TEXT,
            updated_at TEXT
        );

        CREATE TABLE menu_items (
            menu_item_id TEXT PRIMARY KEY,
            name TEXT
        );

        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            menu_item_id TEXT,
            name_raw TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE customer_merge_history (
            merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_customer_id INTEGER NOT NULL,
            target_customer_id INTEGER NOT NULL,
            similarity_score REAL,
            model_name TEXT,
            suggestion_context TEXT,
            source_snapshot TEXT,
            target_snapshot TEXT,
            moved_order_ids TEXT,
            copied_address_count INTEGER DEFAULT 0,
            merged_at TEXT DEFAULT CURRENT_TIMESTAMP,
            undone_at TEXT,
            undo_context TEXT
        );

        CREATE TABLE customer_merge_sync_events (
            event_id TEXT PRIMARY KEY,
            merge_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            upload_attempted_at TEXT,
            uploaded_at TEXT,
            last_error TEXT,
            UNIQUE (merge_id, event_type)
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO customers (
            customer_id, customer_identity_key, name, name_normalized,
            phone, address, total_orders, total_spent, first_order_date, last_order_date, is_verified
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "phone:source", "Rahul Sharma", "rahul sharma", "9999999999", "HSR Layout", 1, 80.0, "2024-02-03", "2024-02-03", 0),
            (2, "addr:target", "Rahul S.", "rahul s.", None, "HSR Layout", 1, 120.0, "2024-02-04", "2024-02-04", 0),
        ],
    )
    conn.executemany(
        """
        INSERT INTO customer_addresses (
            customer_id, label, address_line_1, city, state, postal_code, country, is_default
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "Primary", "HSR Layout", "Bengaluru", "KA", "560102", "IN", 1),
            (2, "Primary", "HSR Layout", "Bengaluru", "KA", "560102", "IN", 1),
        ],
    )
    conn.executemany(
        """
        INSERT INTO orders (
            order_id, customer_id, petpooja_order_id, stream_id, event_id, aggregate_id, total, created_on
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (101, 1, "PP-101", 5001, "evt-101", "agg-101", 80.0, "2024-02-03 10:00:00"),
            (102, 2, "PP-102", 5002, "evt-102", "agg-102", 120.0, "2024-02-04 10:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO menu_items (menu_item_id, name) VALUES (?, ?)",
        [("m_burger", "Burger"), ("m_fries", "Fries")],
    )
    conn.executemany(
        "INSERT INTO order_items (order_id, menu_item_id, name_raw, quantity) VALUES (?, ?, ?, ?)",
        [(101, "m_burger", "Burger", 1), (102, "m_fries", "Fries", 2)],
    )
    conn.commit()
    return conn


class CustomerStrictModeGateTests(unittest.TestCase):
    def test_ready_false_without_cloud_config(self) -> None:
        conn = _conn_with_cloud_config(url=None)
        self.addCleanup(conn.close)
        set_customer_state_revision(conn, 5)
        self.assertFalse(strict_mode_ready(conn))

    def test_ready_false_before_revision_seen(self) -> None:
        conn = _conn_with_cloud_config(url="https://cloud.example")
        self.addCleanup(conn.close)
        self.assertFalse(strict_mode_ready(conn))

    def test_ready_true_with_cloud_config_and_revision(self) -> None:
        conn = _conn_with_cloud_config(url="https://cloud.example")
        self.addCleanup(conn.close)
        set_customer_state_revision(conn, 3)
        self.assertTrue(strict_mode_ready(conn))

    def test_active_requires_flag_and_readiness(self) -> None:
        conn = _conn_with_cloud_config(url="https://cloud.example")
        self.addCleanup(conn.close)
        set_customer_state_revision(conn, 3)
        self.assertFalse(strict_mode_active(conn))

        set_customer_strict_mode_enabled(conn, True)
        self.assertTrue(strict_mode_active(conn))

    def test_active_false_when_flag_on_but_not_ready(self) -> None:
        conn = _conn_with_cloud_config(url=None)
        self.addCleanup(conn.close)
        set_customer_strict_mode_enabled(conn, True)
        self.assertFalse(strict_mode_active(conn))


class CustomerMutationBuildingBlocksTests(unittest.TestCase):
    def test_build_payload_in_uncommitted_txn_matches_pull_apply(self) -> None:
        capture_conn = _customer_merge_db()
        peer_conn = _customer_merge_db()
        ensure_customer_merge_pull_tables(peer_conn)
        try:
            capture_conn.execute(
                """
                UPDATE orders SET customer_id = 2, updated_at = CURRENT_TIMESTAMP WHERE customer_id = 1
                """
            )
            merge_id = capture_conn.execute(
                """
                INSERT INTO customer_merge_history (
                    source_customer_id, target_customer_id, similarity_score, model_name,
                    suggestion_context, source_snapshot, target_snapshot, moved_order_ids, copied_address_count
                )
                VALUES (1, 2, 0.95, 'test', '{}', '{}', '{}', '[101]', 0)
                RETURNING merge_id
                """
            ).fetchone()[0]

            payload = build_merge_applied_event_payload(capture_conn, int(merge_id))
            self.assertIsNotNone(payload)
            assert payload is not None
            payload = dict(payload)
            payload["server_seq"] = 99
            payload["server_ingested_at"] = "2026-07-06T10:00:00Z"

            capture_conn.rollback()
            self.assertEqual(
                capture_conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                1,
            )

            result = apply_remote_customer_merge_event(peer_conn, payload, "cursor-99")
            peer_conn.commit()
            self.assertIn(result["status"], {"applied", "duplicate"})
            self.assertEqual(
                peer_conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                2,
            )

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"events": [payload], "next_cursor": "cursor-99"}
            with patch("requests.get", return_value=mock_response):
                pull_result = pull_and_apply_customer_merge_events(
                    peer_conn,
                    endpoint="https://cloud.example/desktop-analytics-sync/customer-merges",
                )
            self.assertIsNone(pull_result["error"])
            self.assertEqual(pull_result["merge_events_applied"], 0)
        finally:
            capture_conn.close()
            peer_conn.close()

    def test_record_merge_applied_event_matches_build_plus_insert(self) -> None:
        conn = _customer_merge_db()
        try:
            merge_result = merge_customers(
                conn,
                "1",
                "2",
                similarity_score=0.98,
                model_name="duplicate_matcher_v1",
                reasons=["phone exact match"],
            )
            self.assertEqual(merge_result["status"], "success")
            row = conn.execute(
                "SELECT payload FROM customer_merge_sync_events WHERE merge_id = ?",
                (merge_result["merge_id"],),
            ).fetchone()
            self.assertIsNotNone(row)
            stored_payload = json.loads(row["payload"])
            self.assertEqual(stored_payload["schema_version"], SCHEMA_VERSION)
            self.assertEqual(stored_payload["event_type"], EVENT_TYPE_APPLIED)
            expected_phone_hash = hashlib.sha256("9999999999".encode("utf-8")).hexdigest()
            self.assertEqual(
                stored_payload["source_customer"]["portable_locators"]["phone_hash"],
                expected_phone_hash,
            )
        finally:
            conn.close()


class CustomerMutationCommitFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _customer_merge_db()
        ensure_customer_merge_pull_tables(self.conn)
        set_customer_state_revision(self.conn, 10)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _sample_event(self) -> dict:
        phone_hash = hashlib.sha256("9999999999".encode("utf-8")).hexdigest()
        name_address_hash = hashlib.sha256("rahul sharma|hsr layout".encode("utf-8")).hexdigest()
        return {
            "remote_event_id": "evt-1",
            "schema_version": SCHEMA_VERSION,
            "event_type": EVENT_TYPE_APPLIED,
            "occurred_at": "2026-07-06T10:00:00Z",
            "source_customer": {
                "snapshot": {"name": "Rahul Sharma", "phone": "9999999999", "address": "HSR Layout"},
                "portable_locators": {"phone_hash": phone_hash, "name_address_hash": name_address_hash},
            },
            "target_customer": {
                "snapshot": {"name": "Rahul S.", "address": "HSR Layout"},
                "portable_locators": {"name_address_hash": name_address_hash},
            },
            "merge_metadata": {"reasons": ["test"]},
            "moved_orders": {"count": 1, "portable_refs": []},
        }

    def _accepted_body(self, event: dict, *, customer_revision: int = 11, mutation_id: str = "mut-1") -> dict:
        return {
            "status": "accepted",
            "mutation_id": mutation_id,
            "customer_revision": customer_revision,
            "accepted_events": [
                {
                    "remote_event_id": event["remote_event_id"],
                    "server_seq": 55,
                    "server_ingested_at": "2026-07-06T10:00:00Z",
                }
            ],
            "customer_merge_cursor": "9",
        }

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_first_attempt_uses_revision_pinned_during_capture(self, post, _cloud_config) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="customer_merge.applied",
            event=event,
            mutation_id="mut-pinned",
        )
        plan.expected_customer_revision = 10
        set_customer_state_revision(self.conn, 11)
        self.conn.commit()
        post.return_value = Mock(
            status_code=400,
            content='{"error":"stop"}',
            json=lambda: {"error": "stop"},
        )

        result = commit_mutation(self.conn, plan)

        self.assertEqual(result.status, "error")
        self.assertEqual(post.call_args.kwargs["json"]["expected_customer_revision"], 10)

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_commit_200_applies_locally(self, mock_post, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="customer_merge.applied",
            event=event,
            mutation_id="mut-1",
        )
        accepted = self._accepted_body(event)
        mock_post.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.merge_id)
        self.assertEqual(
            self.conn.execute("SELECT value FROM system_config WHERE key = 'customer_state_revision'").fetchone()[0],
            "11",
        )

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("requests.post")
    def test_commit_409_non_overlapping_retries_success(self, mock_post, mock_pull, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="customer_merge.applied",
            event=event,
            customer_keys=["other-key"],
            mutation_id="mut-retry",
        )
        conflict = {"status": "conflict", "conflicting_events": [{"customer_keys": ["unrelated"]}]}
        accepted = self._accepted_body(event, customer_revision=12, mutation_id="mut-retry")
        mock_post.side_effect = [
            Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict),
            Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted),
        ]

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "ok")
        mock_pull.assert_called_once()
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_post.call_args_list[0].kwargs["json"]["mutation_id"], "mut-retry")
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["mutation_id"], "mut-retry")

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("requests.post")
    def test_commit_409_overlapping_surfaces_conflict(self, mock_post, mock_pull, _mock_cfg) -> None:
        event = self._sample_event()
        phone_hash = event["source_customer"]["portable_locators"]["phone_hash"]
        plan = build_plan(
            mutation_type="customer_merge.applied",
            event=event,
            customer_keys=[phone_hash],
            mutation_id="mut-overlap",
        )
        conflict = {"status": "conflict", "conflicting_events": [{"customer_keys": [phone_hash]}]}
        mock_post.return_value = Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict)

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "conflict")
        mock_pull.assert_called_once()

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("requests.post")
    def test_commit_409_pull_failure_propagates_error_message(self, mock_post, mock_pull, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="customer_merge.applied",
            event=event,
            customer_keys=["other-key"],
            mutation_id="mut-pull-fail",
        )
        conflict = {"status": "conflict", "conflicting_events": [{"customer_keys": ["unrelated"]}]}
        mock_post.return_value = Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict)
        mock_pull.side_effect = CustomerStatePullError(
            "Could not resolve source customer for remote_event_id=abc-123"
        )

        result = commit_mutation(self.conn, plan)

        self.assertEqual(result.status, "error")
        self.assertEqual(
            result.message,
            "Could not resolve source customer for remote_event_id=abc-123",
        )
        self.assertNotEqual(result.message, "Customer merge requires a cloud connection.")

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("requests.get")
    @patch("requests.post")
    def test_reconcile_409_pull_failure_propagates_error_message(
        self, mock_post, mock_get, mock_pull, _mock_cfg
    ) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="customer_merge.applied",
            event=event,
            customer_keys=["other-key"],
            mutation_id="mut-reconcile-pull-fail",
        )
        conflict = {"status": "conflict", "conflicting_events": [{"customer_keys": ["unrelated"]}]}
        mock_post.side_effect = [
            Exception("timeout"),
            Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict),
        ]
        mock_get.return_value = Mock(status_code=404, content="{}", json=lambda: {})
        mock_pull.side_effect = CustomerStatePullError("Customer merge event apply did not complete cleanly.")

        result = commit_mutation(self.conn, plan)

        self.assertEqual(result.status, "error")
        self.assertEqual(result.message, "Customer merge event apply did not complete cleanly.")

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("requests.get")
    @patch("requests.post")
    def test_commit_5xx_reconcile_conflict_returns_without_redundant_pull(
        self, mock_post, mock_get, mock_pull, _mock_cfg
    ) -> None:
        event = self._sample_event()
        phone_hash = event["source_customer"]["portable_locators"]["phone_hash"]
        plan = build_plan(
            mutation_type="customer_merge.applied",
            event=event,
            customer_keys=[phone_hash],
            mutation_id="mut-5xx-conflict",
        )
        conflict = {"status": "conflict", "conflicting_events": [{"customer_keys": [phone_hash]}]}
        mock_post.side_effect = [
            Mock(status_code=500, content="{}", json=lambda: {}),
            Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict),
        ]
        mock_get.return_value = Mock(status_code=404, content="{}", json=lambda: {})

        result = commit_mutation(self.conn, plan)

        self.assertEqual(result.status, "conflict")
        mock_pull.assert_called_once()

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_timeout_status_200_applies(self, mock_post, mock_get, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(mutation_type="customer_merge.applied", event=event, mutation_id="mut-status")
        accepted = self._accepted_body(event, mutation_id="mut-status")
        mock_post.side_effect = Exception("timeout")
        mock_get.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "ok")

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_timeout_status_404_repost_applies(self, mock_post, mock_get, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(mutation_type="customer_merge.applied", event=event, mutation_id="mut-repost")
        accepted = self._accepted_body(event, mutation_id="mut-repost")
        mock_post.side_effect = [
            Exception("timeout"),
            Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted),
        ]
        mock_get.return_value = Mock(status_code=404, content="{}", json=lambda: {})

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "ok")
        self.assertEqual(mock_post.call_count, 2)

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_timeout_status_404_repost_network_fail_leaves_sqlite_unchanged(self, mock_post, mock_get, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(mutation_type="customer_merge.applied", event=event, mutation_id="mut-net-fail")
        mock_post.side_effect = [Exception("timeout"), Exception("timeout")]
        mock_get.return_value = Mock(status_code=404, content="{}", json=lambda: {})

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "error")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_merge_history").fetchone()[0],
            0,
        )

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("src.core.customer_mutation_commit.apply_accepted", side_effect=RuntimeError("apply blew up"))
    @patch("requests.post")
    def test_local_apply_failure_triggers_pull_replay(self, mock_post, _mock_apply, mock_pull, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(mutation_type="customer_merge.applied", event=event, mutation_id="mut-apply-fail")
        accepted = self._accepted_body(event)
        mock_post.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        result = commit_mutation(self.conn, plan)

        self.assertEqual(result.status, "error")
        self.assertEqual(result.message, LOCAL_APPLY_FAILED_MESSAGE)
        mock_pull.assert_called_once()

    def test_replayed_response_does_not_rewind_revision_or_cursor(self) -> None:
        event = self._sample_event()
        plan = build_plan(mutation_type="customer_merge.applied", event=event, mutation_id="mut-old")
        set_customer_state_revision(self.conn, 20)
        set_customer_merge_pull_cursor(self.conn, "15")
        self.conn.commit()

        old_body = self._accepted_body(event, customer_revision=12, mutation_id="mut-old")
        old_body["customer_merge_cursor"] = "9"
        apply_accepted(self.conn, old_body, plan)

        self.assertEqual(
            self.conn.execute("SELECT value FROM system_config WHERE key = 'customer_state_revision'").fetchone()[0],
            "20",
        )
        self.assertEqual(
            self.conn.execute("SELECT value FROM system_config WHERE key = 'customer_merge_pull_cursor'").fetchone()[0],
            "15",
        )

    def test_plan_overlap_detection(self) -> None:
        plan = build_plan(
            mutation_type="customer_merge.applied",
            customer_keys=["key-a", "key-b"],
        )
        self.assertTrue(plan_overlaps_pulled_changes(plan, [{"customer_keys": ["key-b"]}]))
        self.assertFalse(plan_overlaps_pulled_changes(plan, [{"customer_keys": ["key-z"]}]))
        self.assertTrue(plan_overlaps_pulled_changes(plan, []))
        self.assertTrue(plan_overlaps_pulled_changes(plan, [{}]))

    def test_extract_customer_keys_from_event(self) -> None:
        event = self._sample_event()
        keys = extract_customer_keys_from_event(event)
        self.assertIn(event["source_customer"]["portable_locators"]["phone_hash"], keys)
        self.assertIn(event["source_customer"]["portable_locators"]["name_address_hash"], keys)

    @patch(
        "src.core.customer_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "secret"),
    )
    @patch(
        "src.core.customer_mutation_commit.get_customer_merge_pull_endpoint",
        return_value="https://cloud.example/customer-merges",
    )
    @patch("src.core.customer_mutation_commit.pull_and_apply_customer_merge_events")
    def test_pull_latest_drains_pages(self, pull_fn, _endpoint, _cloud_config) -> None:
        pull_fn.side_effect = [
            {"error": None, "has_more": True},
            {"error": None, "has_more": False, "advertised_customer_revision": 42},
        ]
        stats = pull_latest_customer_state(self.conn)
        self.assertEqual(pull_fn.call_count, 2)
        self.assertFalse(stats["has_more"])


class CustomerStrictModeMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _customer_merge_db()
        ensure_customer_merge_pull_tables(self.conn)
        self.conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example')"
        )
        self.conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_api_key', 'secret')"
        )
        set_customer_state_revision(self.conn, 10)
        set_customer_strict_mode_enabled(self.conn, True)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_strict_mode_merge_no_outbox_and_applies_after_200(self, mock_post, _mock_cfg) -> None:
        def _capture_post(*_args, **kwargs):
            event = kwargs["json"]["event"]
            accepted = {
                "status": "accepted",
                "mutation_id": kwargs["json"]["mutation_id"],
                "customer_revision": 11,
                "accepted_events": [
                    {
                        "remote_event_id": event["remote_event_id"],
                        "server_seq": 55,
                        "server_ingested_at": "2026-07-06T10:00:00Z",
                    }
                ],
                "customer_merge_cursor": "cursor-1",
            }
            return Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        mock_post.side_effect = _capture_post

        result = merge_customers(
            self.conn,
            "1",
            "2",
            similarity_score=0.95,
            model_name="duplicate_matcher_v1",
            reasons=["same phone"],
        )

        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(result.get("merge_id"))
        outbox_count = self.conn.execute(
            "SELECT COUNT(*) FROM customer_merge_sync_events"
        ).fetchone()[0]
        self.assertEqual(outbox_count, 0)
        self.assertEqual(
            self.conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
            2,
        )
        self.assertEqual(get_customer_state_revision(self.conn), 11)

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_strict_mode_undo_without_reverts_target_fails_locally(self, mock_post, _mock_cfg) -> None:
        """Strict undo must not mint a dangling reverts_remote_event_id and POST a 422."""
        set_customer_strict_mode_enabled(self.conn, False)
        self.conn.commit()

        merge_result = merge_customers(
            self.conn,
            "1",
            "2",
            similarity_score=0.95,
            model_name="duplicate_matcher_v1",
            reasons=["same phone"],
        )
        self.assertEqual(merge_result["status"], "success")
        merge_id = merge_result["merge_id"]

        self.conn.execute(
            "DELETE FROM customer_merge_sync_events WHERE merge_id = ?",
            (merge_id,),
        )
        self.conn.execute(
            """
            UPDATE customer_merge_history
            SET suggestion_context = ?
            WHERE merge_id = ?
            """,
            (
                json.dumps({
                    "reasons": ["same phone"],
                    "target_before_fields": {},
                    "inserted_target_address_ids": [],
                }),
                merge_id,
            ),
        )
        self.conn.commit()

        set_customer_strict_mode_enabled(self.conn, True)
        self.conn.commit()

        undo_result = undo_customer_merge(self.conn, merge_id)

        self.assertEqual(undo_result["status"], "error")
        self.assertEqual(undo_result["message"], "Failed to build undo event")
        mock_post.assert_not_called()
        undone_at = self.conn.execute(
            "SELECT undone_at FROM customer_merge_history WHERE merge_id = ?",
            (merge_id,),
        ).fetchone()[0]
        self.assertIsNone(undone_at)
        self.assertEqual(
            self.conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
            2,
        )

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_strict_mode_undo_no_outbox_and_applies_after_200(self, mock_post, _mock_cfg) -> None:
        def _accept_post(*_args, **kwargs):
            event = kwargs["json"]["event"]
            rev = 11 if kwargs["json"]["mutation_type"] == "customer_merge.applied" else 12
            accepted = {
                "status": "accepted",
                "mutation_id": kwargs["json"]["mutation_id"],
                "customer_revision": rev,
                "accepted_events": [
                    {
                        "remote_event_id": event["remote_event_id"],
                        "server_seq": 55,
                        "server_ingested_at": "2026-07-06T10:00:00Z",
                    }
                ],
                "customer_merge_cursor": "cursor-1",
            }
            return Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        mock_post.side_effect = _accept_post

        merge_result = merge_customers(
            self.conn,
            "1",
            "2",
            similarity_score=0.95,
            model_name="duplicate_matcher_v1",
            reasons=["same phone"],
        )
        self.assertEqual(merge_result["status"], "success")
        merge_id = merge_result["merge_id"]

        undo_result = undo_customer_merge(self.conn, merge_id)
        self.assertEqual(undo_result["status"], "success")
        self.assertEqual(undo_result["merge_id"], merge_id)
        undone_count = self.conn.execute(
            "SELECT COUNT(*) FROM customer_merge_sync_events WHERE event_type = 'customer_merge.undone'"
        ).fetchone()[0]
        self.assertEqual(undone_count, 0)
        undone_at = self.conn.execute(
            "SELECT undone_at FROM customer_merge_history WHERE merge_id = ?",
            (merge_id,),
        ).fetchone()[0]
        self.assertIsNotNone(undone_at)


if __name__ == "__main__":
    unittest.main()
