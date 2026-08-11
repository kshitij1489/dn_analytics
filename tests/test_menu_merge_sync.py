import json
import sqlite3
import unittest
from tests.profile_test_helpers import bind_test_profile
from unittest.mock import Mock, patch

from src.core.menu_merge_sync import (
    get_menu_merge_pull_cursor,
    pull_and_apply_menu_merge_events,
    set_menu_merge_pull_cursor,
)
from src.core.menu_merge_sync_events import ensure_menu_merge_sync_tables
from src.core.menu_sync_quarantine import (
    dismiss_sync_conflict,
    list_sync_conflicts,
    quarantine_event,
)
from src.core.sync_cursor_migration import (
    SYNC_CURSOR_SCHEMA_VERSION,
    SYNC_CURSOR_SCHEMA_VERSION_KEY,
)
from tests.strict_commit_test_helpers import make_capturing_menu_commit, make_fake_menu_commit
from utils import menu_utils


class MenuMergeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = self._create_db()

    @staticmethod
    def _create_db() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        bind_test_profile(conn)
        conn.executescript(
            """
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                order_status TEXT NOT NULL
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

            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            );

            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT,
                price REAL DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                addon_eligible BOOLEAN DEFAULT 0,
                delivery_eligible BOOLEAN DEFAULT 1,
                is_verified BOOLEAN DEFAULT 1,
                updated_at TEXT
            );

            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT,
                quantity INTEGER DEFAULT 1,
                total_price REAL DEFAULT 0,
                name_raw TEXT,
                updated_at TEXT
            );

            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_item_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT,
                quantity INTEGER DEFAULT 0,
                price REAL DEFAULT 0
            );

            CREATE TABLE merge_history (
                merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                affected_order_items TEXT NOT NULL,
                merged_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            INSERT INTO orders (order_id, order_status)
            VALUES (1, 'Success')
            """
        )
        conn.executemany(
            """
            INSERT INTO menu_items (
                menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("item_source", "Iced Coffee", "Beverage", 1, 4, 480.0, 4, 0),
                ("item_target", "Cold Coffee", "Beverage", 1, 7, 910.0, 7, 0),
            ],
        )
        conn.execute(
            """
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES ('variant_1_piece', '1_PIECE', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO order_items (order_id, menu_item_id, quantity, total_price, name_raw)
            VALUES (1, 'item_source', 2, 240.0, 'Iced Coffee')
            """
        )
        conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES ('1', 'item_source', NULL, 1)
            """
        )
        conn.commit()

        from src.core.sync_identity import set_menu_state_revision

        set_menu_state_revision(conn, 1)
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example'), ('cloud_sync_api_key', 'secret')"
        )
        conn.commit()
        return conn

    def _seed_strict_apply_tables(self) -> None:
        # A strict commit self-applies its own merge + verification events through
        # the pull appliers, which read menu_merge_remote_events and
        # menu_mapping_verification_remote_events (production creates these via
        # migration; the plain pull tests get them lazily on first pull).
        from src.core.menu_mapping_verification_sync import _ensure_tables as _ensure_verification_tables

        ensure_menu_merge_sync_tables(self.conn)
        _ensure_verification_tables(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_remap_rides_merge_stream_and_applies_on_peer(self, _mock_models) -> None:
        # A per-order-item remap emits a merge-stream event whose assignments
        # carry the new menu/variant, so peers receive the full mapping and the
        # echo of our own event clears pending_local. The is_verified flag rides
        # the SEPARATE verification stream (its sole owner) via a companion
        # verification event — merge apply only reassigns the mapping and seeds
        # the flag on brand-new rows, never on an existing one.
        self._seed_strict_apply_tables()
        captured: dict = {}
        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=make_capturing_menu_commit(captured),
        ):
            result = menu_utils.remap_order_item_cluster(
                self.conn, "1", "item_target", "variant_1_piece"
            )
        self.assertEqual(result["status"], "success", result.get("message"))

        # The strict path carries the merge-stream event in the mutation plan (not
        # the legacy outbox), so read it from the captured commit.
        payload = captured["merge_event"]
        self.assertEqual(payload["merge_payload"]["kind"], "order_item_remap_v1")
        self.assertEqual(
            payload["merge_payload"]["assignments"],
            [
                {
                    "order_item_id": "1",
                    "menu_item_id": "item_target",
                    "variant_id": "variant_1_piece",
                    "is_verified": 1,
                }
            ],
        )

        # A companion verification event carries the is_verified=1 decision on
        # the verification stream (the flag's single owner).
        verification_payloads = captured["verification_events"]
        self.assertEqual(len(verification_payloads), 1)
        self.assertEqual(verification_payloads[0]["order_item_id"], "1")
        self.assertEqual(verification_payloads[0]["menu_item_id"], "item_target")
        self.assertEqual(int(verification_payloads[0]["is_verified"]), 1)

        # Apply the event on a peer: the mapping (menu_item_id / variant_id)
        # lands via the merge stream, but the flag does NOT — the peer's row
        # keeps whatever is_verified the verification stream last gave it.
        peer = self._create_db()
        try:
            peer.execute(
                "UPDATE menu_item_variants SET is_verified = 0 WHERE order_item_id = '1'"
            )
            peer.commit()
            remote_event = dict(payload)
            remote_event["server_seq"] = 41
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "events": [remote_event],
                "next_cursor": "cursor-remap",
            }
            with patch("requests.get", return_value=mock_response):
                pull_result = pull_and_apply_menu_merge_events(
                    peer,
                    endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
                )
            self.assertIsNone(pull_result["error"])
            self.assertEqual(pull_result["merge_events_applied"], 1)

            peer_row = peer.execute(
                "SELECT menu_item_id, variant_id, is_verified FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()
            self.assertEqual(peer_row["menu_item_id"], "item_target")
            self.assertEqual(peer_row["variant_id"], "variant_1_piece")
            # Merge apply left the flag untouched on the existing row.
            self.assertEqual(int(peer_row["is_verified"]), 0)

            peer_history = peer.execute(
                "SELECT origin FROM merge_history"
            ).fetchone()
            self.assertEqual(peer_history["origin"], "remote")
        finally:
            peer.close()

        # Echo our own event back: pending_local clears, assignment_seq stamps.
        echo_event = dict(payload)
        echo_event["server_seq"] = 41
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": [echo_event],
            "next_cursor": "cursor-echo",
        }
        with patch("requests.get", return_value=mock_response):
            echo_result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )
        self.assertIsNone(echo_result["error"])

        own_row = self.conn.execute(
            "SELECT pending_local, assignment_seq FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(int(own_row["pending_local"] or 0), 0)
        self.assertEqual(int(own_row["assignment_seq"]), 41)

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_remap_undo_restores_order_items_after_commit(self, _mock_models) -> None:
        # Regression: the remap must relink order_items / order_item_addons and
        # record their prior rows, so that once the merge stream has moved them to
        # the target, an undo restores all three tables. Before the fix, undo
        # reverted only menu_item_variants and left order_items stranded on the
        # target. Both the remap and its undo run through the strict commit path,
        # which self-applies the merge stream as it lands (no separate echo).
        self._seed_strict_apply_tables()
        captured: dict = {}
        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=make_capturing_menu_commit(captured),
        ):
            result = menu_utils.remap_order_item_cluster(
                self.conn, "1", "item_target", "variant_1_piece"
            )
            self.assertEqual(result["status"], "success", result.get("message"))
            merge_id = result["merge_id"]

            # The commit self-applied the merge stream, so the relink has landed.
            order_row = self.conn.execute(
                "SELECT menu_item_id, variant_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()
            self.assertEqual(order_row["menu_item_id"], "item_target")
            self.assertEqual(order_row["variant_id"], "variant_1_piece")

            # Undo: the parent returns to the pre-remap source mapping. The
            # pre-remap variant was NULL, which the applier treats as
            # "variant unspecified" (menu_item_variants.variant_id is NOT NULL
            # on the live schema), so the remapped variant is kept.
            undo = menu_utils.undo_merge(self.conn, merge_id)
        self.assertEqual(undo["status"], "success", undo.get("message"))

        mapping_row = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM menu_item_variants WHERE order_item_id = '1'"
        ).fetchone()
        self.assertEqual(mapping_row["menu_item_id"], "item_source")
        self.assertEqual(mapping_row["variant_id"], "variant_1_piece")

        order_row = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM order_items WHERE order_item_id = 1"
        ).fetchone()
        self.assertEqual(order_row["menu_item_id"], "item_source")
        self.assertEqual(order_row["variant_id"], "variant_1_piece")

    def test_pull_applies_and_undoes_remote_menu_merge_events(self) -> None:
        remote_events = [
            {
                "remote_event_id": "remote-menu-merge-1",
                "schema_version": 1,
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:00:00Z",
                "source_item": {
                    "menu_item_id": "item_source",
                    "name": "Iced Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {
                    "kind": "basic_merge_v1",
                },
            },
            {
                "remote_event_id": "remote-menu-merge-undo-1",
                "schema_version": 1,
                "event_type": "menu_merge.undone",
                "occurred_at": "2026-04-14T10:05:00Z",
                "reverts_remote_event_id": "remote-menu-merge-1",
                "source_item": {
                    "menu_item_id": "item_source",
                    "name": "Iced Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {
                    "kind": "basic_merge_v1",
                },
            },
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": remote_events,
            "next_cursor": "cursor-2",
        }

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertIsNone(result["error"])
        self.assertEqual(result["events_fetched"], 2)
        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["undo_events_applied"], 1)
        self.assertEqual(result["events_skipped"], 0)
        self.assertEqual(result["cursor_after"], "cursor-2")

        restored_item = self.conn.execute(
            "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
        ).fetchone()
        self.assertEqual(restored_item["menu_item_id"], "item_source")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM menu_merge_remote_events").fetchone()[0],
            2,
        )

    def test_repeat_resolution_of_same_pair_gets_its_own_history_row(self) -> None:
        # Regression: a strict-mode resolution rolls its local merge_history row
        # back and re-materializes it from the server's accepted response. When the
        # same item + variant pair is resolved again later (new orders keep landing
        # on the old mapping), the new event's content signature is identical to the
        # first resolution's history row, so the apply used to claim that months-old
        # row as "duplicate": no new row, nothing in Resolution History, no undo.
        self._seed_strict_apply_tables()
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES ('variant_unknown', 'UNKNOWN', 0)"
        )
        earlier_history_id = self.conn.execute(
            """
            INSERT INTO merge_history (
                source_id, target_id, source_name, source_type, affected_order_items, merged_at, origin
            )
            VALUES ('item_source', 'item_target', 'Iced Coffee', 'Beverage', ?, '2026-07-10 17:50:27', 'remote')
            """,
            (
                json.dumps(
                    {
                        "kind": "resolution_variant_v1",
                        "source_variant_id": "variant_unknown",
                        "target_variant_id": "variant_1_piece",
                    }
                ),
            ),
        ).lastrowid
        self.conn.execute(
            """
            INSERT INTO menu_merge_remote_events (
                remote_event_id, event_type, local_merge_id, payload, occurred_at
            )
            VALUES ('remote-resolution-july', 'menu_merge.applied', ?, '{}', '2026-07-10T17:50:27Z')
            """,
            (earlier_history_id,),
        )
        self.conn.commit()

        repeat_event = {
            "remote_event_id": "remote-resolution-august",
            "schema_version": 2,
            "event_type": "menu_merge.applied",
            "occurred_at": "2026-08-09T05:41:20Z",
            "server_seq": 668,
            "source_item": {
                "menu_item_id": "item_source",
                "name": "Iced Coffee",
                "type": "Beverage",
                "is_verified": True,
            },
            "target_item": {
                "menu_item_id": "item_target",
                "name": "Cold Coffee",
                "type": "Beverage",
                "is_verified": True,
            },
            "merge_payload": {
                "kind": "resolution_variant_v1",
                "resolution": {
                    "source_variant_id": "variant_unknown",
                    "target_variant_id": "variant_1_piece",
                },
                "assignments": [
                    {
                        "order_item_id": "1",
                        "menu_item_id": "item_target",
                        "variant_id": "variant_1_piece",
                        "is_verified": 1,
                    }
                ],
            },
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": [repeat_event],
            "next_cursor": "cursor-repeat",
        }

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertIsNone(result["error"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0],
            2,
        )
        claimed_id = self.conn.execute(
            "SELECT local_merge_id FROM menu_merge_remote_events WHERE remote_event_id = 'remote-resolution-august'"
        ).fetchone()["local_merge_id"]
        self.assertIsNotNone(claimed_id)
        self.assertNotEqual(int(claimed_id), int(earlier_history_id))

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_strict_resolution_records_history_beside_identical_older_merge(self, _mock_models) -> None:
        # Same regression from the commit side: the strict path rolls its local
        # merge_history row back and re-materializes it by self-applying the
        # server's accepted event, so an identical older resolution must not
        # absorb it.
        self._seed_strict_apply_tables()
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES ('variant_unknown', 'UNKNOWN', 0)"
        )
        self.conn.execute(
            "UPDATE menu_item_variants SET variant_id = 'variant_unknown', is_verified = 0 WHERE order_item_id = '1'"
        )
        earlier_history_id = self.conn.execute(
            """
            INSERT INTO merge_history (
                source_id, target_id, source_name, source_type, affected_order_items, merged_at, origin
            )
            VALUES ('item_source', 'item_target', 'Iced Coffee', 'Beverage', ?, '2026-07-10 17:50:27', 'remote')
            """,
            (
                json.dumps(
                    {
                        "kind": "resolution_variant_v1",
                        "source_variant_id": "variant_unknown",
                        "target_variant_id": "variant_1_piece",
                    }
                ),
            ),
        ).lastrowid
        self.conn.execute(
            """
            INSERT INTO menu_merge_remote_events (
                remote_event_id, event_type, local_merge_id, payload, occurred_at
            )
            VALUES ('remote-resolution-july', 'menu_merge.applied', ?, '{}', '2026-07-10T17:50:27Z')
            """,
            (earlier_history_id,),
        )
        self.conn.commit()

        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=make_fake_menu_commit(),
        ):
            result = menu_utils.resolve_menu_item_variant(
                self.conn,
                source_menu_item_id="item_source",
                source_variant_id="variant_unknown",
                target_menu_item_id="item_target",
                target_variant_id="variant_1_piece",
            )

        self.assertEqual(result["status"], "success", result.get("message"))
        history_ids = [
            int(row["merge_id"])
            for row in self.conn.execute("SELECT merge_id FROM merge_history ORDER BY merge_id").fetchall()
        ]
        self.assertEqual(len(history_ids), 2, history_ids)
        self.assertNotEqual(history_ids[-1], int(earlier_history_id))

    def test_pull_skips_unappliable_event_and_keeps_going(self) -> None:
        # First event is un-appliable (no derivable assignments and a source item
        # whose snapshot is too bare to resurrect); the pull must not halt on it.
        # The second, valid event should still apply and the cursor should
        # advance past the whole page.
        remote_events = [
            {
                "remote_event_id": "remote-bad-1",
                "schema_version": 1,
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:00:00Z",
                "source_item": {
                    "menu_item_id": "item_missing",
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {"kind": "basic_merge_v1"},
            },
            {
                "remote_event_id": "remote-good-1",
                "schema_version": 1,
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:01:00Z",
                "source_item": {
                    "menu_item_id": "item_source",
                    "name": "Iced Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {"kind": "basic_merge_v1"},
            },
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": remote_events,
            "next_cursor": "cursor-9",
        }

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        # A per-event failure is not a transport error, so error stays None and the
        # cursor advances so future pulls do not re-fetch the un-appliable event.
        self.assertIsNone(result["error"])
        self.assertEqual(result["events_fetched"], 2)
        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["events_failed"], 1)
        self.assertEqual(result["events_quarantined"], 1)
        self.assertIsNotNone(result["last_event_error"])
        self.assertEqual(result["cursor_after"], "cursor-9")

        # The failed event is quarantined (not silently dropped) for retry/review.
        quarantine_row = self.conn.execute(
            "SELECT stream, error, fail_count, resolved_at FROM menu_sync_event_quarantine"
            " WHERE remote_event_id = 'remote-bad-1'"
        ).fetchone()
        self.assertIsNotNone(quarantine_row)
        self.assertEqual(quarantine_row["stream"], "menu_merge")
        self.assertEqual(int(quarantine_row["fail_count"]), 1)
        self.assertIsNone(quarantine_row["resolved_at"])
        self.assertIn("not found", quarantine_row["error"])

        # The good merge landed; the bad one left no partial state behind.
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-good-1'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-bad-1'"
            ).fetchone()[0],
            0,
        )

    def test_retry_drains_quarantine_on_next_pull(self) -> None:
        # A quarantined event that has since become appliable is retried at the
        # start of the next pull, applied, and marked resolved.
        event = {
            "remote_event_id": "remote-quarantined-1",
            "schema_version": 1,
            "event_type": "menu_merge.applied",
            "occurred_at": "2026-04-14T10:00:00Z",
            "source_item": {
                "menu_item_id": "item_source",
                "name": "Iced Coffee",
                "type": "Beverage",
                "is_verified": True,
            },
            "target_item": {
                "menu_item_id": "item_target",
                "name": "Cold Coffee",
                "type": "Beverage",
                "is_verified": True,
            },
            "merge_payload": {"kind": "basic_merge_v1"},
        }
        ensure_menu_merge_sync_tables(self.conn)
        quarantine_event(self.conn, "menu_merge", "remote-quarantined-1", event, "was failing")
        self.conn.commit()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [], "next_cursor": None}

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertEqual(result["quarantine_retried"], 1)
        self.assertEqual(result["quarantine_resolved"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-quarantined-1'"
            ).fetchone()[0],
            1,
        )
        quarantine_row = self.conn.execute(
            "SELECT resolved_at FROM menu_sync_event_quarantine WHERE remote_event_id = 'remote-quarantined-1'"
        ).fetchone()
        self.assertIsNotNone(quarantine_row["resolved_at"])
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()["menu_item_id"],
            "item_target",
        )

    @staticmethod
    def _self_merge_audit_event(remote_event_id: str) -> dict:
        # Real shape from the April addon-consolidation events: source == target
        # by design, merge_payload kind basic_merge_v1 but a mapping_audit_v1
        # history with no affected_order_item_ids → no derivable assignments.
        item = {
            "menu_item_id": "item_target",
            "name": "Cold Coffee",
            "type": "Beverage",
            "is_verified": True,
        }
        return {
            "remote_event_id": remote_event_id,
            "schema_version": 1,
            "event_type": "menu_merge.applied",
            "occurred_at": "2026-04-16T19:13:47Z",
            "source_item": dict(item),
            "target_item": dict(item),
            "merge_payload": {
                "kind": "basic_merge_v1",
                "history_payload": {
                    "kind": "mapping_audit_v1",
                    "menu_item_id": "item_target",
                    "actions": ["Consolidated duplicate addon keys"],
                },
            },
        }

    def test_same_item_audit_event_applies_as_noop(self) -> None:
        # A same-item event with no derivable assignments must be recorded and
        # skipped, not replayed through the cluster path (which would raise
        # "Cannot merge item into itself" and quarantine it forever).
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": [self._self_merge_audit_event("remote-audit-1")],
            "next_cursor": "cursor-10",
        }

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertIsNone(result["error"])
        self.assertEqual(result["events_failed"], 0)
        self.assertEqual(result["events_quarantined"], 0)
        self.assertEqual(result["events_skipped"], 1)
        self.assertEqual(result["cursor_after"], "cursor-10")
        # Recorded for dedupe so a re-pull skips it as a duplicate.
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-audit-1'"
            ).fetchone()[0],
            1,
        )
        # No state was touched.
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM menu_item_variants WHERE order_item_id = '1'"
            ).fetchone()["menu_item_id"],
            "item_source",
        )

    def test_quarantined_self_merge_event_drains_as_noop(self) -> None:
        # Events quarantined by the old behavior resolve on the next pull's
        # retry pass once the no-op path recognizes them.
        event = self._self_merge_audit_event("remote-audit-stuck-1")
        ensure_menu_merge_sync_tables(self.conn)
        quarantine_event(
            self.conn, "menu_merge", "remote-audit-stuck-1", event,
            "Cannot merge item into itself",
        )
        self.conn.commit()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [], "next_cursor": None}

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertEqual(result["quarantine_retried"], 1)
        self.assertEqual(result["quarantine_resolved"], 1)
        quarantine_row = self.conn.execute(
            "SELECT resolved_at FROM menu_sync_event_quarantine WHERE remote_event_id = 'remote-audit-stuck-1'"
        ).fetchone()
        self.assertIsNotNone(quarantine_row["resolved_at"])

    def test_cursor_reset_happens_exactly_once(self) -> None:
        # Pre-migration state: v1 cursors exist and no schema version is recorded.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO system_config (key, value) VALUES (?, ?)",
            [
                ("menu_merge_pull_cursor", "v1-cursor-a"),
                ("menu_mapping_verification_pull_cursor", "v1-cursor-b"),
                ("customer_merge_pull_cursor", "v1-cursor-c"),
            ],
        )
        self.conn.commit()

        # First access migrates: all three cursors are dropped, version recorded.
        self.assertIsNone(get_menu_merge_pull_cursor(self.conn))
        for key in (
            "menu_merge_pull_cursor",
            "menu_mapping_verification_pull_cursor",
            "customer_merge_pull_cursor",
        ):
            row = self.conn.execute(
                "SELECT value FROM system_config WHERE key = ?", (key,)
            ).fetchone()
            self.assertIsNone(row, key)
        version_row = self.conn.execute(
            "SELECT value FROM system_config WHERE key = ?",
            (SYNC_CURSOR_SCHEMA_VERSION_KEY,),
        ).fetchone()
        self.assertEqual(version_row["value"], SYNC_CURSOR_SCHEMA_VERSION)

        # Once migrated, newly stored cursors survive subsequent accesses.
        set_menu_merge_pull_cursor(self.conn, "v2-cursor")
        self.conn.commit()
        self.assertEqual(get_menu_merge_pull_cursor(self.conn), "v2-cursor")

    def test_sync_conflicts_listing_and_dismiss(self) -> None:
        ensure_menu_merge_sync_tables(self.conn)
        quarantine_event(
            self.conn,
            "menu_merge",
            "remote-conflict-1",
            {
                "remote_event_id": "remote-conflict-1",
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:00:00Z",
                "source_item": {"menu_item_id": "item_source", "name": "Iced Coffee"},
                "target_item": {"menu_item_id": "item_target", "name": "Cold Coffee"},
            },
            "Source variant was not found",
        )
        self.conn.commit()

        conflicts = list_sync_conflicts(self.conn)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["remote_event_id"], "remote-conflict-1")
        self.assertEqual(conflicts[0]["summary"]["source_name"], "Iced Coffee")
        self.assertEqual(conflicts[0]["summary"]["target_name"], "Cold Coffee")

        self.assertTrue(dismiss_sync_conflict(self.conn, "remote-conflict-1"))
        self.conn.commit()
        self.assertEqual(list_sync_conflicts(self.conn), [])
        # Dismissing an unknown/already-resolved conflict reports failure.
        self.assertFalse(dismiss_sync_conflict(self.conn, "remote-conflict-1"))

    def test_pull_backfills_missing_source_item(self) -> None:
        # Source item is absent locally (already merged away on this device); the
        # pull should recreate it from the event snapshot and apply the merge.
        self.conn.execute("DELETE FROM order_items WHERE menu_item_id = 'item_source'")
        self.conn.execute("DELETE FROM menu_item_variants WHERE menu_item_id = 'item_source'")
        self.conn.execute("DELETE FROM menu_items WHERE menu_item_id = 'item_source'")
        self.conn.commit()

        remote_events = [
            {
                "remote_event_id": "remote-backfill-1",
                "schema_version": 1,
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:00:00Z",
                "source_item": {
                    "menu_item_id": "item_source",
                    "name": "Iced Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {"kind": "basic_merge_v1"},
            }
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": remote_events, "next_cursor": "cursor-3"}

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertIsNone(result["error"])
        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["events_failed"], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM menu_merge_remote_events WHERE remote_event_id = 'remote-backfill-1'"
            ).fetchone()[0],
            1,
        )

    # --- State-scoped husk sweep (sweep_orphan_menu_entities) ---

    def _seed_husks(self) -> None:
        # Husk item: no mapping row, no order rows — an event batch moved its
        # last reference away without GC'ing it (the touched-set leak).
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES ('item_husk', 'Legacy Flavor', 'Ice Cream', 1)"
        )
        # A stale merge suggestion pointing at the husk (suggestion_id self-FK
        # must be cleared before the DELETE).
        self.conn.execute(
            "UPDATE menu_items SET suggestion_id = 'item_husk' WHERE menu_item_id = 'item_target'"
        )
        # Husk variant: nothing maps to it, no order row references it, and it
        # is past the 7-day creation grace window.
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified, created_at) VALUES ('variant_husk', 'UNKNOWN_80GMS', 1, '2020-01-01 00:00:00')"
        )
        # Keep item_target live (in the base fixture it has no mapping or
        # order rows and would itself be swept).
        self.conn.execute(
            "INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified) VALUES ('target-key', 'item_target', 'variant_1_piece', 1)"
        )
        self.conn.commit()

    def test_sweep_deletes_unreferenced_items_and_variants(self) -> None:
        self._seed_husks()
        # Referenced-by-order-row-only variant must survive (usage counts even
        # without a mapping row).
        self.conn.execute(
            "UPDATE order_items SET variant_id = 'variant_1_piece' WHERE order_item_id = 1"
        )
        self.conn.commit()

        cursor = self.conn.cursor()
        swept = menu_utils.sweep_orphan_menu_entities(cursor)
        self.conn.commit()

        self.assertEqual(swept["menu_item_ids"], ["item_husk"])
        self.assertEqual(swept["variant_ids"], ["variant_husk"])
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM menu_items WHERE menu_item_id = 'item_husk'"
            ).fetchone()
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM variants WHERE variant_id = 'variant_husk'"
            ).fetchone()
        )
        # The stale suggestion pointer was cleared, not left dangling.
        self.assertIsNone(
            self.conn.execute(
                "SELECT suggestion_id FROM menu_items WHERE menu_item_id = 'item_target'"
            ).fetchone()["suggestion_id"]
        )
        # Live rows survive: item_source has a mapping + an order row,
        # item_target exists with the seeded mapping's owner intact.
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM menu_items WHERE menu_item_id = 'item_source'"
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM variants WHERE variant_id = 'variant_1_piece'"
            ).fetchone()
        )

    def test_fresh_unreferenced_variant_survives_sweep(self) -> None:
        # POST /menu/variants/create inserts a bare variant row before any
        # mapping references it; the 7-day grace window must keep it alive.
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES ('variant_fresh', 'NEW_TUB_250GMS', 1)"
        )
        self.conn.commit()

        cursor = self.conn.cursor()
        swept = menu_utils.sweep_orphan_menu_entities(cursor)
        self.conn.commit()

        self.assertNotIn("variant_fresh", swept["variant_ids"])
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM variants WHERE variant_id = 'variant_fresh'"
            ).fetchone()
        )

    def test_zero_sales_item_with_mapping_survives_sweep(self) -> None:
        # A manually created item that has a mapping row but no sales yet must
        # NOT be swept — liveness is any reference, not sales.
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES ('item_new', 'Brand New Flavor', 'Ice Cream', 1)"
        )
        self.conn.execute(
            "INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified) VALUES ('new-key', 'item_new', 'variant_1_piece', 1)"
        )
        self.conn.commit()

        cursor = self.conn.cursor()
        swept = menu_utils.sweep_orphan_menu_entities(cursor)
        self.conn.commit()

        self.assertNotIn("item_new", swept["menu_item_ids"])
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM menu_items WHERE menu_item_id = 'item_new'"
            ).fetchone()
        )

    def test_eventless_pull_sweeps_husks(self) -> None:
        # A pull that applies no events must still repair husks left by older
        # builds or missed batches (the pull-end invariant).
        self._seed_husks()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [], "next_cursor": None}

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertIsNone(result["error"])
        self.assertEqual(result["events_fetched"], 0)
        self.assertEqual(result["husks_swept"], 1)
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM menu_items WHERE menu_item_id = 'item_husk'"
            ).fetchone()
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM variants WHERE variant_id = 'variant_husk'"
            ).fetchone()
        )

    @patch("utils.menu_utils._clear_impacted_models", return_value=None)
    def test_merge_pull_sweeps_order_row_old_owner_husk(self, _mock_models) -> None:
        # Leak-A regression: the mapping row for the assignment key already
        # points at the target, but the order rows still reference a third
        # item ('item_husk_owner'). Applying the assignment moves the order
        # rows; the touched set only ever sees the mapping row's owners, so
        # without the sweep 'item_husk_owner' would linger forever.
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES ('item_husk_owner', 'Divergent Owner', 'Ice Cream', 1)"
        )
        self.conn.execute(
            "UPDATE order_items SET menu_item_id = 'item_husk_owner' WHERE order_item_id = 1"
        )
        # Mapping row for key '1' points at item_target already (diverged).
        self.conn.execute(
            "UPDATE menu_item_variants SET menu_item_id = 'item_target' WHERE order_item_id = '1'"
        )
        self.conn.commit()

        remote_events = [
            {
                "remote_event_id": "remote-leak-a-1",
                "schema_version": 1,
                "event_type": "menu_merge.applied",
                "occurred_at": "2026-04-14T10:00:00Z",
                "source_item": {
                    "menu_item_id": "item_source",
                    "name": "Iced Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "target_item": {
                    "menu_item_id": "item_target",
                    "name": "Cold Coffee",
                    "type": "Beverage",
                    "is_verified": True,
                },
                "merge_payload": {
                    "kind": "basic_merge_v1",
                    "assignments": [
                        {
                            "order_item_id": "1",
                            "menu_item_id": "item_target",
                            "variant_id": "variant_1_piece",
                            "is_verified": 1,
                        }
                    ],
                },
                "server_seq": 10,
            }
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": remote_events, "next_cursor": "cursor-4"}

        with patch("requests.get", return_value=mock_response):
            result = pull_and_apply_menu_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/menu-merges",
            )

        self.assertIsNone(result["error"])
        self.assertEqual(result["merge_events_applied"], 1)
        # Order row moved to the target...
        self.assertEqual(
            self.conn.execute(
                "SELECT menu_item_id FROM order_items WHERE order_item_id = 1"
            ).fetchone()["menu_item_id"],
            "item_target",
        )
        # ...and its previous owner (never in the touched set) was swept.
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM menu_items WHERE menu_item_id = 'item_husk_owner'"
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
