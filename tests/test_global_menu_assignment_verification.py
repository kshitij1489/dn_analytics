from __future__ import annotations

import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.core.db.connection import apply_analytics_schema
from src.core.menu_mutation_commit import (
    CommitResult,
    MUTATION_TYPE_VERIFY,
    NETWORK_ERROR_MESSAGE,
    apply_accepted,
)
from src.core.menu_mapping_verification_sync import (
    get_menu_mapping_verification_pull_cursor,
)
from src.core.queries.menu_queries import fetch_unverified_items
from src.core.sync_identity import set_menu_state_revision
from utils.menu_utils import verify_menu_mapping_assignments


class GlobalMenuAssignmentVerificationTests(unittest.TestCase):
    ASSIGNMENT_ID = "61458679"
    ITEM_ID = "35032c91-0c1e-5146-811e-19d7649690fb"
    VARIANT_ID = "07760a66-a729-57a2-a8f7-a50822d756d2"
    GLOBAL_ITEM_ID = "global-banoffee"
    GLOBAL_VARIANT_ID = "global-banoffee-scoop"
    MUTATION_ID = "8ebc4a40-1022-4aa5-b8dc-b1ff120bc72f"

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(self.conn)
        get_menu_mapping_verification_pull_cursor(self.conn)
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES (?, 'Banoffee Ice Cream', 'Dessert', 0)",
            (self.ITEM_ID,),
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES (?, 'Regular', 1)",
            (self.VARIANT_ID,),
        )
        self.conn.execute(
            """
            INSERT INTO global_menu_items (
                global_menu_item_id, menu_group_id, canonical_name,
                canonical_type, is_verified, lifecycle_state, server_revision
            ) VALUES (?, 'group-1', 'Banoffee Ice Cream', 'Dessert', 1, 'active', 7)
            """,
            (self.GLOBAL_ITEM_ID,),
        )
        self.conn.execute(
            """
            INSERT INTO global_variants (
                global_variant_id, menu_group_id, canonical_name,
                is_verified, lifecycle_state, server_revision
            ) VALUES (?, 'group-1', 'Regular', 1, 'active', 7)
            """,
            (self.GLOBAL_VARIANT_ID,),
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_global_links (
                local_menu_item_id, global_menu_item_id, provenance,
                server_revision, is_projection_owner
            ) VALUES (?, ?, 'server-link', 7, 0)
            """,
            (self.ITEM_ID, self.GLOBAL_ITEM_ID),
        )
        self.conn.execute(
            """
            INSERT INTO variant_global_links (
                local_variant_id, global_variant_id, provenance,
                server_revision, is_projection_owner
            ) VALUES (?, ?, 'server-link', 7, 0)
            """,
            (self.VARIANT_ID, self.GLOBAL_VARIANT_ID),
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, verification_seq, pending_local
            ) VALUES (?, ?, ?, 0, 41, 12, 0)
            """,
            (self.ASSIGNMENT_ID, self.ITEM_ID, self.VARIANT_ID),
        )
        self.conn.execute(
            """
            INSERT INTO orders (
                order_id, petpooja_order_id, stream_id, event_id, occurred_at,
                created_on, order_type, order_from, order_status
            ) VALUES (1, 1, 1, 'event-1', '2026-08-10T10:00:00Z',
                      '2026-08-10 15:30:00', 'Delivery', 'POS', 'Success')
            """
        )
        self.conn.execute(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id,
                petpooja_itemid, name_raw, quantity, unit_price, total_price
            ) VALUES (1, 1, ?, ?, 999, 'Parent item', 1, 100, 100)
            """,
            (self.ITEM_ID, self.VARIANT_ID),
        )
        self.conn.execute(
            """
            INSERT INTO order_item_addons (
                order_item_addon_id, order_item_id, menu_item_id, variant_id,
                petpooja_addonid, name_raw, quantity, price
            ) VALUES (1, 1, ?, ?, ?, 'Banoffee Ice Cream', 1, 40)
            """,
            (self.ITEM_ID, self.VARIANT_ID, self.ASSIGNMENT_ID),
        )
        set_menu_state_revision(self.conn, 9)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _accept(self, captured: list):
        def commit(conn, plan):
            captured.append(plan)
            accepted_events = [
                {
                    "remote_event_id": event["remote_event_id"],
                    "server_seq": 88,
                    "server_ingested_at": "2026-08-12T10:00:00Z",
                }
                for event in plan.verification_events
            ]
            apply_accepted(
                conn,
                {
                    "status": "accepted",
                    "mutation_id": plan.mutation_id,
                    "menu_revision": 10,
                    "accepted_events": accepted_events,
                    "assignment_rows": [],
                    "catalog_delta": plan.catalog_delta,
                    "verification_cursor": "verification-88",
                },
                plan,
            )
            return CommitResult(status="ok")

        return commit

    def _verify(self):
        return verify_menu_mapping_assignments(
            self.conn,
            [self.ASSIGNMENT_ID],
            expected_global_menu_item_id=self.GLOBAL_ITEM_ID,
            expected_global_variant_id=self.GLOBAL_VARIANT_ID,
            mutation_id=self.MUTATION_ID,
        )

    def _remove_global_projection(
        self, *, remove_item: bool, remove_variant: bool
    ) -> None:
        if remove_variant:
            self.conn.execute(
                "DELETE FROM variant_global_links WHERE local_variant_id=?",
                (self.VARIANT_ID,),
            )
            self.conn.execute(
                "DELETE FROM global_variants WHERE global_variant_id=?",
                (self.GLOBAL_VARIANT_ID,),
            )
        if remove_item:
            self.conn.execute(
                "DELETE FROM menu_item_global_links WHERE local_menu_item_id=?",
                (self.ITEM_ID,),
            )
            self.conn.execute(
                "DELETE FROM global_menu_items WHERE global_menu_item_id=?",
                (self.GLOBAL_ITEM_ID,),
            )
        self.conn.commit()

    def _apply_locator_mapping_projection(
        self, *, create_item: bool, create_variant: bool
    ) -> None:
        if create_item:
            self.conn.execute(
                """
                INSERT INTO global_menu_items (
                    global_menu_item_id, menu_group_id, canonical_name,
                    canonical_type, is_verified, lifecycle_state, server_revision
                ) VALUES (?, 'group-1', 'Banoffee Ice Cream', 'Dessert', 1, 'active', 8)
                """,
                (self.GLOBAL_ITEM_ID,),
            )
            self.conn.execute(
                """
                INSERT INTO menu_item_global_links (
                    local_menu_item_id, global_menu_item_id, provenance,
                    server_revision, is_projection_owner
                ) VALUES (?, ?, 'restaurant-pos', 8, 0)
                """,
                (self.ITEM_ID, self.GLOBAL_ITEM_ID),
            )
        if create_variant:
            self.conn.execute(
                """
                INSERT INTO global_variants (
                    global_variant_id, menu_group_id, canonical_name,
                    is_verified, lifecycle_state, server_revision
                ) VALUES (?, 'group-1', 'Regular', 1, 'active', 8)
                """,
                (self.GLOBAL_VARIANT_ID,),
            )
            self.conn.execute(
                """
                INSERT INTO variant_global_links (
                    local_variant_id, global_variant_id, provenance,
                    server_revision, is_projection_owner
                ) VALUES (?, ?, 'restaurant-pos', 8, 0)
                """,
                (self.VARIANT_ID, self.GLOBAL_VARIANT_ID),
            )
        self.conn.commit()

    def _assert_verified_identity_preserved(self) -> None:
        row = self.conn.execute(
            """
            SELECT mv.is_verified, gil.global_menu_item_id, gvl.global_variant_id
            FROM menu_item_variants mv
            JOIN menu_item_global_links gil
                ON gil.local_menu_item_id=mv.menu_item_id
            JOIN variant_global_links gvl
                ON gvl.local_variant_id=mv.variant_id
            WHERE mv.order_item_id=?
            """,
            (self.ASSIGNMENT_ID,),
        ).fetchone()
        self.assertEqual(
            tuple(row),
            (1, self.GLOBAL_ITEM_ID, self.GLOBAL_VARIANT_ID),
        )

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_addon_assignment_uses_verification_event_and_disappears_after_refresh(
        self, _cloud_config
    ) -> None:
        before = fetch_unverified_items(
            self.conn, include_global_identity_gaps=True
        )
        self.assertEqual(len(before), 1)
        self.assertEqual(before.iloc[0]["resolution_kind"], "unverified_mapping")
        self.assertEqual(
            before.iloc[0]["assignment_order_item_ids"], [self.ASSIGNMENT_ID]
        )

        captured: list = []
        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=self._accept(captured),
        ):
            result = self._verify()

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(len(captured), 1)
        plan = captured[0]
        self.assertEqual(plan.mutation_type, MUTATION_TYPE_VERIFY)
        self.assertIsNone(plan.event)
        self.assertEqual(plan.order_item_ids, [self.ASSIGNMENT_ID])
        self.assertEqual(
            plan.verification_events[0]["order_item_id"], self.ASSIGNMENT_ID
        )
        self.assertNotEqual(plan.mutation_type, "global_locator.map")

        row = self.conn.execute(
            """
            SELECT mv.is_verified, gil.global_menu_item_id, gvl.global_variant_id
            FROM menu_item_variants mv
            JOIN menu_item_global_links gil ON gil.local_menu_item_id=mv.menu_item_id
            JOIN variant_global_links gvl ON gvl.local_variant_id=mv.variant_id
            WHERE mv.order_item_id=?
            """,
            (self.ASSIGNMENT_ID,),
        ).fetchone()
        self.assertEqual(int(row["is_verified"]), 1)
        self.assertEqual(row["global_menu_item_id"], self.GLOBAL_ITEM_ID)
        self.assertEqual(row["global_variant_id"], self.GLOBAL_VARIANT_ID)
        self.assertTrue(
            fetch_unverified_items(
                self.conn, include_global_identity_gaps=True
            ).empty
        )

        with patch("src.core.menu_mutation_commit.commit_mutation") as duplicate:
            second = self._verify()
        self.assertEqual(second["status"], "success")
        self.assertTrue(second["already_verified"])
        duplicate.assert_not_called()

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_missing_item_and_variant_projection_maps_then_verifies(
        self, _cloud_config
    ) -> None:
        self._remove_global_projection(remove_item=True, remove_variant=True)
        phases = []
        self.assertEqual(
            fetch_unverified_items(
                self.conn, include_global_identity_gaps=True
            ).iloc[0]["assignment_order_item_ids"],
            [self.ASSIGNMENT_ID],
        )

        phases.extend(["global_item.create", "global_variant.create"])
        self._apply_locator_mapping_projection(
            create_item=True, create_variant=True
        )
        phases.append("global_locator.map")
        captured: list = []
        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=self._accept(captured),
        ):
            result = self._verify()
        phases.append(captured[0].mutation_type)

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(
            phases,
            [
                "global_item.create",
                "global_variant.create",
                "global_locator.map",
                MUTATION_TYPE_VERIFY,
            ],
        )
        self._assert_verified_identity_preserved()

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_existing_item_missing_variant_maps_variant_then_verifies(
        self, _cloud_config
    ) -> None:
        self._remove_global_projection(remove_item=False, remove_variant=True)
        item_link_before = self.conn.execute(
            "SELECT global_menu_item_id FROM menu_item_global_links "
            "WHERE local_menu_item_id=?",
            (self.ITEM_ID,),
        ).fetchone()[0]

        self._apply_locator_mapping_projection(
            create_item=False, create_variant=True
        )
        captured: list = []
        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=self._accept(captured),
        ):
            result = self._verify()

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].mutation_type, MUTATION_TYPE_VERIFY)
        self.assertEqual(
            self.conn.execute(
                "SELECT global_menu_item_id FROM menu_item_global_links "
                "WHERE local_menu_item_id=?",
                (self.ITEM_ID,),
            ).fetchone()[0],
            item_link_before,
        )
        self._assert_verified_identity_preserved()

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_projection_owner_move_before_verification_uses_global_identity(
        self, _cloud_config
    ) -> None:
        target_item_id = "canonical-local-item"
        target_variant_id = "canonical-local-variant"
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES (?, 'Banoffee Canonical', 'Dessert', 1)",
            (target_item_id,),
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES (?, 'Regular Canonical', 1)",
            (target_variant_id,),
        )
        self.conn.execute(
            "INSERT INTO menu_item_global_links "
            "(local_menu_item_id, global_menu_item_id, provenance, "
            "server_revision, is_projection_owner) "
            "VALUES (?, ?, 'projection', 8, 1)",
            (target_item_id, self.GLOBAL_ITEM_ID),
        )
        self.conn.execute(
            "INSERT INTO variant_global_links "
            "(local_variant_id, global_variant_id, provenance, "
            "server_revision, is_projection_owner) "
            "VALUES (?, ?, 'projection', 8, 1)",
            (target_variant_id, self.GLOBAL_VARIANT_ID),
        )
        self.conn.execute(
            "UPDATE menu_item_variants SET menu_item_id=?, variant_id=? "
            "WHERE order_item_id=?",
            (target_item_id, target_variant_id, self.ASSIGNMENT_ID),
        )
        self.conn.commit()

        captured: list = []
        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=self._accept(captured),
        ):
            result = self._verify()

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(captured[0].mutation_type, MUTATION_TYPE_VERIFY)
        self.assertEqual(
            captured[0].verification_events[0]["menu_item_id"],
            target_item_id,
        )
        self.assertEqual(
            captured[0].verification_events[0]["variant_id"],
            target_variant_id,
        )
        self._assert_verified_identity_preserved()

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_successful_verification_stays_absent_after_database_reopen(
        self, _cloud_config
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = f"{temp_dir}/analytics-test.db"
            disk_conn = sqlite3.connect(database_path)
            disk_conn.row_factory = sqlite3.Row
            self.conn.backup(disk_conn)
            self.conn.close()
            self.conn = disk_conn

            captured: list = []
            with patch(
                "src.core.menu_mutation_commit.commit_mutation",
                side_effect=self._accept(captured),
            ):
                result = self._verify()
            self.assertEqual(result["status"], "success", result)
            self.assertTrue(
                fetch_unverified_items(
                    self.conn, include_global_identity_gaps=True
                ).empty
            )

            self.conn.close()
            self.conn = sqlite3.connect(database_path)
            self.conn.row_factory = sqlite3.Row
            self.assertTrue(
                fetch_unverified_items(
                    self.conn, include_global_identity_gaps=True
                ).empty
            )
            self._assert_verified_identity_preserved()

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_retry_reuses_mutation_and_verification_event_ids(
        self, _cloud_config
    ) -> None:
        captured: list = []

        def fail(_conn, plan):
            captured.append(plan)
            return CommitResult(status="error", message=NETWORK_ERROR_MESSAGE)

        with patch(
            "src.core.menu_mutation_commit.commit_mutation", side_effect=fail
        ):
            first = self._verify()
            second = self._verify()

        self.assertEqual(first["status"], "error")
        self.assertEqual(second["status"], "error")
        self.assertEqual(first["message"], NETWORK_ERROR_MESSAGE)
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0].mutation_id, captured[1].mutation_id)
        self.assertEqual(
            captured[0].verification_events[0]["remote_event_id"],
            captured[1].verification_events[0]["remote_event_id"],
        )
        self.assertEqual(
            int(
                self.conn.execute(
                    "SELECT is_verified FROM menu_item_variants WHERE order_item_id=?",
                    (self.ASSIGNMENT_ID,),
                ).fetchone()[0]
            ),
            0,
        )

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_central_rejection_leaves_resolution_unverified(
        self, _cloud_config
    ) -> None:
        rejection = CommitResult(
            status="conflict",
            message="stale assignment",
            conflict={
                "current_menu_revision": 10,
                "conflicting_events": [
                    {"order_item_ids": [self.ASSIGNMENT_ID]}
                ],
            },
        )
        with patch(
            "src.core.menu_mutation_commit.commit_mutation",
            return_value=rejection,
        ):
            result = self._verify()

        self.assertEqual(result["status"], "conflict")
        self.assertIn("another installation", result["message"])
        row = self.conn.execute(
            "SELECT is_verified FROM menu_item_variants WHERE order_item_id=?",
            (self.ASSIGNMENT_ID,),
        ).fetchone()
        self.assertEqual(int(row[0]), 0)
        self.assertEqual(
            len(fetch_unverified_items(self.conn, include_global_identity_gaps=True)),
            1,
        )

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_changed_assignment_is_rejected_without_a_commit(
        self, _cloud_config
    ) -> None:
        with patch("src.core.menu_mutation_commit.commit_mutation") as commit:
            result = verify_menu_mapping_assignments(
                self.conn,
                [self.ASSIGNMENT_ID],
                expected_global_menu_item_id="stale-global-item",
                expected_global_variant_id=self.GLOBAL_VARIANT_ID,
                mutation_id=self.MUTATION_ID,
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("changed", result["message"])
        commit.assert_not_called()
        self.assertEqual(
            int(
                self.conn.execute(
                    "SELECT is_verified FROM menu_item_variants WHERE order_item_id=?",
                    (self.ASSIGNMENT_ID,),
                ).fetchone()[0]
            ),
            0,
        )

    def test_resolution_assignment_lookup_chunks_large_pair_sets(self) -> None:
        pair_count = 1005
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES (?, ?, 'Dessert', 0)",
            [
                (f"bulk-item-{index}", f"Bulk item {index}")
                for index in range(pair_count)
            ],
        )
        self.conn.executemany(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES (?, ?, 0)",
            [
                (f"bulk-variant-{index}", f"Bulk variant {index}")
                for index in range(pair_count)
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified
            ) VALUES (?, ?, ?, 0)
            """,
            [
                (
                    f"bulk-assignment-{index}",
                    f"bulk-item-{index}",
                    f"bulk-variant-{index}",
                )
                for index in range(pair_count)
            ],
        )

        rows = fetch_unverified_items(
            self.conn, include_global_identity_gaps=True
        )

        bulk_rows = rows[rows["menu_item_id"].str.startswith("bulk-item-")]
        self.assertEqual(len(bulk_rows), pair_count)
        self.assertTrue(
            all(len(ids) == 1 for ids in bulk_rows["assignment_order_item_ids"])
        )
        sample = bulk_rows[bulk_rows["menu_item_id"] == "bulk-item-1000"].iloc[0]
        self.assertEqual(
            sample["assignment_order_item_ids"], ["bulk-assignment-1000"]
        )

    @patch(
        "src.core.menu_mutation_commit.get_cloud_sync_config",
        return_value=("https://cloud.example", "sync-key"),
    )
    def test_shadow_profile_refreshes_missing_menu_revision_before_commit(
        self, _cloud_config
    ) -> None:
        self.conn.execute(
            "DELETE FROM system_config WHERE key='menu_state_revision'"
        )
        self.conn.commit()
        captured: list = []

        def refresh_assignments(conn):
            set_menu_state_revision(conn, 9)
            conn.commit()
            return {"status": "applied", "menu_revision": 9}

        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=Mock(resolution_ready=True),
        ), patch(
            "src.core.global_menu_sync.pull_global_menu_state",
            return_value={"status": "applied"},
        ) as global_pull, patch(
            "src.core.global_menu_sync.pull_global_assignment_snapshot",
            side_effect=refresh_assignments,
        ) as assignment_pull, patch(
            "src.core.menu_mutation_commit.commit_mutation",
            side_effect=self._accept(captured),
        ):
            result = self._verify()

        self.assertEqual(result["status"], "success", result)
        global_pull.assert_called_once_with(self.conn)
        assignment_pull.assert_called_once_with(self.conn)
        self.assertEqual(len(captured), 1)


if __name__ == "__main__":
    unittest.main()
