"""
Phase 7 validation — strict server-authoritative customer mutation mode.

Maps to docs/MENU_SYNC_ARCHITECTURE.md §15 validation scenarios 1–10.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.api.routers import operations
from src.core.customer_merge_sync import (
    apply_remote_customer_merge_event,
    ensure_customer_merge_pull_tables,
    pull_and_apply_customer_merge_events,
)
from src.core.customer_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    EVENT_TYPE_UNDONE,
    SCHEMA_VERSION,
    build_merge_applied_event_payload,
)
from src.core.customer_mutation_commit import (
    apply_accepted,
    build_plan,
    commit_mutation,
)
from src.core.services.sync_service import SyncStatus
from src.core.sync_identity import set_customer_state_revision
from tests.test_customer_mutation_commit import _customer_merge_db


def _strict_mode_customer_db() -> sqlite3.Connection:
    conn = _customer_merge_db()
    ensure_customer_merge_pull_tables(conn)
    conn.execute(
        "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example')"
    )
    conn.execute(
        "INSERT INTO system_config (key, value) VALUES ('cloud_sync_api_key', 'secret')"
    )
    set_customer_state_revision(conn, 10)
    conn.commit()
    return conn


def _merge_event(*, remote_event_id: str = "evt-merge") -> dict:
    phone_hash = hashlib.sha256("9999999999".encode("utf-8")).hexdigest()
    name_address_hash = hashlib.sha256("rahul sharma|hsr layout".encode("utf-8")).hexdigest()
    return {
        "remote_event_id": remote_event_id,
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


def _accepted_body(
    event: dict,
    *,
    customer_revision: int = 11,
    mutation_id: str = "mut-1",
) -> dict:
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
        "customer_merge_cursor": "cursor-55",
    }


class CustomerPhase7ValidationTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # 7.1 Two-install race
    # ------------------------------------------------------------------

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("requests.post")
    def test_phase7_01_stale_merge_409_sqlite_unchanged_then_retry(self, mock_post, mock_pull, _cfg) -> None:
        conn = _strict_mode_customer_db()
        try:
            overlap_event = _merge_event(remote_event_id="evt-race-overlap")
            overlap_plan = build_plan(
                mutation_type="customer_merge.applied",
                event=overlap_event,
                customer_keys=overlap_event["source_customer"]["portable_locators"]["phone_hash"],
                mutation_id="mut-race-overlap",
            )
            conflict = {
                "status": "conflict",
                "conflicting_events": [
                    {"customer_keys": [overlap_event["source_customer"]["portable_locators"]["phone_hash"]]}
                ],
            }
            mock_post.return_value = Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict)

            overlap_result = commit_mutation(conn, overlap_plan)
            self.assertEqual(overlap_result.status, "conflict")
            self.assertEqual(
                conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                1,
            )

            non_overlap_event = _merge_event(remote_event_id="evt-race-retry")
            accepted = _accepted_body(non_overlap_event, customer_revision=12, mutation_id="mut-race-retry")
            mock_post.side_effect = [
                Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict),
                Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted),
            ]
            non_overlap_plan = build_plan(
                mutation_type="customer_merge.applied",
                event=non_overlap_event,
                customer_keys=["unrelated-customer-key"],
                mutation_id="mut-race-retry",
            )
            result = commit_mutation(conn, non_overlap_plan)
            self.assertEqual(result.status, "ok")
            mock_pull.assert_called()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.2 Overlapping race
    # ------------------------------------------------------------------

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("requests.post")
    def test_phase7_02_overlapping_merge_surfaces_conflict_sqlite_unchanged(self, mock_post, mock_pull, _cfg) -> None:
        conn = _strict_mode_customer_db()
        try:
            event = _merge_event(remote_event_id="evt-overlap")
            phone_hash = event["source_customer"]["portable_locators"]["phone_hash"]
            plan = build_plan(
                mutation_type="customer_merge.applied",
                event=event,
                customer_keys=[phone_hash],
                mutation_id="mut-overlap",
            )
            conflict = {"status": "conflict", "conflicting_events": [{"customer_keys": [phone_hash]}]}
            mock_post.return_value = Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict)

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "conflict")
            self.assertEqual(
                conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                1,
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customer_merge_history").fetchone()[0], 0)
            mock_pull.assert_called_once()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.3 Undo race
    # ------------------------------------------------------------------

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.customer_mutation_commit.pull_latest_customer_state")
    @patch("requests.post")
    def test_phase7_03_stale_undo_rejected_merge_history_unchanged(self, mock_post, mock_pull, _cfg) -> None:
        conn = _strict_mode_customer_db()
        try:
            conn.execute(
                """
                INSERT INTO customer_merge_history (
                    source_customer_id, target_customer_id, similarity_score, model_name,
                    suggestion_context, source_snapshot, target_snapshot, moved_order_ids, copied_address_count
                )
                VALUES (1, 2, 0.95, 'test', '{"remote_event_id":"evt-applied-base"}', '{}', '{}', '[101]', 0)
                """
            )
            conn.execute(
                "UPDATE orders SET customer_id = 2, updated_at = CURRENT_TIMESTAMP WHERE order_id = 101"
            )
            conn.commit()
            history_before = conn.execute("SELECT COUNT(*) FROM customer_merge_history").fetchone()[0]

            phone_hash = hashlib.sha256("9999999999".encode("utf-8")).hexdigest()
            undo_event = {
                "remote_event_id": "evt-undo-stale",
                "schema_version": SCHEMA_VERSION,
                "event_type": EVENT_TYPE_UNDONE,
                "occurred_at": "2026-07-06T11:00:00Z",
                "reverts_remote_event_id": "evt-applied-base",
                "source_customer": {
                    "snapshot": {"name": "Rahul Sharma", "phone": "9999999999"},
                    "portable_locators": {"phone_hash": phone_hash},
                },
                "target_customer": {
                    "snapshot": {"name": "Rahul S."},
                    "portable_locators": {},
                },
                "merge_metadata": {},
                "undo_metadata": {"original_merged_at": "2026-07-06T10:00:00Z"},
                "moved_orders": {"count": 1, "portable_refs": []},
            }
            plan = build_plan(
                mutation_type="customer_merge.undone",
                event=undo_event,
                customer_keys=[phone_hash],
                mutation_id="mut-undo-stale",
            )
            conflict = {"status": "conflict", "conflicting_events": [{"customer_keys": [phone_hash]}]}
            mock_post.return_value = Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict)

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "conflict")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customer_merge_history").fetchone()[0], history_before)
            self.assertEqual(
                conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                2,
            )
            self.assertIsNone(
                conn.execute("SELECT undone_at FROM customer_merge_history WHERE merge_id = 1").fetchone()[0]
            )
            mock_pull.assert_called_once()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.4 Network failure
    # ------------------------------------------------------------------

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_phase7_04_network_failure_reconcile_404_sqlite_unchanged(self, mock_post, mock_get, _cfg) -> None:
        conn = _strict_mode_customer_db()
        try:
            event = _merge_event(remote_event_id="evt-net")
            plan = build_plan(
                mutation_type="customer_merge.applied",
                event=event,
                mutation_id="mut-net",
            )
            mock_post.side_effect = [Exception("timeout"), Exception("timeout")]
            mock_get.return_value = Mock(status_code=404, content="{}", json=lambda: {})

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "error")
            self.assertEqual(
                conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                1,
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.5 Commit succeeds but client times out
    # ------------------------------------------------------------------

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_phase7_05_timeout_reconcile_200_applies_without_duplicate(self, mock_post, mock_get, _cfg) -> None:
        conn = _strict_mode_customer_db()
        try:
            event = _merge_event(remote_event_id="evt-timeout")
            plan = build_plan(mutation_type="customer_merge.applied", event=event, mutation_id="mut-timeout")
            accepted = _accepted_body(event, mutation_id="mut-timeout")
            mock_post.side_effect = Exception("timeout")
            mock_get.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "ok")
            self.assertEqual(
                conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                2,
            )
            self.assertEqual(mock_post.call_count, 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM customer_merge_remote_events").fetchone()[0],
                1,
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.7 Fresh install / reset convergence
    # ------------------------------------------------------------------

    def test_phase7_07_fresh_install_pull_replay_matches_reference(self) -> None:
        reference_conn = _customer_merge_db()
        fresh_conn = _customer_merge_db()
        ensure_customer_merge_pull_tables(reference_conn)
        ensure_customer_merge_pull_tables(fresh_conn)
        try:
            reference_conn.execute(
                """
                UPDATE orders SET customer_id = 2, updated_at = CURRENT_TIMESTAMP WHERE customer_id = 1
                """
            )
            merge_id = reference_conn.execute(
                """
                INSERT INTO customer_merge_history (
                    source_customer_id, target_customer_id, similarity_score, model_name,
                    suggestion_context, source_snapshot, target_snapshot, moved_order_ids, copied_address_count
                )
                VALUES (1, 2, 0.95, 'test', '{}', '{}', '{}', '[101]', 0)
                RETURNING merge_id
                """
            ).fetchone()[0]
            payload = build_merge_applied_event_payload(reference_conn, int(merge_id))
            self.assertIsNotNone(payload)
            assert payload is not None
            remote_event = dict(payload)
            remote_event["server_seq"] = 77
            remote_event["server_ingested_at"] = "2026-07-06T10:00:00Z"
            reference_conn.rollback()

            apply_remote_customer_merge_event(reference_conn, remote_event, "cursor-77")
            reference_conn.commit()

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "events": [remote_event],
                "next_cursor": "cursor-77",
                "customer_revision": 77,
            }
            with patch("requests.get", return_value=mock_response):
                pull_result = pull_and_apply_customer_merge_events(
                    fresh_conn,
                    endpoint="https://cloud.example/desktop-analytics-sync/customer-merges",
                    auth="secret",
                )
            self.assertIsNone(pull_result.get("error"))
            self.assertEqual(pull_result["merge_events_applied"], 1)

            for query in (
                "SELECT customer_id FROM orders WHERE order_id = 101",
                "SELECT source_customer_id, target_customer_id FROM customer_merge_history ORDER BY merge_id DESC LIMIT 1",
            ):
                local = fresh_conn.execute(query).fetchone()
                remote = reference_conn.execute(query).fetchone()
                self.assertEqual(tuple(local), tuple(remote))
        finally:
            reference_conn.close()
            fresh_conn.close()

    # ------------------------------------------------------------------
    # 7.8 Sync DB pull failure
    # ------------------------------------------------------------------

    def test_phase7_08_sync_db_surfaces_customer_pull_failure(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )
        cloud_summary = {
            "attempted": True,
            "menu_merges": None,
            "menu_bootstrap": None,
            "menu_assignments_bootstrap": None,
            "menu_mapping_verifications": None,
            "customer_merges": {"error": "HTTP 503"},
        }
        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ), patch("src.api.routers.operations._menu_items_empty", return_value=False):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(statuses[-1].type, "error")
        self.assertIn("Customer pull failed", statuses[-1].message)

    # ------------------------------------------------------------------
    # 7.9 Self-apply parity
    # ------------------------------------------------------------------

    def test_phase7_09_commit_apply_matches_peer_pull_apply(self) -> None:
        capture_conn = _strict_mode_customer_db()
        peer_conn = _strict_mode_customer_db()
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
            capture_conn.rollback()

            event = _merge_event(remote_event_id=str(payload["remote_event_id"]))
            plan = build_plan(
                mutation_type="customer_merge.applied",
                event=event,
                mutation_id="mut-self",
            )
            accepted = _accepted_body(event, customer_revision=11, mutation_id="mut-self")
            accepted["accepted_events"][0]["server_seq"] = 99
            apply_accepted(capture_conn, accepted, plan)

            remote_event = dict(event)
            remote_event["server_seq"] = 99
            remote_event["server_ingested_at"] = "2026-07-06T10:00:00Z"
            apply_remote_customer_merge_event(peer_conn, remote_event, "cursor-99")
            peer_conn.commit()

            for table, query in (
                ("orders", "SELECT customer_id FROM orders WHERE order_id = 101"),
                (
                    "customer_merge_history",
                    "SELECT source_customer_id, target_customer_id FROM customer_merge_history ORDER BY merge_id DESC LIMIT 1",
                ),
                (
                    "customer_addresses",
                    "SELECT customer_id, address_line_1 FROM customer_addresses ORDER BY address_id",
                ),
            ):
                local = capture_conn.execute(query).fetchall()
                remote = peer_conn.execute(query).fetchall()
                self.assertEqual(local, remote, f"mismatch in {table}")
        finally:
            capture_conn.close()
            peer_conn.close()

    # ------------------------------------------------------------------
    # 7.10 Orphaned accepted mutation
    # ------------------------------------------------------------------

    @patch("src.core.customer_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_phase7_10_orphaned_mutation_converges_on_next_pull(self, mock_post, mock_get, _cfg) -> None:
        conn = _strict_mode_customer_db()
        peer = _strict_mode_customer_db()
        try:
            event = _merge_event(remote_event_id="evt-orphan")
            plan = build_plan(
                mutation_type="customer_merge.applied",
                event=event,
                mutation_id="mut-orphan",
            )
            mock_post.side_effect = Exception("timeout")
            mock_get.side_effect = Exception("timeout")

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "error")
            self.assertEqual(
                conn.execute("SELECT customer_id FROM orders WHERE order_id = 101").fetchone()[0],
                1,
            )

            remote_event = dict(event)
            remote_event["server_seq"] = 88
            remote_event["server_ingested_at"] = "2026-07-06T10:05:00Z"
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "events": [remote_event],
                "next_cursor": "cursor-88",
                "customer_revision": 12,
            }
            with patch("requests.get", return_value=mock_response):
                pull_result = pull_and_apply_customer_merge_events(
                    conn,
                    endpoint="https://cloud.example/desktop-analytics-sync/customer-merges",
                    auth="secret",
                )
            self.assertIsNone(pull_result.get("error"))
            apply_remote_customer_merge_event(peer, remote_event, "cursor-88")
            peer.commit()

            local_row = conn.execute(
                "SELECT customer_id FROM orders WHERE order_id = 101"
            ).fetchone()
            peer_row = peer.execute(
                "SELECT customer_id FROM orders WHERE order_id = 101"
            ).fetchone()
            self.assertEqual(local_row["customer_id"], peer_row["customer_id"])
        finally:
            conn.close()
            peer.close()


if __name__ == "__main__":
    unittest.main()
