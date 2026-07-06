"""
Phase 7 validation — strict server-authoritative menu mutation mode.

Maps to docs/MENU_SYNC_ARCHITECTURE.md §13 (strict-mode validation scenarios 1–13).
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.api.routers import operations
from src.core.services.sync_service import SyncStatus
from src.core.menu_assignment_schema import ensure_assignment_sync_schema
from src.core.menu_mapping_verification_sync import (
    apply_remote_menu_mapping_verification_event,
    pull_and_apply_menu_mapping_verification_events,
)
from src.core.menu_merge_sync import (
    apply_remote_menu_merge_event,
    pull_and_apply_menu_merge_events,
)
from src.core.menu_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    EVENT_TYPE_UNDONE,
    build_menu_merge_event_payload,
    ensure_menu_merge_sync_tables,
)
from src.core.menu_mutation_commit import (
    apply_accepted,
    build_plan,
    check_assignment_parity,
    commit_mutation,
)
from src.core.menu_outbox_drain import drain_menu_outbox, get_menu_outbox_status
from src.core.sync_identity import set_menu_state_revision, set_menu_strict_mode_enabled


def _strict_mode_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
            is_verified BOOLEAN DEFAULT 1
        );
        """
    )
    conn.execute("INSERT INTO orders (order_id, order_status) VALUES (1, 'Success')")
    conn.executemany(
        """
        INSERT INTO menu_items
        (menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon)
        VALUES (?, ?, ?, 1, 4, 480.0, 4, 0)
        """,
        [
            ("item_source", "Iced Coffee", "Beverage"),
            ("item_target", "Cold Coffee", "Beverage"),
        ],
    )
    conn.execute(
        "INSERT INTO menu_item_variants (order_item_id, menu_item_id, is_verified) VALUES ('1', 'item_source', 1)"
    )
    conn.execute(
        "INSERT INTO order_items (order_id, menu_item_id, quantity, total_price, name_raw) VALUES (1, 'item_source', 2, 240.0, 'Iced Coffee')"
    )
    conn.execute(
        "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example'), ('cloud_sync_api_key', 'secret')"
    )
    set_menu_state_revision(conn, 10)
    set_menu_strict_mode_enabled(conn, True)
    ensure_menu_merge_sync_tables(conn)
    ensure_assignment_sync_schema(conn)
    conn.commit()
    return conn


def _merge_event(*, remote_event_id: str = "evt-merge", order_item_id: str = "1") -> dict:
    return {
        "remote_event_id": remote_event_id,
        "schema_version": 2,
        "event_type": EVENT_TYPE_APPLIED,
        "occurred_at": "2026-07-06T10:00:00Z",
        "source_item": {"menu_item_id": "item_source", "name": "Iced Coffee", "type": "Beverage"},
        "target_item": {"menu_item_id": "item_target", "name": "Cold Coffee", "type": "Beverage"},
        "merge_payload": {
            "kind": "basic_merge_v1",
            "assignments": [
                {
                    "order_item_id": order_item_id,
                    "menu_item_id": "item_target",
                    "variant_id": None,
                    "is_verified": 1,
                }
            ],
        },
    }


def _accepted_body(
    event: dict,
    *,
    menu_revision: int = 11,
    assignment_rows: list | None = None,
    mutation_id: str = "mut-1",
) -> dict:
    order_item_id = event["merge_payload"]["assignments"][0]["order_item_id"]
    rows = assignment_rows
    if rows is None:
        rows = [
            {
                "order_item_id": order_item_id,
                "menu_item_id": "item_target",
                "variant_id": None,
                "is_verified": 1,
                "assignment_seq": 55,
                "verification_seq": None,
            }
        ]
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
        "assignment_rows": rows,
        "catalog_delta": {"items": [], "variants": []},
        "merge_cursor": "55",
        "verification_cursor": "0",
    }


