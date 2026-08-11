import json
import sqlite3
import unittest
from tests.profile_test_helpers import bind_test_profile
from unittest.mock import Mock, patch

from src.core.menu_assignment_schema import ensure_assignment_sync_schema
from src.core.menu_mapping_verification_sync import (
    apply_remote_menu_mapping_verification_event,
    get_menu_mapping_verification_pull_cursor,
    set_menu_mapping_verification_pull_cursor,
)
from src.core.menu_merge_sync import (
    apply_remote_menu_merge_event,
    pull_and_apply_menu_merge_events,
    set_menu_merge_pull_cursor,
)
from src.core.menu_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    build_menu_merge_event_payload,
    ensure_menu_merge_sync_tables,
)
from src.core.menu_mapping_verification_sync_events import ensure_menu_mapping_verification_sync_tables
from src.core.menu_mutation_commit import (
    CommitResult,
    LOCAL_APPLY_FAILED_MESSAGE,
    MUTATION_TYPE_CATALOG_UPDATE,
    MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC,
    MUTATION_TYPE_VERIFY,
    MenuStatePullError,
    MutationPlan,
    apply_accepted,
    build_plan,
    commit_mutation,
    plan_overlaps_pulled_changes,
    pull_latest_menu_state,
    strict_mode_active,
    strict_mode_ready,
)
from src.core.sync_identity import get_menu_state_revision, set_menu_state_revision
from src.core.sync_cursor import pull_cursor_is_ahead
from utils import menu_utils


class MenuMutationCommitReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()

    def test_strict_mode_ready_false_without_cloud_config(self) -> None:
        with patch(
            "src.core.menu_mutation_commit.get_cloud_sync_config",
            return_value=(None, None),
        ):
            self.assertFalse(strict_mode_ready(self.conn))

    def test_strict_mode_ready_false_before_revision_seen(self) -> None:
        with patch(
            "src.core.menu_mutation_commit.get_cloud_sync_config",
            return_value=("https://cloud.example", "secret"),
        ):
            self.assertFalse(strict_mode_ready(self.conn))

    def test_strict_mode_ready_true_with_cloud_config_and_revision(self) -> None:
        set_menu_state_revision(self.conn, 5)
        with patch(
            "src.core.menu_mutation_commit.get_cloud_sync_config",
            return_value=("https://cloud.example", "secret"),
        ):
            self.assertTrue(strict_mode_ready(self.conn))

    def test_strict_mode_active_false_when_not_ready(self) -> None:
        with patch(
            "src.core.menu_mutation_commit.get_cloud_sync_config",
            return_value=(None, None),
        ):
            self.assertFalse(strict_mode_active(self.conn))

    def test_strict_mode_active_true_when_ready(self) -> None:
        set_menu_state_revision(self.conn, 5)
        with patch(
            "src.core.menu_mutation_commit.get_cloud_sync_config",
            return_value=("https://cloud.example", "secret"),
        ):
            self.assertTrue(strict_mode_active(self.conn))