class Phase7ValidationTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # 7.1 Two-install race
    # ------------------------------------------------------------------

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.menu_mutation_commit.pull_latest_menu_state")
    @patch("requests.post")
    def test_phase7_01_stale_merge_409_sqlite_unchanged_then_retry(self, mock_post, mock_pull, _cfg) -> None:
        conn = _strict_mode_db()
        try:
            event = _merge_event(remote_event_id="evt-race")
            overlap_plan = build_plan(
                mutation_type="menu_merge.applied",
                event=event,
                order_item_ids=["1"],
                mutation_id="mut-race",
            )
            conflict = {"status": "conflict", "conflicting_events": [{"order_item_ids": ["1"]}]}
            accepted = _accepted_body(
                _merge_event(remote_event_id="evt-race-2", order_item_id="99"),
                menu_revision=12,
                mutation_id="mut-race-2",
            )
            mock_post.side_effect = [
                Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict),
                Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted),
            ]

            overlap_result = commit_mutation(conn, overlap_plan)
            self.assertEqual(overlap_result.status, "conflict")
            row = conn.execute(
                "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(row["menu_item_id"], "item_source")

            non_overlap_plan = build_plan(
                mutation_type="menu_merge.applied",
                event=_merge_event(remote_event_id="evt-race-2", order_item_id="99"),
                order_item_ids=["99"],
                mutation_id="mut-race-2",
            )
            result = commit_mutation(conn, non_overlap_plan)
            self.assertEqual(result.status, "ok")
            mock_pull.assert_called()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.2 Undo race
    # ------------------------------------------------------------------

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.menu_mutation_commit.pull_latest_menu_state")
    @patch("requests.post")
    def test_phase7_02_stale_undo_rejected_merge_history_unchanged(self, mock_post, mock_pull, _cfg) -> None:
        conn = _strict_mode_db()
        try:
            conn.execute(
                """
                INSERT INTO merge_history
                (source_id, target_id, source_name, source_type, affected_order_items, origin)
                VALUES ('item_source', 'item_target', 'Iced Coffee', 'Beverage', ?, 'remote')
                """,
                (json.dumps({"kind": "basic_merge_v1", "affected_order_item_ids": ["1"]}),),
            )
            conn.execute(
                "UPDATE menu_item_variants SET menu_item_id = 'item_target', assignment_seq = 50 WHERE order_item_id = '1'"
            )
            conn.commit()
            history_before = conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0]

            undo_event = {
                "remote_event_id": "evt-undo-stale",
                "schema_version": 2,
                "event_type": EVENT_TYPE_UNDONE,
                "occurred_at": "2026-07-06T10:00:00Z",
                "source_item": {"menu_item_id": "item_source", "name": "Iced Coffee", "type": "Beverage"},
                "target_item": {"menu_item_id": "item_target", "name": "Cold Coffee", "type": "Beverage"},
                "merge_payload": {
                    "kind": "basic_merge_v1",
                    "assignments": [
                        {
                            "order_item_id": "1",
                            "menu_item_id": "item_source",
                            "variant_id": None,
                            "is_verified": 1,
                        }
                    ],
                },
            }
            plan = build_plan(
                mutation_type="menu_merge.undone",
                event=undo_event,
                order_item_ids=["1"],
                mutation_id="mut-undo-stale",
            )
            conflict = {"status": "conflict", "conflicting_events": [{"order_item_ids": ["1"]}]}
            mock_post.return_value = Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict)

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "conflict")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0], history_before)
            row = conn.execute(
                "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(row["menu_item_id"], "item_target")
            mock_pull.assert_called_once()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.3 Verification race
    # ------------------------------------------------------------------

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("src.core.menu_mutation_commit.pull_latest_menu_state")
    @patch("requests.post")
    def test_phase7_03_stale_verify_rejected_flag_unchanged(self, mock_post, mock_pull, _cfg) -> None:
        conn = _strict_mode_db()
        try:
            conn.execute("UPDATE menu_items SET is_verified = 0 WHERE menu_item_id = 'item_source'")
            conn.execute("UPDATE menu_item_variants SET is_verified = 0 WHERE order_item_id = '1'")
            conn.commit()

            verification_event = {
                "remote_event_id": "map-verify-stale",
                "schema_version": 1,
                "event_type": "mapping.verified",
                "occurred_at": "2026-07-06T10:00:00Z",
                "order_item_id": "1",
                "menu_item_id": "item_source",
                "variant_id": None,
                "is_verified": True,
            }
            plan = build_plan(
                mutation_type="verify",
                verification_events=[verification_event],
                order_item_ids=["1"],
                mutation_id="mut-verify-stale",
            )
            conflict = {"status": "conflict", "conflicting_events": [{"order_item_ids": ["1"]}]}
            mock_post.return_value = Mock(status_code=409, content=json.dumps(conflict), json=lambda: conflict)

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "conflict")
            self.assertEqual(
                int(conn.execute("SELECT is_verified FROM menu_items WHERE menu_item_id = 'item_source'").fetchone()[0]),
                0,
            )
            self.assertEqual(
                int(conn.execute("SELECT is_verified FROM menu_item_variants WHERE order_item_id = '1'").fetchone()[0]),
                0,
            )
            mock_pull.assert_called_once()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.4 Network failure
    # ------------------------------------------------------------------

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_phase7_04_network_failure_reconcile_404_sqlite_unchanged(self, mock_post, mock_get, _cfg) -> None:
        conn = _strict_mode_db()
        try:
            event = _merge_event(remote_event_id="evt-net")
            plan = build_plan(
                mutation_type="menu_merge.applied",
                event=event,
                order_item_ids=["1"],
                mutation_id="mut-net",
            )
            mock_post.side_effect = [Exception("timeout"), Exception("timeout")]
            mock_get.return_value = Mock(status_code=404, content="{}", json=lambda: {})

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "error")
            row = conn.execute(
                "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(row["menu_item_id"], "item_source")
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.5 Commit succeeds but client times out
    # ------------------------------------------------------------------

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_phase7_05_timeout_reconcile_200_applies_without_duplicate(self, mock_post, mock_get, _cfg) -> None:
        conn = _strict_mode_db()
        try:
            event = _merge_event(remote_event_id="evt-timeout")
            plan = build_plan(
                mutation_type="menu_merge.applied",
                event=event,
                order_item_ids=["1"],
                mutation_id="mut-timeout",
            )
            accepted = _accepted_body(event, mutation_id="mut-timeout")
            mock_post.side_effect = Exception("timeout")
            mock_get.return_value = Mock(status_code=200, content=json.dumps(accepted), json=lambda: accepted)

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "ok")
            row = conn.execute(
                "SELECT menu_item_id, pending_local FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(row["menu_item_id"], "item_target")
            self.assertEqual(int(row["pending_local"] or 0), 0)
            self.assertEqual(mock_post.call_count, 1)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.7 Accepted mutation parity
    # ------------------------------------------------------------------

    def test_phase7_07_assignment_rows_match_local_after_apply(self) -> None:
        conn = _strict_mode_db()
        try:
            event = _merge_event(remote_event_id="evt-parity")
            plan = build_plan(
                mutation_type="menu_merge.applied",
                event=event,
                order_item_ids=["1"],
                mutation_id="mut-parity",
            )
            accepted = _accepted_body(event, mutation_id="mut-parity")
            apply_accepted(conn, accepted, plan)
            self.assertEqual(check_assignment_parity(conn, accepted["assignment_rows"]), [])
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.8 Sync DB after reset (catalog before orders)
    # ------------------------------------------------------------------

    def test_phase7_08_sync_db_pulls_catalog_before_orders(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE menu_items (menu_item_id TEXT PRIMARY KEY)")
        conn.commit()

        sync_statuses = iter(
            [SyncStatus("done", "No new orders to sync", progress=1.0, stats={"fetched": 0})]
        )
        bootstrap_result = {"items_seeded": 3, "error": None}
        cloud_summary = {"attempted": True, "menu_bootstrap": None}

        with patch(
            "src.core.menu_bootstrap_sync.get_menu_bootstrap_pull_endpoint",
            return_value="https://cloud.example/menu-bootstrap/latest",
        ), patch(
            "src.core.menu_bootstrap_sync.get_menu_bootstrap_apply_mode",
            return_value="seed_only",
        ), patch(
            "src.core.config.cloud_sync_config.get_cloud_sync_config",
            return_value=("https://cloud.example", "test-key"),
        ), patch(
            "src.core.menu_bootstrap_sync.fetch_and_apply_menu_bootstrap_snapshot",
            return_value=bootstrap_result,
        ) as bootstrap_pull, patch(
            "src.api.routers.operations.sync_database",
            return_value=sync_statuses,
        ) as order_sync, patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ) as cloud_pull:
            statuses = list(operations.iter_sync_statuses(conn))

        bootstrap_pull.assert_called_once()
        order_sync.assert_called_once_with(conn)
        cloud_pull.assert_called_once_with(conn, skip_menu_bootstrap=True)
        self.assertEqual(statuses[-1].type, "done")
        conn.close()

    # ------------------------------------------------------------------
    # 7.9 Pull failure during Sync DB
    # ------------------------------------------------------------------

    def test_phase7_09_sync_db_surfaces_menu_pull_failure(self) -> None:
        conn = Mock()
        sync_statuses = iter(
            [SyncStatus("done", "Sync Complete", progress=1.0, stats={"fetched": 1})]
        )
        cloud_summary = {
            "attempted": True,
            "menu_merges": {"error": "HTTP 503"},
            "menu_bootstrap": None,
            "menu_assignments_bootstrap": None,
            "menu_mapping_verifications": None,
            "customer_merges": None,
        }
        with patch("src.api.routers.operations.sync_database", return_value=sync_statuses), patch(
            "src.api.routers.operations.run_best_effort_cloud_pulls",
            return_value=cloud_summary,
        ), patch("src.api.routers.operations._menu_items_empty", return_value=False):
            statuses = list(operations.iter_sync_statuses(conn))

        self.assertEqual(statuses[-1].type, "error")
        self.assertIn("Menu pull failed", statuses[-1].message)

    # ------------------------------------------------------------------
    # 7.10 Legacy unsent event drain
    # ------------------------------------------------------------------

    @patch("src.core.menu_outbox_drain.upload_verification_events", return_value={"events_sent": 0})
    @patch("src.core.menu_outbox_drain.upload_merge_events")
    @patch("src.core.menu_outbox_drain.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    def test_phase7_10_legacy_outbox_drain_does_not_touch_strict_state(self, _cfg, mock_merge, _verification) -> None:
        conn = _strict_mode_db()
        try:
            conn.execute(
                """
                INSERT INTO menu_merge_sync_events (event_id, event_type, payload, occurred_at)
                VALUES ('evt-legacy', 'menu_merge.applied', '{"remote_event_id":"evt-legacy"}', '2026-07-06T10:00:00Z')
                """
            )
            conn.commit()
            self.assertFalse(get_menu_outbox_status(conn)["outbox_drained"])

            def _upload(conn, **kwargs):
                conn.execute(
                    "UPDATE menu_merge_sync_events SET uploaded_at = '2026-07-06T11:00:00Z' WHERE event_id = 'evt-legacy'"
                )
                conn.commit()
                return {"events_sent": 1, "backfilled_applied": 0, "error": None}

            mock_merge.side_effect = _upload
            result = drain_menu_outbox(conn)
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["outbox_drained"])
            self.assertEqual(
                conn.execute("SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'").fetchone()[
                    "menu_item_id"
                ],
                "item_source",
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 7.12 Orphaned accepted mutation
    # ------------------------------------------------------------------

    @patch("src.core.menu_mutation_commit.get_cloud_sync_config", return_value=("https://cloud.example", "secret"))
    @patch("requests.get")
    @patch("requests.post")
    def test_phase7_12_orphaned_mutation_converges_on_next_pull(self, mock_post, mock_get, _cfg) -> None:
        conn = _strict_mode_db()
        peer = _strict_mode_db()
        try:
            event = _merge_event(remote_event_id="evt-orphan")
            plan = build_plan(
                mutation_type="menu_merge.applied",
                event=event,
                order_item_ids=["1"],
                mutation_id="mut-orphan",
            )
            mock_post.side_effect = Exception("timeout")
            mock_get.side_effect = Exception("timeout")

            result = commit_mutation(conn, plan)
            self.assertEqual(result.status, "error")
            self.assertEqual(
                conn.execute("SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'").fetchone()[
                    "menu_item_id"
                ],
                "item_source",
            )

            remote_event = dict(event)
            remote_event["server_seq"] = 88
            remote_event["server_ingested_at"] = "2026-07-06T10:05:00Z"
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "events": [remote_event],
                "next_cursor": "88",
                "scope_state": {"menu_revision": 12, "strict_mode_enabled": True},
            }
            with patch("requests.get", return_value=mock_response):
                pull_result = pull_and_apply_menu_merge_events(
                    conn,
                    endpoint="https://cloud.example/desktop-analytics-sync/menu-merges",
                    auth="secret",
                )
            self.assertIsNone(pull_result.get("error"))
            apply_remote_menu_merge_event(peer, remote_event, "88")
            peer.commit()

            local_row = conn.execute(
                "SELECT menu_item_id, assignment_seq, pending_local FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            peer_row = peer.execute(
                "SELECT menu_item_id, assignment_seq, pending_local FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(local_row["menu_item_id"], peer_row["menu_item_id"])
            self.assertEqual(local_row["assignment_seq"], peer_row["assignment_seq"])
            self.assertEqual(int(local_row["pending_local"] or 0), 0)
        finally:
            conn.close()
            peer.close()

    # ------------------------------------------------------------------
    # 7.13 Self-apply parity
    # ------------------------------------------------------------------

    def test_phase7_13_commit_apply_matches_peer_pull_apply(self) -> None:
        capture_conn = _strict_mode_db()
        peer_conn = _strict_mode_db()
        try:
            capture_conn.execute(
                """
                INSERT INTO merge_history
                (source_id, target_id, source_name, source_type, affected_order_items)
                VALUES ('item_source', 'item_target', 'Iced Coffee', 'Beverage', ?)
                """,
                (json.dumps({"kind": "basic_merge_v1", "affected_order_item_ids": ["1"]}),),
            )
            capture_conn.execute(
                "UPDATE menu_item_variants SET menu_item_id = 'item_target', pending_local = 1 WHERE order_item_id = '1'"
            )
            payload = build_menu_merge_event_payload(capture_conn, 1, EVENT_TYPE_APPLIED)
            assert payload is not None
            payload = dict(payload)
            payload["server_seq"] = 99
            capture_conn.rollback()

            event = _merge_event(remote_event_id=str(payload["remote_event_id"]))
            plan = build_plan(
                mutation_type="menu_merge.applied",
                event=event,
                order_item_ids=["1"],
                mutation_id="mut-self",
            )
            accepted = _accepted_body(
                event,
                menu_revision=11,
                mutation_id="mut-self",
            )
            accepted["accepted_events"][0]["server_seq"] = 99
            accepted["assignment_rows"][0]["assignment_seq"] = 99
            apply_accepted(capture_conn, accepted, plan)

            remote_event = dict(event)
            remote_event["server_seq"] = 99
            remote_event["server_ingested_at"] = "2026-07-06T10:00:00Z"
            apply_remote_menu_merge_event(peer_conn, remote_event, "99")
            peer_conn.commit()

            for table, query in (
                (
                    "menu_item_variants",
                    "SELECT menu_item_id, variant_id, is_verified, assignment_seq, pending_local FROM menu_item_variants WHERE order_item_id = '1'",
                ),
                (
                    "merge_history",
                    "SELECT source_id, target_id, origin FROM merge_history ORDER BY merge_id DESC LIMIT 1",
                ),
            ):
                local = capture_conn.execute(query).fetchone()
                remote = peer_conn.execute(query).fetchone()
                self.assertEqual(tuple(local), tuple(remote), f"mismatch in {table}")
        finally:
            capture_conn.close()
            peer_conn.close()


if __name__ == "__main__":
    unittest.main()