class MenuMutationBuildingBlocksTests(unittest.TestCase):
    @staticmethod
    def _create_db() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        bind_test_profile(conn)
        conn.executescript(
            """
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                suggestion_id TEXT REFERENCES menu_items(menu_item_id),
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                total_sold INTEGER DEFAULT 0,
                total_revenue REAL DEFAULT 0,
                sold_as_item INTEGER DEFAULT 0,
                sold_as_addon INTEGER DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                is_verified BOOLEAN DEFAULT 1,
                pending_local INTEGER DEFAULT 0,
                assignment_seq INTEGER,
                updated_at TEXT
            );
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                menu_item_id TEXT,
                variant_id TEXT,
                updated_at TEXT
            );
            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT,
                quantity INTEGER DEFAULT 1,
                price REAL DEFAULT 0
            );
            CREATE TABLE merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                affected_order_items TEXT NOT NULL,
                merged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                origin TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES (?, ?, ?, 1)",
            [
                ("item_source", "Iced Coffee", "Beverage"),
                ("item_target", "Cold Coffee", "Beverage"),
            ],
        )
        conn.execute(
            "INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified) VALUES ('1', 'item_source', NULL, 1)"
        )
        conn.execute("INSERT INTO order_items (order_item_id, menu_item_id) VALUES (1, 'item_source')")
        return conn

    def test_build_payload_in_uncommitted_txn_matches_pull_apply(self) -> None:
        capture_conn = self._create_db()
        peer_conn = self._create_db()
        ensure_menu_merge_sync_tables(capture_conn)
        ensure_menu_merge_sync_tables(peer_conn)
        ensure_assignment_sync_schema(capture_conn)
        ensure_assignment_sync_schema(peer_conn)
        try:
            capture_conn.execute(
                """
                INSERT INTO merge_history (source_id, target_id, source_name, source_type, affected_order_items)
                VALUES ('item_source', 'item_target', 'Iced Coffee', 'Beverage', ?)
                """,
                (
                    json.dumps(
                        {
                            "kind": "basic_merge_v1",
                            "affected_order_item_ids": ["1"],
                        }
                    ),
                ),
            )
            merge_id = int(capture_conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            capture_conn.execute(
                "UPDATE menu_item_variants SET menu_item_id = 'item_target', pending_local = 1 WHERE order_item_id = '1'"
            )
            capture_conn.execute("DELETE FROM menu_items WHERE menu_item_id = 'item_source'")

            payload = build_menu_merge_event_payload(capture_conn, merge_id, EVENT_TYPE_APPLIED)
            self.assertIsNotNone(payload)
            assert payload is not None
            payload = dict(payload)
            payload["server_seq"] = 99

            capture_conn.rollback()
            self.assertIsNone(
                capture_conn.execute("SELECT 1 FROM menu_items WHERE menu_item_id = 'item_source'").fetchone()
            )

            result = apply_remote_menu_merge_event(peer_conn, payload, "cursor-99")
            peer_conn.commit()
            self.assertIn(result["status"], {"applied", "duplicate"})

            peer_row = peer_conn.execute(
                "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(peer_row["menu_item_id"], "item_target")
            # Assignment-based apply moves mappings; it does not delete the source catalog row.
            self.assertIsNotNone(peer_conn.execute("SELECT 1 FROM menu_items WHERE menu_item_id = 'item_source'").fetchone())

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"events": [payload], "next_cursor": "cursor-99"}
            with patch("requests.get", return_value=mock_response):
                pull_result = pull_and_apply_menu_merge_events(
                    peer_conn,
                    endpoint="https://cloud.example/desktop-analytics-sync/menu-merges",
                )
            self.assertIsNone(pull_result["error"])
            self.assertEqual(pull_result["merge_events_applied"], 0)
        finally:
            capture_conn.close()
            peer_conn.close()


class MenuMutationCommitFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        bind_test_profile(self.conn)
        self.conn.executescript(
            """
            CREATE TABLE system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                suggestion_id TEXT REFERENCES menu_items(menu_item_id),
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                total_sold INTEGER DEFAULT 0,
                total_revenue REAL DEFAULT 0,
                sold_as_item INTEGER DEFAULT 0,
                sold_as_addon INTEGER DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                is_verified BOOLEAN DEFAULT 1,
                pending_local INTEGER DEFAULT 0,
                assignment_seq INTEGER,
                verification_seq INTEGER,
                updated_at TEXT
            );
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                menu_item_id TEXT,
                variant_id TEXT,
                updated_at TEXT
            );
            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT
            );
            CREATE TABLE merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                affected_order_items TEXT NOT NULL,
                merged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                origin TEXT
            );
            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 1,
                updated_at TEXT
            );
            """
        )
        ensure_menu_merge_sync_tables(self.conn)
        ensure_assignment_sync_schema(self.conn)
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES (?, ?, ?, 1)",
            [("item_source", "Iced Coffee", "Beverage"), ("item_target", "Cold Coffee", "Beverage")],
        )
        self.conn.execute(
            "INSERT INTO menu_item_variants (order_item_id, menu_item_id, is_verified) VALUES ('1', 'item_source', 1)"
        )
        set_menu_state_revision(self.conn, 10)
        self.conn.commit()

    def _accepted_body(
        self,
        event: dict,
        *,
        menu_revision: int = 11,
        mutation_id: str = "mut-1",
    ) -> dict:
        return {
            "status": "accepted",
            "mutation_id": mutation_id,
            "menu_revision": menu_revision,
            "accepted_events": [
                {
                    "remote_event_id": event["remote_event_id"],
                    "server_seq": 55,
                    "server_ingested_at": "2026-07-06T10:00:00Z",
                }
            ],
            "assignment_rows": [],
            "catalog_delta": {"items": [], "variants": []},
            "merge_cursor": "9",
            "verification_cursor": "4",
        }

    def _sample_event(self) -> dict:
        return {
            "remote_event_id": "evt-1",
            "schema_version": 2,
            "event_type": EVENT_TYPE_APPLIED,
            "occurred_at": "2026-07-06T10:00:00Z",
            "source_item": {"menu_item_id": "item_source", "name": "Iced Coffee", "type": "Beverage"},
            "target_item": {"menu_item_id": "item_target", "name": "Cold Coffee", "type": "Beverage"},
            "merge_payload": {
                "kind": "basic_merge_v1",
                "assignments": [
                    {"order_item_id": "1", "menu_item_id": "item_target", "variant_id": None, "is_verified": 1}
                ],
            },
        }

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_commit_200_applies_locally(self, mock_post, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-1",
        )
        accepted = self._accepted_body(event)
        mock_post.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "ok")
        row = self.conn.execute(
            "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(row["menu_item_id"], "item_target")
        self.assertEqual(self.conn.execute("SELECT value FROM system_config WHERE key = 'menu_state_revision'").fetchone()[0], "11")

    def test_derived_commit_skipped_existing_is_not_self_applied(self) -> None:
        event = {
            "remote_event_id": "evt-derived-skip",
            "schema_version": 2,
            "event_type": EVENT_TYPE_APPLIED,
            "occurred_at": "2026-07-06T10:00:00Z",
            "merge_payload": {
                "kind": "derived_assignment_v1",
                "assignments": [
                    {
                        "order_item_id": "1",
                        "menu_item_id": "item_target",
                        "variant_id": None,
                        "is_verified": 0,
                    }
                ],
            },
        }
        plan = build_plan(
            mutation_type=MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC,
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-derived-skip",
        )
        body = self._accepted_body(event, mutation_id="mut-derived-skip")
        body["skipped_existing"] = ["1"]

        apply_accepted(self.conn, body, plan)

        row = self.conn.execute(
            "SELECT menu_item_id, assignment_seq FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(row["menu_item_id"], "item_source")
        self.assertIsNone(row["assignment_seq"])

    def test_derived_commit_skipped_existing_adopts_server_row(self) -> None:
        # The server's commit response carries the authoritative row for every
        # touched key. A skipped_existing key must adopt it: stamp its seq so
        # the row leaves the flush pending set, and take the server mapping so
        # this install converges even when the originating event is already
        # behind the local merge cursor.
        self.conn.execute("INSERT INTO order_items (order_item_id, menu_item_id) VALUES (1, 'item_source')")
        self.conn.commit()
        event = {
            "remote_event_id": "evt-derived-adopt",
            "schema_version": 2,
            "event_type": EVENT_TYPE_APPLIED,
            "occurred_at": "2026-07-06T10:00:00Z",
            "merge_payload": {
                "kind": "derived_assignment_v1",
                "assignments": [
                    {
                        "order_item_id": "1",
                        "menu_item_id": "item_source",
                        "variant_id": None,
                        "is_verified": 0,
                    }
                ],
            },
        }
        plan = build_plan(
            mutation_type=MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC,
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-derived-adopt",
        )
        body = self._accepted_body(event, mutation_id="mut-derived-adopt")
        body["skipped_existing"] = ["1"]
        body["assignment_rows"] = [
            {
                "order_item_id": "1",
                "menu_item_id": "item_target",
                "variant_id": None,
                "is_verified": 1,
                "assignment_seq": 42,
                "verification_seq": 7,
            }
        ]

        apply_accepted(self.conn, body, plan)

        row = self.conn.execute(
            """
            SELECT menu_item_id, variant_id, is_verified, assignment_seq,
                   verification_seq, pending_local
            FROM menu_item_variants WHERE order_item_id = '1'
            """
        ).fetchone()
        self.assertEqual(row["menu_item_id"], "item_target")
        self.assertIsNone(row["variant_id"])
        self.assertEqual(int(row["is_verified"]), 1)
        self.assertEqual(int(row["assignment_seq"]), 42)
        self.assertEqual(int(row["verification_seq"]), 7)
        self.assertEqual(int(row["pending_local"]), 0)
        order_row = self.conn.execute(
            "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
        ).fetchone()
        self.assertEqual(order_row["menu_item_id"], "item_target")

    def test_derived_commit_adoption_skips_unknown_menu_item(self) -> None:
        # FK safety: server row referencing an item this install has not
        # materialized yet is left pending (heals after the catalog pull).
        event = {
            "remote_event_id": "evt-derived-fk",
            "schema_version": 2,
            "event_type": EVENT_TYPE_APPLIED,
            "occurred_at": "2026-07-06T10:00:00Z",
            "merge_payload": {
                "kind": "derived_assignment_v1",
                "assignments": [
                    {
                        "order_item_id": "1",
                        "menu_item_id": "item_source",
                        "variant_id": None,
                        "is_verified": 0,
                    }
                ],
            },
        }
        plan = build_plan(
            mutation_type=MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC,
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-derived-fk",
        )
        body = self._accepted_body(event, mutation_id="mut-derived-fk")
        body["skipped_existing"] = ["1"]
        body["assignment_rows"] = [
            {
                "order_item_id": "1",
                "menu_item_id": "item_never_seen",
                "variant_id": None,
                "is_verified": 1,
                "assignment_seq": 42,
                "verification_seq": None,
            }
        ]

        apply_accepted(self.conn, body, plan)

        row = self.conn.execute(
            "SELECT menu_item_id, assignment_seq FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(row["menu_item_id"], "item_source")
        self.assertIsNone(row["assignment_seq"])

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_commit_200_runs_batch_epilogue(self, mock_post, _mock_cfg, _mock_models) -> None:
        # The committing install must run the same batch epilogue peers run on
        # pull: stats recompute + resolution-state sync, which GCs a source
        # item left with zero mappings and zero usage.
        self.conn.executescript(
            """
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, order_status TEXT);
            ALTER TABLE order_items ADD COLUMN order_id INTEGER;
            ALTER TABLE order_items ADD COLUMN quantity INTEGER DEFAULT 0;
            ALTER TABLE order_items ADD COLUMN total_price REAL DEFAULT 0;
            ALTER TABLE order_item_addons ADD COLUMN quantity INTEGER DEFAULT 0;
            ALTER TABLE order_item_addons ADD COLUMN price REAL DEFAULT 0;
            """
        )
        self.conn.commit()

        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-1",
        )
        accepted = self._accepted_body(event)
        mock_post.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM menu_items WHERE menu_item_id = 'item_source'").fetchone()
        )
        row = self.conn.execute(
            "SELECT is_verified, total_sold FROM menu_items WHERE menu_item_id = 'item_target'"
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)
        self.assertEqual(int(row["total_sold"]), 0)

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_accepted_response_does_not_advance_pull_cursors(self, mock_post, _mock_cfg) -> None:
        # Plan §12.5: the response cursors are post-commit stream heads; when the
        # local cursors lag, advancing to them would skip never-applied peer
        # events, so apply_accepted must leave the cursors alone.
        set_menu_merge_pull_cursor(self.conn, "5")
        set_menu_mapping_verification_pull_cursor(self.conn, "2")
        self.conn.commit()
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-1",
        )
        accepted = self._accepted_body(event)
        mock_post.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        result = commit_mutation(self.conn, plan)

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            self.conn.execute("SELECT value FROM system_config WHERE key = 'menu_merge_pull_cursor'").fetchone()[0],
            "5",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT value FROM system_config WHERE key = 'menu_mapping_verification_pull_cursor'"
            ).fetchone()[0],
            "2",
        )
        self.assertEqual(self.conn.execute("SELECT value FROM system_config WHERE key = 'menu_state_revision'").fetchone()[0], "11")

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "secret"),
    )
    @patch("requests.post")
    def test_first_attempt_uses_revision_pinned_during_capture(
        self,
        post,
        _cloud_config,
    ) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-pinned",
        )
        plan.expected_menu_revision = 10
        set_menu_state_revision(self.conn, 11)
        self.conn.commit()
        post.return_value = Mock(
            status_code=400,
            content='{"error":"stop"}',
            json=lambda: {"error": "stop"},
        )

        result = commit_mutation(self.conn, plan)

        self.assertEqual(result.status, "error")
        self.assertEqual(post.call_args.kwargs["json"]["expected_menu_revision"], 10)

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.menu_mutation_commit.pull_latest_menu_state")
    @patch("requests.post")
    def test_commit_409_non_overlapping_retries_success(self, mock_post, mock_pull, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["99"],
            mutation_id="mut-retry",
        )
        conflict = {
            "status": "conflict",
            "conflicting_events": [{"order_item_ids": ["2"]}],
        }
        accepted = self._accepted_body(
            event,
            menu_revision=12,
            mutation_id="mut-retry",
        )
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

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.menu_mutation_commit.pull_latest_menu_state")
    @patch("requests.post")
    def test_commit_409_overlapping_surfaces_conflict(self, mock_post, mock_pull, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-overlap",
        )
        conflict = {
            "status": "conflict",
            "conflicting_events": [{"order_item_ids": ["1"]}],
        }
        mock_post.return_value = Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict)

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "conflict")
        mock_pull.assert_called_once()
        row = self.conn.execute(
            "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(row["menu_item_id"], "item_source")

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_commit_request_omits_scope_key(self, mock_post, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-no-scope",
        )
        accepted = self._accepted_body(event, mutation_id="mut-no-scope")
        mock_post.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        commit_mutation(self.conn, plan)

        payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn("scope_key", payload)

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_status_get_omits_scope_key_query(self, mock_post, mock_get, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-status-scope",
        )
        accepted = self._accepted_body(event, mutation_id="mut-status-scope")
        mock_post.side_effect = Exception("timeout")
        mock_get.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        commit_mutation(self.conn, plan)

        self.assertNotIn("params", mock_get.call_args.kwargs)

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_timeout_status_200_applies(self, mock_post, mock_get, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(mutation_type="menu_merge.applied", event=event, order_item_ids=["1"], mutation_id="mut-status")
        accepted = self._accepted_body(event, mutation_id="mut-status")
        mock_post.side_effect = Exception("timeout")
        mock_get.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "ok")

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_timeout_status_404_repost_applies(self, mock_post, mock_get, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(mutation_type="menu_merge.applied", event=event, order_item_ids=["1"], mutation_id="mut-repost")
        accepted = self._accepted_body(event, mutation_id="mut-repost")
        mock_post.side_effect = [
            Exception("timeout"),
            Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted),
        ]
        mock_get.return_value = Mock(status_code=404, content="{}", json=lambda: {})

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "ok")
        self.assertEqual(mock_post.call_count, 2)

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_timeout_status_404_repost_network_fail_leaves_sqlite_unchanged(self, mock_post, mock_get, _mock_cfg) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-net-fail",
        )
        mock_post.side_effect = [Exception("timeout"), Exception("timeout")]
        mock_get.return_value = Mock(status_code=404, content="{}", json=lambda: {})

        result = commit_mutation(self.conn, plan)
        self.assertEqual(result.status, "error")
        row = self.conn.execute(
            "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(row["menu_item_id"], "item_source")

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.menu_mutation_commit.pull_latest_menu_state")
    @patch("src.core.menu_mutation_commit.apply_accepted", side_effect=RuntimeError("apply blew up"))
    @patch("requests.post")
    def test_local_apply_failure_triggers_pull_replay(
        self,
        mock_post,
        _mock_apply,
        mock_pull,
        _mock_cfg,
    ) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-apply-fail",
        )
        accepted = self._accepted_body(event)
        mock_post.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

        result = commit_mutation(self.conn, plan)

        self.assertEqual(result.status, "error")
        self.assertEqual(result.message, LOCAL_APPLY_FAILED_MESSAGE)
        mock_pull.assert_called_once()

    def test_accepted_response_accepts_semantically_equivalent_catalog_delta(self) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-catalog-echo",
            catalog_delta={
                "items": [
                    {
                        "menu_item_id": "item_new",
                        "name": "New Item",
                        "type": "Food",
                        "is_verified": True,
                    }
                ],
                "variants": [
                    {
                        "variant_id": "var_new",
                        "variant_name": "Large",
                        "is_verified": False,
                    }
                ],
            },
        )
        accepted = self._accepted_body(event, mutation_id="mut-catalog-echo")
        accepted["catalog_delta"] = {
            "variants": [
                {
                    "variant_id": "var_new",
                    "variant_name": "Large",
                    "is_verified": 0,
                    "server_extra": "ignored",
                }
            ],
            "items": [
                {
                    "menu_item_id": "item_new",
                    "name": "New Item",
                    "type": "Food",
                    "is_verified": 1,
                }
            ],
        }

        apply_accepted(self.conn, accepted, plan)

        row = self.conn.execute(
            "SELECT name, type, is_verified FROM menu_items WHERE menu_item_id = 'item_new'"
        ).fetchone()
        self.assertEqual(row["name"], "New Item")
        self.assertEqual(row["type"], "Food")
        self.assertEqual(int(row["is_verified"]), 1)
        variant = self.conn.execute(
            "SELECT variant_name, is_verified FROM variants WHERE variant_id = 'var_new'"
        ).fetchone()
        self.assertEqual(variant["variant_name"], "Large")
        self.assertEqual(int(variant["is_verified"]), 0)

    def test_accepted_response_rejects_catalog_delta_id_mismatch(self) -> None:
        event = self._sample_event()
        plan = build_plan(
            mutation_type="menu_merge.applied",
            event=event,
            order_item_ids=["1"],
            mutation_id="mut-catalog-mismatch",
            catalog_delta={
                "items": [{"menu_item_id": "item_new", "name": "New Item", "type": "Food", "is_verified": True}],
                "variants": [],
            },
        )
        accepted = self._accepted_body(event, mutation_id="mut-catalog-mismatch")
        accepted["catalog_delta"] = {
            "items": [{"menu_item_id": "item_other", "name": "Other", "type": "Food", "is_verified": 1}],
            "variants": [],
        }

        with self.assertRaisesRegex(ValueError, "catalog_delta does not match"):
            apply_accepted(self.conn, accepted, plan)

    def test_replayed_response_does_not_rewind_revision_or_cursors(self) -> None:
        event = self._sample_event()
        plan = build_plan(mutation_type="menu_merge.applied", event=event, order_item_ids=["1"], mutation_id="mut-old")
        set_menu_state_revision(self.conn, 20)
        set_menu_merge_pull_cursor(self.conn, "15")
        set_menu_mapping_verification_pull_cursor(self.conn, "8")
        self.conn.commit()

        old_body = self._accepted_body(
            event,
            menu_revision=12,
            mutation_id="mut-old",
        )
        old_body["merge_cursor"] = "9"
        old_body["verification_cursor"] = "4"
        apply_accepted(self.conn, old_body, plan)

        self.assertEqual(self.conn.execute("SELECT value FROM system_config WHERE key = 'menu_state_revision'").fetchone()[0], "20")
        self.assertEqual(self.conn.execute("SELECT value FROM system_config WHERE key = 'menu_merge_pull_cursor'").fetchone()[0], "15")
        self.assertEqual(
            self.conn.execute("SELECT value FROM system_config WHERE key = 'menu_mapping_verification_pull_cursor'").fetchone()[0],
            "8",
        )

    def test_plan_overlap_detection(self) -> None:
        plan = build_plan(mutation_type="menu_merge.applied", order_item_ids=["1", "2"])
        self.assertTrue(plan_overlaps_pulled_changes(plan, [{"order_item_ids": ["2"]}]))
        self.assertFalse(plan_overlaps_pulled_changes(plan, [{"order_item_ids": ["9"]}]))
        self.assertTrue(plan_overlaps_pulled_changes(plan, []))
        self.assertTrue(plan_overlaps_pulled_changes(plan, [{}]))

    def test_catalog_update_conflict_detection_fails_closed(self) -> None:
        plan = build_plan(
            mutation_type=MUTATION_TYPE_CATALOG_UPDATE,
            catalog_delta={
                "items": [
                    {
                        "menu_item_id": "item_catalog",
                        "name": "Catalog Item",
                        "type": "Dessert",
                        "is_verified": True,
                    }
                ],
                "variants": [],
            },
        )
        self.assertTrue(plan_overlaps_pulled_changes(plan, [{"order_item_ids": ["unrelated"]}]))

    def test_opaque_cursor_order_uses_decoded_server_order(self) -> None:
        earlier = (
            "eyJ2IjoyLCJpbmdlc3RlZF9hdCI6IjIwMjYtMDctMDZUMTA6MDA6"
            "MDAuMDAwMDAzKzAwOjAwIiwiaWQiOjN9"
        )
        later = (
            "eyJ2IjoyLCJpbmdlc3RlZF9hdCI6IjIwMjYtMDctMDZUMTA6MDA6"
            "MDAuMDAwMDA0KzAwOjAwIiwiaWQiOjR9"
        )
        self.assertLess(later, earlier)
        self.assertTrue(pull_cursor_is_ahead(later, earlier))
        self.assertFalse(pull_cursor_is_ahead(earlier, later))

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "secret"),
    )
    @patch(
        "src.core.menu_mutation_commit.get_menu_merge_pull_endpoint",
        return_value="https://cloud.example/menu-merges",
    )
    @patch(
        "src.core.menu_mutation_commit.get_menu_mapping_verification_pull_endpoint",
        return_value="https://cloud.example/menu-mapping-verifications",
    )
    @patch("src.core.menu_mutation_commit.pull_and_apply_menu_mapping_verification_events")
    @patch("src.core.menu_mutation_commit.pull_and_apply_menu_merge_events")
    def test_pull_latest_drains_pages_before_advancing_revision(
        self,
        merge_pull,
        verification_pull,
        _verification_endpoint,
        _merge_endpoint,
        _cloud_config,
    ) -> None:
        merge_pull.side_effect = [
            {
                "error": None,
                "has_more": True,
                "events_failed": 0,
                "events_quarantined": 0,
                "advertised_menu_revision": 12,
            },
            {
                "error": None,
                "has_more": False,
                "events_failed": 0,
                "events_quarantined": 0,
                "advertised_menu_revision": 12,
            },
        ]
        verification_pull.return_value = {
            "error": None,
            "has_more": False,
            "events_failed": 0,
            "events_quarantined": 0,
            "deferred": 0,
            "advertised_menu_revision": 12,
        }

        result = pull_latest_menu_state(self.conn)

        self.assertEqual(result["menu_revision"], 12)
        self.assertEqual(merge_pull.call_count, 2)
        self.assertEqual(get_menu_state_revision(self.conn), 12)

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "secret"),
    )
    @patch(
        "src.core.menu_mutation_commit.get_menu_merge_pull_endpoint",
        return_value="https://cloud.example/menu-merges",
    )
    @patch(
        "src.core.menu_mutation_commit.get_menu_mapping_verification_pull_endpoint",
        return_value="https://cloud.example/menu-mapping-verifications",
    )
    @patch("src.core.menu_mutation_commit.pull_and_apply_menu_merge_events")
    def test_pull_latest_rejects_quarantined_page(
        self,
        merge_pull,
        _verification_endpoint,
        _merge_endpoint,
        _cloud_config,
    ) -> None:
        merge_pull.return_value = {
            "error": None,
            "has_more": False,
            "events_failed": 1,
            "events_quarantined": 1,
            "advertised_menu_revision": 12,
        }

        with self.assertRaises(MenuStatePullError):
            pull_latest_menu_state(self.conn)

        self.assertEqual(get_menu_state_revision(self.conn), 10)

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "secret"),
    )
    @patch(
        "src.core.menu_mutation_commit.get_menu_merge_pull_endpoint",
        return_value="https://cloud.example/menu-merges",
    )
    @patch(
        "src.core.menu_mutation_commit.get_menu_mapping_verification_pull_endpoint",
        return_value="https://cloud.example/menu-mapping-verifications",
    )
    @patch("src.core.menu_mutation_commit.pull_and_apply_menu_mapping_verification_events")
    @patch("src.core.menu_mutation_commit.pull_and_apply_menu_merge_events")
    def test_pull_latest_tolerates_deferred_verifications(
        self,
        merge_pull,
        verification_pull,
        _verification_endpoint,
        _merge_endpoint,
        _cloud_config,
    ) -> None:
        # A verification event for an order line this install never ingested is
        # deferred (persisted + retried by the flush pass), not failed. A
        # permanent deferral must NOT wedge the whole menu pull, else every sync
        # reports "Sync Failed" forever.
        merge_pull.return_value = {
            "error": None,
            "has_more": False,
            "events_failed": 0,
            "events_quarantined": 0,
            "advertised_menu_revision": 12,
        }
        verification_pull.return_value = {
            "error": None,
            "has_more": False,
            "events_failed": 0,
            "events_quarantined": 0,
            "deferred": 65,
            "advertised_menu_revision": 12,
        }

        result = pull_latest_menu_state(self.conn)

        self.assertEqual(result["menu_revision"], 12)
        self.assertEqual(get_menu_state_revision(self.conn), 12)


class StrictModeMergeTests(unittest.TestCase):
    @staticmethod
    def _create_db() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        bind_test_profile(conn)
        conn.executescript(
            """
            CREATE TABLE system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, order_status TEXT NOT NULL);
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                suggestion_id TEXT REFERENCES menu_items(menu_item_id),
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                total_sold INTEGER DEFAULT 0,
                total_revenue REAL DEFAULT 0,
                sold_as_item INTEGER DEFAULT 0,
                sold_as_addon INTEGER DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                is_verified BOOLEAN DEFAULT 1,
                pending_local INTEGER DEFAULT 0,
                assignment_seq INTEGER,
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
                quantity INTEGER DEFAULT 1,
                price REAL DEFAULT 0
            );
            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 1,
                updated_at TEXT
            );
            CREATE TABLE merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                affected_order_items TEXT NOT NULL,
                merged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                origin TEXT
            );
            """
        )
        conn.execute("INSERT INTO orders (order_id, order_status) VALUES (1, 'Success')")
        conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon) VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
            [
                ("item_source", "Iced Coffee", "Beverage", 4, 480.0, 4, 0),
                ("item_target", "Cold Coffee", "Beverage", 7, 910.0, 7, 0),
            ],
        )
        conn.execute(
            "INSERT INTO menu_item_variants (order_item_id, menu_item_id, is_verified) VALUES ('1', 'item_source', 1)"
        )
        conn.execute(
            "INSERT INTO order_items (order_id, menu_item_id, quantity, total_price, name_raw) VALUES (1, 'item_source', 2, 240.0, 'Iced Coffee')"
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES ('variant-large', 'Large', 1)"
        )
        set_menu_state_revision(conn, 3)
        conn.commit()
        return conn

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "secret"),
    )
    @patch("utils.menu_utils.resolve_menu_item_variant")
    def test_strict_verify_variant_change_uses_assignment_mutation(
        self,
        resolve_variant,
        _cloud_config,
    ) -> None:
        conn = self._create_db()

        def _resolve(*_args, **_kwargs):
            conn.rollback()
            return {"status": "success", "message": "resolved"}

        resolve_variant.side_effect = _resolve
        try:
            result = menu_utils.verify_item(
                conn,
                "item_source",
                new_variant_id="variant-large",
            )

            self.assertEqual(result["status"], "success", result)
            resolve_variant.assert_called_once()
            self.assertEqual(
                resolve_variant.call_args.kwargs["target_variant_id"],
                "variant-large",
            )
        finally:
            conn.close()

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_strict_plain_verify_emits_unverified_mappings(self, mock_post, _cfg, _models) -> None:
        conn = self._create_db()
        get_menu_mapping_verification_pull_cursor(conn)
        ensure_menu_mapping_verification_sync_tables(conn)
        ensure_assignment_sync_schema(conn)
        conn.execute("UPDATE menu_items SET is_verified = 0 WHERE menu_item_id = 'item_source'")
        conn.execute(
            "UPDATE menu_item_variants SET is_verified = 0 WHERE order_item_id = '1'"
        )
        conn.commit()
        captured_plan: dict = {}

        def _fake_commit(_conn, plan):
            captured_plan["plan"] = plan
            accepted_events = []
            for verification_event in plan.verification_events:
                accepted_events.append(
                    {
                        "remote_event_id": verification_event["remote_event_id"],
                        "server_seq": 88,
                        "server_ingested_at": "2026-07-06T10:00:00Z",
                    }
                )
            accepted = {
                "status": "accepted",
                "mutation_id": plan.mutation_id,
                "menu_revision": 4,
                "accepted_events": accepted_events,
                "assignment_rows": [],
                "catalog_delta": plan.catalog_delta,
                "merge_cursor": "77",
                "verification_cursor": "88",
            }
            apply_accepted(_conn, accepted, plan)
            return CommitResult(status="ok")

        try:
            with patch("src.core.menu_mutation_commit.commit_mutation", side_effect=_fake_commit):
                result = menu_utils.verify_item(conn, "item_source")

            self.assertEqual(result["status"], "success", result)
            plan = captured_plan["plan"]
            self.assertEqual(plan.mutation_type, MUTATION_TYPE_VERIFY)
            self.assertTrue(plan.verification_events)
            self.assertEqual(plan.order_item_ids, ["1"])
            item_row = conn.execute(
                "SELECT is_verified FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
            mapping_row = conn.execute(
                "SELECT is_verified FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(int(item_row["is_verified"]), 1)
            self.assertEqual(int(mapping_row["is_verified"]), 1)
            outbox_count = conn.execute(
                "SELECT COUNT(*) FROM menu_mapping_verification_sync_events"
            ).fetchone()[0]
            self.assertEqual(outbox_count, 0)
        finally:
            conn.close()

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.post")
    def test_strict_merge_applies_after_mocked_200_without_outbox(self, mock_post, _cfg, _models) -> None:
        conn = self._create_db()
        ensure_menu_merge_sync_tables(conn)
        ensure_assignment_sync_schema(conn)
        try:
            captured_plan: dict = {}

            def _fake_commit(_conn, plan):
                captured_plan["mutation_id"] = plan.mutation_id
                event = dict(plan.event)
                accepted = {
                    "status": "accepted",
                    "mutation_id": plan.mutation_id,
                    "menu_revision": 4,
                    "accepted_events": [
                        {
                            "remote_event_id": event["remote_event_id"],
                            "server_seq": 77,
                            "server_ingested_at": "2026-07-06T10:00:00Z",
                        }
                    ],
                    "assignment_rows": [],
                    "catalog_delta": plan.catalog_delta,
                    "merge_cursor": "77",
                    "verification_cursor": "0",
                }
                apply_accepted(_conn, accepted, plan)
                return CommitResult(status="ok", merge_id=1)

            with patch("src.core.menu_mutation_commit.commit_mutation", side_effect=_fake_commit):
                result = menu_utils.merge_menu_items(conn, "item_source", "item_target")

            self.assertEqual(result["status"], "success")
            outbox_count = conn.execute("SELECT COUNT(*) FROM menu_merge_sync_events").fetchone()[0]
            self.assertEqual(outbox_count, 0)
            row = conn.execute(
                "SELECT menu_item_id, pending_local FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(row["menu_item_id"], "item_target")
            self.assertEqual(int(row["pending_local"] or 0), 0)
            self.assertIn("mutation_id", captured_plan)
        finally:
            conn.close()

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.menu_mutation_commit.pull_latest_menu_state")
    @patch("requests.get")
    @patch("requests.post")
    def test_forced_retry_reuses_same_mutation_id(self, mock_post, mock_get, mock_pull, _cfg, _models) -> None:
        conn = self._create_db()
        ensure_menu_merge_sync_tables(conn)
        ensure_assignment_sync_schema(conn)
        mutation_ids: list[str] = []

        def _track_post(_url, json=None, **kwargs):
            mutation_ids.append(json["mutation_id"])
            conflict = {"status": "conflict", "conflicting_events": [{"order_item_ids": ["99"]}]}
            if len(mutation_ids) == 1:
                return Mock(status_code=409, content='{"status":"conflict"}', json=lambda: conflict)
            event_remote_id = "evt-retry"
            accepted = {
                "status": "accepted",
                "mutation_id": json["mutation_id"],
                "menu_revision": 4,
                "accepted_events": [
                    {"remote_event_id": event_remote_id, "server_seq": 80, "server_ingested_at": "2026-07-06T10:00:00Z"}
                ],
                "assignment_rows": [],
                "catalog_delta": json["catalog_delta"],
                "merge_cursor": "80",
                "verification_cursor": "0",
            }
            return Mock(status_code=200, content='{"status":"accepted"}', json=lambda: accepted)

        mock_post.side_effect = _track_post
        mock_get.side_effect = AssertionError("status GET should not run for 409 retry path")

        original_build = build_menu_merge_event_payload

        def _stable_event(conn, merge_id, event_type, **kwargs):
            payload = original_build(conn, merge_id, event_type, **kwargs)
            if payload is not None:
                payload = dict(payload)
                payload["remote_event_id"] = "evt-retry"
            return payload

        with patch(
            "src.core.menu_merge_sync_events.build_menu_merge_event_payload",
            side_effect=_stable_event,
        ):
            result = menu_utils.merge_menu_items(conn, "item_source", "item_target")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(mutation_ids), 2)
        self.assertEqual(mutation_ids[0], mutation_ids[1])
        conn.close()


class ResolveItemRenameTests(unittest.TestCase):
    @staticmethod
    def _create_db() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        bind_test_profile(conn)
        conn.executescript(
            """
            CREATE TABLE system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                suggestion_id TEXT REFERENCES menu_items(menu_item_id),
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                total_sold INTEGER DEFAULT 0,
                total_revenue REAL DEFAULT 0,
                sold_as_item INTEGER DEFAULT 0,
                sold_as_addon INTEGER DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                price DECIMAL(10,2) DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                addon_eligible BOOLEAN DEFAULT 0,
                delivery_eligible BOOLEAN DEFAULT 1,
                is_verified BOOLEAN DEFAULT 1,
                pending_local INTEGER DEFAULT 0,
                assignment_seq INTEGER,
                updated_at TEXT
            );
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                menu_item_id TEXT,
                variant_id TEXT,
                updated_at TEXT
            );
            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT
            );
            CREATE TABLE merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                affected_order_items TEXT NOT NULL,
                merged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                origin TEXT
            );
            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 1,
                updated_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES ('variant_1_piece', '1_PIECE', 1)"
        )
        conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon) "
            "VALUES ('item_source', 'Iced Coffee', 'Beverage', 1, 4, 480.0, 4, 0)"
        )
        conn.execute(
            "INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified) VALUES ('1', 'item_source', NULL, 1)"
        )
        conn.execute("INSERT INTO order_items (order_item_id, menu_item_id) VALUES (1, 'item_source')")
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example'), ('cloud_sync_api_key', 'secret')"
        )
        set_menu_state_revision(conn, 4)
        conn.commit()
        return conn

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    def test_resolve_item_rename_failed_merge_does_not_leave_orphan_target(
        self, _cfg, _models
    ) -> None:
        from utils.id_generator import generate_deterministic_id

        conn = self._create_db()
        ensure_menu_merge_sync_tables(conn)
        ensure_assignment_sync_schema(conn)
        target_id = generate_deterministic_id("Hot Coffee", "Beverage")
        try:
            with patch(
                "src.core.menu_mutation_commit.commit_mutation",
                return_value=CommitResult(status="conflict"),
            ):
                result = menu_utils.resolve_item_rename(conn, "item_source", "Hot Coffee", "Beverage")

            self.assertEqual(result["status"], "conflict")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0],
                1,
            )
            source_row = conn.execute(
                "SELECT menu_item_id, name FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
            self.assertIsNotNone(source_row)
            self.assertEqual(source_row["name"], "Iced Coffee")
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM menu_items WHERE menu_item_id = ?",
                    (target_id,),
                ).fetchone()
            )
            mapping_row = conn.execute(
                "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(mapping_row["menu_item_id"], "item_source")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
