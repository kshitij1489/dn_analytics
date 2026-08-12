"""Analytics-side ownership tests for the additive revision-1.8 alias wire."""

from __future__ import annotations

import base64
import copy
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from services.clustering_service import OrderItemCluster
from src.api.routers.menu import get_global_menu_catalog
from src.core.analytics_stream_contract import parse_allowed_restaurants
from src.core.db.connection import apply_analytics_schema
from src.core.global_menu_schema import (
    GLOBAL_MENU_GROUP_POS_ALIASES_CAPABILITY,
    GLOBAL_MENU_MODE,
    GlobalMenuCapabilityStatus,
)
from src.core.global_menu_sync import (
    apply_global_assignment_rows,
    apply_global_menu_payload_page,
)
from src.core.queries.global_menu_diagnostics import fetch_global_menu_diagnostics


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "1"
    / "global_menu_alias_fixtures.json"
)


class GlobalMenuAliasContractV18Tests(unittest.TestCase):
    """Pin every new fixture to the client behavior that will consume it."""

    #: Applied through real client code by a test in this class.
    CLIENT_PINNED_FIXTURES = {
        "allowed_restaurants_group_pos_alias_capability",
        "menu_bootstrap_group_pos_alias_observation_request",
        "menu_bootstrap_group_pos_alias_observation_response",
        "global_menu_alias_resolution_queue_page",
        "global_menu_alias_decision_preview_approved",
        "global_menu_alias_decision_commit_approved",
        "global_menu_alias_decision_commit_idempotent_replay",
        "global_menu_alias_decision_status_applied",
        "error_global_menu_alias_observation_conflict",
        "global_menu_alias_decision_preview_stale_observation",
        "error_global_menu_alias_revision_conflict",
        "error_global_menu_alias_preview_conflict",
        "error_global_menu_alias_mutation_conflict",
        "error_global_menu_alias_mutation_not_found",
        "error_global_menu_alias_mutation_invalid",
        "error_global_menu_alias_invalid_cursor",
        "global_menu_alias_reconciliation_plan",
        "global_menu_alias_reconciliation_plan_not_ready",
        "global_menu_alias_reconciliation_status",
        "global_menu_alias_unknown_locator_quarantined",
        "global_menu_alias_reconciled_event_page",
        "global_menu_alias_snapshot_rules_many_aliases_one_pair",
        "global_menu_shadow_canonical_catalog_for_alias_resolution",
    }

    #: Kept as a forcing gate for future contract additions. Revision-1.8 has
    #: executable observation, transport, projection, and diagnostics owners.
    AWAITING_CLIENT_PIN_FIXTURES = set()

    EXPECTED_FIXTURES = CLIENT_PINNED_FIXTURES | AWAITING_CLIENT_PIN_FIXTURES

    ALIAS_CAPABILITY = "global_menu_group_pos_aliases_v1"

    @classmethod
    def setUpClass(cls) -> None:
        document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        if document["contract_revision"] != "1.8":
            raise AssertionError("Expected revision-1.8 alias fixtures")
        cls.fixtures = {row["name"]: row for row in document["fixtures"]}

    def _payload(self, name: str):
        fixture = self.fixtures[name]
        referenced = fixture.get("payload_fixture")
        if referenced:
            return self._payload(referenced)
        return fixture["payload"]

    def _request(self, name: str):
        fixture = self.fixtures[name]
        referenced = fixture.get("request_fixture")
        if referenced:
            return self._request(referenced)
        return fixture["request"]

    def _capability(
        self,
        *capabilities: str,
        catalog_revision: int = 5,
        bootstrap_status: str = "complete",
    ):
        return GlobalMenuCapabilityStatus(
            mode=GLOBAL_MENU_MODE,
            restaurant_id="kmov2tngwh",
            menu_group_id="group-1",
            schema_version=1,
            server_advertised=True,
            authorized=True,
            selected=True,
            reason="fixture",
            catalog_revision=catalog_revision,
            bootstrap_status=bootstrap_status,
            capabilities=capabilities,
        )

    def _alias_capability(self):
        """What an alias-policy member advertises after activation."""
        return self._capability(
            "global_menu_v1",
            "global_menu_resolution_v1",
            GLOBAL_MENU_GROUP_POS_ALIASES_CAPABILITY,
            catalog_revision=6,
        )

    def _seed_alias_profile(self, *, existing_locator=None):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        apply_global_menu_payload_page(
            conn,
            self._payload("global_menu_shadow_canonical_catalog_for_alias_resolution"),
            stream="snapshot",
            capability=self._capability(
                "global_menu_v1",
                "global_menu_resolution_v1",
                self.ALIAS_CAPABILITY,
                catalog_revision=5,
            ),
        )
        if existing_locator:
            local_item_id = conn.execute(
                """
                SELECT local_menu_item_id FROM menu_item_global_links
                WHERE global_menu_item_id='1795ed65318544a9907caab6202b9d42'
                  AND is_projection_owner=1
                """
            ).fetchone()[0]
            local_variant_id = f"legacy-variant-{existing_locator}"
            conn.execute(
                "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES (?, ?, 1)",
                (local_variant_id, f"Legacy {existing_locator}"),
            )
            conn.execute(
                """
                INSERT INTO menu_item_variants (
                    order_item_id, menu_item_id, variant_id, price,
                    is_active, addon_eligible, delivery_eligible, is_verified
                ) VALUES (?, ?, ?, 111.00, 0, 1, 0, 0)
                """,
                (existing_locator, local_item_id, local_variant_id),
            )
        apply_global_menu_payload_page(
            conn,
            self._payload("global_menu_alias_snapshot_rules_many_aliases_one_pair"),
            stream="snapshot",
            capability=self._alias_capability(),
        )
        return conn

    def test_every_new_fixture_has_a_client_owner(self) -> None:
        self.assertEqual(set(self.fixtures), self.EXPECTED_FIXTURES)
        for name, fixture in self.fixtures.items():
            with self.subTest(fixture=name):
                method, path = fixture["endpoint"].split(" ", 1)
                self.assertIn(method, {"GET", "POST"})
                self.assertTrue(
                    path.startswith(("/analytics/", "/desktop-analytics-sync/"))
                )

    def test_allowed_restaurants_parser_preserves_alias_capability(self) -> None:
        payload = self._payload(
            "allowed_restaurants_group_pos_alias_capability"
        )
        restaurants = parse_allowed_restaurants(payload)
        self.assertEqual({row["menu_group_id"] for row in restaurants}, {"group-1"})
        for restaurant in restaurants:
            capabilities = set(restaurant["menu_capabilities"])
            self.assertIn("global_menu_group_pos_aliases_v1", capabilities)
            self.assertNotIn("global_menu_shared_pos_catalog_v1", capabilities)

    def test_observation_contract_preserves_raw_itemcode(self) -> None:
        request = self._request(
            "menu_bootstrap_group_pos_alias_observation_request"
        )
        row = request["group_pos_alias_observation"][0]
        self.assertEqual(row["locator_value"], "1312789339")
        self.assertEqual(row["itemcode"], "Tiramisu")
        self.assertIsNone(row["variant_id"])
        self.assertNotIn("shared_pos_catalog", request)
        acknowledgement = self._payload(
            "menu_bootstrap_group_pos_alias_observation_response"
        )
        self.assertTrue(acknowledgement["group_pos_alias_observation_updated"])

    def test_queue_cursor_candidate_and_quarantine_are_machine_actionable(self) -> None:
        fixture = self.fixtures["global_menu_alias_resolution_queue_page"]
        queue = fixture["payload"]
        cursor = queue["next_cursor"]
        decoded = json.loads(
            base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        )
        # ``after`` is exclusive, so the client must resend the key of the last
        # row it saw. A cursor naming the next row would skip that row.
        last_row = queue["rows"][-1]
        self.assertEqual(
            decoded,
            {
                "v": 1,
                "g": queue["menu_group_id"],
                "locator_type": last_row["locator_type"],
                "locator_value": last_row["locator_value"],
            },
        )
        # The queue serves peer restaurants' raw POS identifiers, so the client
        # must send the editor credential, not the read token alone.
        self.assertIn("X-Global-Menu-Key", fixture["request_headers"])
        self.assertEqual(queue["rows"][0]["candidate"]["reason"], "unique_itemcode")
        quarantine = self._payload("global_menu_alias_unknown_locator_quarantined")
        row = quarantine["rows"][0]
        self.assertEqual(row["resolution_state"], "quarantined")
        self.assertIsNone(row["candidate"])
        self.assertEqual(row["conflicts"][0]["code"], "unknown_group_pos_alias")

    def test_decision_lifecycle_and_stale_error_are_frozen(self) -> None:
        preview = self.fixtures["global_menu_alias_decision_preview_approved"]
        self.assertTrue(preview["payload"]["revision_current"])
        self.assertTrue(preview["payload"]["observation_current"])
        self.assertTrue(preview["payload"]["commit_allowed"])
        self.assertEqual(len(preview["payload"]["preview_digest"]), 64)

        committed = self._payload("global_menu_alias_decision_commit_approved")
        replayed = self._payload(
            "global_menu_alias_decision_commit_idempotent_replay"
        )
        status = self._payload("global_menu_alias_decision_status_applied")
        self.assertEqual({**committed, "idempotent_replay": True}, replayed)
        self.assertEqual(status, replayed)
        self.assertEqual(
            self._request("global_menu_alias_decision_commit_idempotent_replay"),
            self._request("global_menu_alias_decision_commit_approved"),
        )
        stale = self._payload("error_global_menu_alias_observation_conflict")
        self.assertEqual(stale["code"], "global_menu_alias_observation_conflict")
        self.assertEqual(stale["recommended_action"], "refresh_alias_resolution")
        stale_preview = self._payload(
            "global_menu_alias_decision_preview_stale_observation"
        )
        self.assertFalse(stale_preview["observation_current"])
        self.assertFalse(stale_preview["commit_allowed"])
        expected_errors = {
            "error_global_menu_alias_revision_conflict": (
                "global_menu_alias_revision_conflict",
                "refresh_alias_resolution",
            ),
            "error_global_menu_alias_preview_conflict": (
                "global_menu_alias_preview_conflict",
                "refresh_alias_resolution",
            ),
            "error_global_menu_alias_mutation_conflict": (
                "global_menu_alias_mutation_conflict",
                "use_new_mutation_id",
            ),
        }
        for fixture_name, (code, action) in expected_errors.items():
            with self.subTest(fixture=fixture_name):
                error = self._payload(fixture_name)
                self.assertEqual(error["code"], code)
                self.assertEqual(error["recommended_action"], action)
        # A timed-out commit is reconciled by mutation id, so the client must
        # keep "never seen" and "not a uuid" apart: the first means retry is
        # safe, the second means the id the client built is wrong.
        not_found = self.fixtures["error_global_menu_alias_mutation_not_found"]
        invalid = self.fixtures["error_global_menu_alias_mutation_invalid"]
        self.assertEqual(
            not_found["payload"]["code"], "global_menu_alias_mutation_not_found"
        )
        self.assertEqual(not_found["http_status"], 404)
        self.assertEqual(
            invalid["payload"]["code"], "global_menu_alias_mutation_invalid"
        )
        self.assertEqual(invalid["http_status"], 400)
        self.assertEqual(
            self._payload("error_global_menu_alias_invalid_cursor")["code"],
            "invalid_page_parameter",
        )

    def test_plan_and_status_pin_all_execute_gates(self) -> None:
        plan = self._payload("global_menu_alias_reconciliation_plan")
        required_plan_fields = {
            "observation_digests",
            "catalog_digest",
            "redirect_digest",
            "affected_assignments",
            "immutable_facts",
            "unresolved_count",
            "stale_count",
            "blocking_count",
            "execution_ready",
            "plan_digest",
        }
        self.assertTrue(required_plan_fields.issubset(plan))
        self.assertEqual(
            (plan["unresolved_count"], plan["stale_count"], plan["blocking_count"]),
            (0, 0, 0),
        )
        status = self._payload("global_menu_alias_reconciliation_status")
        self.assertTrue(status["canonical_catalog_complete"])
        self.assertTrue(status["alias_decision_coverage_complete"])
        self.assertTrue(status["verified_coverage_complete"])
        self.assertEqual(status["initial_reconciliation"]["status"], "applied")

    def test_a_not_ready_plan_carries_the_same_fields_as_a_ready_one(self) -> None:
        """
        The client renders one plan view for both states. A refusal that dropped
        fields would force it to branch on absence, and ``plan_digest`` would
        cover two different shapes.
        """
        ready = self._payload("global_menu_alias_reconciliation_plan")
        not_ready = self._payload("global_menu_alias_reconciliation_plan_not_ready")
        self.assertEqual(set(ready), set(not_ready))
        self.assertTrue(ready["execution_ready"])
        self.assertFalse(not_ready["execution_ready"])
        self.assertNotEqual(ready["plan_digest"], not_ready["plan_digest"])
        for conflict in not_ready["conflicts"]:
            with self.subTest(code=conflict["code"]):
                self.assertIsInstance(conflict["code"], str)
                self.assertIsInstance(conflict["message"], str)
        self.assertGreater(
            not_ready["unresolved_count"] + not_ready["blocking_count"], 0
        )

    def test_many_outlet_aliases_render_as_one_canonical_menu_row(self) -> None:
        """
        The client must deduplicate by canonical pair, not by POS locator.
        Two aliases for one product are one menu row, not two.
        """
        payload = self._payload(
            "global_menu_alias_snapshot_rules_many_aliases_one_pair"
        )
        pos_rules = [
            row for row in payload["rows"] if row["locator_type"] == "pos_item"
        ]
        self.assertEqual(len(pos_rules), 2)
        self.assertEqual(
            {row["locator_value"] for row in pos_rules},
            {"1283777806", "1312789339"},
        )
        canonical_pairs = {
            (row["global_item_id"], row["global_variant_id"]) for row in pos_rules
        }
        self.assertEqual(len(canonical_pairs), 1)
        for row in payload["rows"]:
            with self.subTest(locator=row["locator_value"]):
                self.assertEqual(row["rule_scope"], "group")
                self.assertEqual(row["restaurant_id"], "")

    def test_group_scoped_pos_rules_apply_under_the_alias_policy(
        self,
    ) -> None:
        """Alias policy accepts and materializes many-to-one group POS rules."""
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        capability = self._alias_capability()
        # Seed the canonical target first: rules are validated against an
        # existing global item, so an empty catalog would fail earlier and for
        # an unrelated reason. The seed page is revision 5 and a page behind the
        # client's known revision is skipped, so it is applied at its own.
        seeded = apply_global_menu_payload_page(
            conn,
            self._payload(
                "global_menu_shadow_canonical_catalog_for_alias_resolution"
            ),
            stream="snapshot",
            capability=self._capability(
                "global_menu_v1",
                "global_menu_resolution_v1",
                self.ALIAS_CAPABILITY,
                catalog_revision=5,
            ),
        )
        self.assertEqual(seeded["rows_applied"], 1)
        payload = self._payload(
            "global_menu_alias_snapshot_rules_many_aliases_one_pair"
        )
        applied = apply_global_menu_payload_page(
            conn,
            payload,
            stream="snapshot",
            capability=capability,
        )
        self.assertEqual(applied["rows_materialized"], 2)
        rows = conn.execute(
            """
            SELECT order_item_id, menu_item_id, variant_id, printf('%.2f', price)
            FROM menu_item_variants
            WHERE order_item_id IN ('1283777806', '1312789339')
            ORDER BY order_item_id
            """
        ).fetchall()
        self.assertEqual([row[0] for row in rows], ["1283777806", "1312789339"])
        self.assertEqual(len({row[1] for row in rows}), 1)
        self.assertEqual(len({row[2] for row in rows}), 1)
        self.assertEqual({row[3] for row in rows}, {"290.00"})

    def test_two_alias_profiles_converge_without_sharing_assignment_state(self) -> None:
        first = self._seed_alias_profile(existing_locator="1283777806")
        second = self._seed_alias_profile(existing_locator="1312789339")
        self.addCleanup(first.close)
        self.addCleanup(second.close)

        for conn, observed_locator in (
            (first, "1283777806"),
            (second, "1312789339"),
        ):
            rows = conn.execute(
                """
                SELECT order_item_id, menu_item_id, variant_id, printf('%.2f', price)
                FROM menu_item_variants
                WHERE order_item_id IN ('1283777806', '1312789339')
                ORDER BY order_item_id
                """
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({row[1] for row in rows}), 1)
            self.assertEqual(len({row[2] for row in rows}), 1)
            self.assertEqual({row[3] for row in rows}, {"290.00"})
            local_flags = conn.execute(
                """
                SELECT is_active, addon_eligible, delivery_eligible, is_verified
                FROM menu_item_variants WHERE order_item_id=?
                """,
                (observed_locator,),
            ).fetchone()
            self.assertEqual(tuple(local_flags), (0, 1, 0, 0))

        for conn, locator in (
            (first, "1283777806"),
            (second, "1312789339"),
        ):
            local = conn.execute(
                """
                SELECT menu_item_id, variant_id FROM menu_item_variants
                WHERE order_item_id=?
                """,
                (locator,),
            ).fetchone()
            apply_global_assignment_rows(
                conn,
                [
                    {
                        "order_item_id": locator,
                        "menu_item_id": local[0],
                        "variant_id": local[1],
                        "global_menu_item_id": "1795ed65318544a9907caab6202b9d42",
                        "global_variant_id": "",
                        "last_seq": 0,
                        "is_verified": False,
                    }
                ],
                capability=self._alias_capability(),
                server_revision=6,
            )

        self.assertEqual(
            first.execute(
                "SELECT is_verified FROM menu_item_variants WHERE order_item_id='1312789339'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            second.execute(
                "SELECT is_verified FROM menu_item_variants WHERE order_item_id='1283777806'"
            ).fetchone()[0],
            1,
        )
        first_diagnostics = fetch_global_menu_diagnostics(first)
        second_diagnostics = fetch_global_menu_diagnostics(second)
        self.assertEqual(
            first_diagnostics["catalog_digest"], second_diagnostics["catalog_digest"]
        )
        self.assertEqual(
            first_diagnostics["history_digest"], second_diagnostics["history_digest"]
        )

    def test_unknown_alias_quarantines_without_creating_a_local_catalog_row(self) -> None:
        conn = self._seed_alias_profile()
        self.addCleanup(conn.close)
        before = conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0]
        capability = self._alias_capability()
        with patch(
            "services.clustering_service.resolve_global_menu_capability",
            return_value=capability,
        ):
            result = OrderItemCluster(conn).add(
                "Future Seasonal Dessert",
                "future-9988",
                restaurant_id="kmov2tngwh",
            )
        self.assertIsNone(result.menu_item_id)
        self.assertEqual(result.match_method, "unknown-group-pos-alias")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0], before
        )
        quarantine = conn.execute(
            """
            SELECT error_code, stream, payload FROM global_menu_sync_quarantine
            WHERE payload_key='group-pos-alias:pos-item:future-9988'
              AND resolved_at IS NULL
            """
        ).fetchone()
        self.assertEqual(quarantine[0], "unknown_group_pos_alias")
        self.assertEqual(quarantine[1], "group_pos_alias_resolution")
        self.assertEqual(json.loads(quarantine[2])["locator_value"], "future-9988")

    def test_alias_assignment_contradiction_and_dual_policy_fail_closed(self) -> None:
        conn = self._seed_alias_profile()
        self.addCleanup(conn.close)
        with self.assertRaises(Exception) as contradiction:
            apply_global_assignment_rows(
                conn,
                [
                    {
                        "order_item_id": "1283777806",
                        "menu_item_id": "unused",
                        "variant_id": "unused",
                        "global_menu_item_id": "different-global-item",
                        "global_variant_id": "",
                        "last_seq": 0,
                    }
                ],
                capability=self._alias_capability(),
                server_revision=6,
            )
        self.assertEqual(
            getattr(contradiction.exception, "code", None),
            "global_menu_group_pos_alias_assignment_conflict",
        )

        conflict = self._capability(
            "global_menu_v1",
            "global_menu_resolution_v1",
            "global_menu_shared_pos_catalog_v1",
            self.ALIAS_CAPABILITY,
            catalog_revision=6,
        )
        self.assertTrue(conflict.pos_policy_conflict)
        self.assertFalse(conflict.active)
        with self.assertRaises(Exception) as refused:
            apply_global_menu_payload_page(
                conn,
                self._payload("global_menu_alias_snapshot_rules_many_aliases_one_pair"),
                stream="snapshot",
                capability=conflict,
            )
        self.assertEqual(
            getattr(refused.exception, "code", None),
            "global_menu_pos_policy_conflict",
        )

    def test_alias_event_redirects_rule_targets_to_one_active_survivor(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        apply_global_menu_payload_page(
            conn,
            self._payload("global_menu_shadow_canonical_catalog_for_alias_resolution"),
            stream="snapshot",
            capability=self._capability(
                "global_menu_v1",
                "global_menu_resolution_v1",
                self.ALIAS_CAPABILITY,
                catalog_revision=5,
            ),
        )
        event_page = copy.deepcopy(
            self._payload("global_menu_alias_reconciled_event_page")
        )
        source_id = "redirected-tiramisu-source"
        body = event_page["events"][0]["payload"]
        body["items"] = [
            {
                "global_item_id": source_id,
                "canonical_name": "Old Tiramisu",
                "canonical_type": "Dessert",
                "is_verified": True,
                "lifecycle_state": "redirected",
                "updated_at": "2026-08-12T12:30:00+00:00",
            }
        ]
        body["redirects"] = [
            {
                "entity_type": "item",
                "source_global_id": source_id,
                "target_global_id": "1795ed65318544a9907caab6202b9d42",
                "menu_group_revision": 6,
            }
        ]
        for rule in body["mapping_rules"]:
            rule["global_item_id"] = source_id

        apply_global_menu_payload_page(
            conn,
            event_page,
            stream="events",
            capability=self._capability(
                "global_menu_v1",
                "global_menu_resolution_v1",
                self.ALIAS_CAPABILITY,
                catalog_revision=5,
            ),
        )
        stored_targets = {
            row[0]
            for row in conn.execute(
                """
                SELECT target_global_menu_item_id FROM global_menu_mapping_rules
                WHERE locator_kind='pos-item'
                """
            ).fetchall()
        }
        self.assertEqual(
            stored_targets, {"1795ed65318544a9907caab6202b9d42"}
        )
        projected_items = {
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT l.global_menu_item_id
                FROM menu_item_variants mv
                JOIN menu_item_global_links l ON l.local_menu_item_id=mv.menu_item_id
                WHERE mv.order_item_id IN ('1283777806', '1312789339')
                """
            ).fetchall()
        }
        self.assertEqual(
            projected_items, {"1795ed65318544a9907caab6202b9d42"}
        )

    def test_shadow_catalog_route_is_not_gated_on_a_pos_policy(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        shadow = self._capability(
            "global_menu_v1",
            "global_menu_resolution_v1",
            catalog_revision=5,
        )
        apply_global_menu_payload_page(
            conn,
            self._payload("global_menu_shadow_canonical_catalog_for_alias_resolution"),
            stream="snapshot",
            capability=shadow,
        )
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=shadow,
        ):
            catalog = get_global_menu_catalog(conn)
        self.assertEqual(catalog["menu_group_id"], "group-1")
        self.assertEqual(
            [row["canonical_name"] for row in catalog["items"]],
            ["Classic Tiramisu"],
        )

    def test_the_reconciliation_event_is_a_complete_non_identity_delta(self) -> None:
        """
        The client converges from snapshot plus tail and applies an event by its
        payload, never by recognising ``event_type``. The run's event must
        therefore carry a full delta — and prove it mints no canonical identity.
        """
        payload = self._payload("global_menu_alias_reconciled_event_page")
        event = payload["events"][0]
        self.assertEqual(
            event["event_type"], "global_menu.group_pos_alias_reconciled"
        )
        body = event["payload"]
        for empty in ("items", "variants", "redirects"):
            with self.subTest(collection=empty):
                self.assertEqual(body[empty], [])
        self.assertEqual(
            {row["global_item_id"] for row in body["mapping_rules"]},
            {"1795ed65318544a9907caab6202b9d42"},
        )
        tombstoned = body["tombstones"]["mapping_rules"]
        self.assertEqual([row["rule_scope"] for row in tombstoned], ["restaurant"])
        self.assertEqual(tombstoned[0]["restaurant_id"], "kmov2tngwh")
        # A full assignment refresh is required, so the client cannot treat this
        # as a metadata-only tick.
        self.assertTrue(body["assignment_snapshot_required"])
        self.assertEqual(
            body["assignment_restaurants"], ["1c8w7fp500", "kmov2tngwh"]
        )
        self.assertEqual(payload["next_cursor"], event["event_seq"])

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        apply_global_menu_payload_page(
            conn,
            self._payload("global_menu_shadow_canonical_catalog_for_alias_resolution"),
            stream="snapshot",
            capability=self._capability(
                "global_menu_v1",
                "global_menu_resolution_v1",
                self.ALIAS_CAPABILITY,
                catalog_revision=5,
            ),
        )
        applied = apply_global_menu_payload_page(
            conn,
            payload,
            stream="events",
            capability=self._capability(
                "global_menu_v1",
                "global_menu_resolution_v1",
                self.ALIAS_CAPABILITY,
                catalog_revision=5,
            ),
        )
        self.assertEqual(applied["rows_applied"], 1)
        self.assertEqual(applied["rows_materialized"], 2)
        stored = conn.execute(
            """
            SELECT event_type, catalog_revision FROM global_menu_events
            WHERE mutation_id='3f0d1b1e-6a4f-4f4d-9f5a-2b8c1d0e7a91'
            """
        ).fetchone()
        self.assertEqual(
            tuple(stored), ("global_menu.group_pos_alias_reconciled", 6)
        )

    def test_shape_only_fixtures_must_be_pinned_once_the_client_lands(self) -> None:
        """
        Phase C capability support must own every Phase C fixture in real code.

        Observation, decision and reconciliation-read fixtures remain explicit
        Phase B/D/F work and have their own landing gates in those modules.
        """
        from src.core import global_menu_schema

        phase_c_shipped = self.ALIAS_CAPABILITY in {
            value
            for name, value in vars(global_menu_schema).items()
            if name.endswith("_CAPABILITY") and isinstance(value, str)
        }
        if phase_c_shipped:
            self.assertTrue(
                {
                    "allowed_restaurants_group_pos_alias_capability",
                    "global_menu_alias_reconciled_event_page",
                    "global_menu_alias_snapshot_rules_many_aliases_one_pair",
                    "global_menu_shadow_canonical_catalog_for_alias_resolution",
                }.issubset(self.CLIENT_PINNED_FIXTURES)
            )
        self.assertEqual(
            self.CLIENT_PINNED_FIXTURES & self.AWAITING_CLIENT_PIN_FIXTURES, set()
        )

    def test_shadow_canonical_catalog_applies_before_alias_readiness(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        fixture = self._payload(
            "global_menu_shadow_canonical_catalog_for_alias_resolution"
        )
        result = apply_global_menu_payload_page(
            conn,
            fixture,
            stream="snapshot",
            capability=GlobalMenuCapabilityStatus(
                mode=GLOBAL_MENU_MODE,
                restaurant_id="kmov2tngwh",
                menu_group_id="group-1",
                schema_version=1,
                server_advertised=True,
                authorized=True,
                selected=True,
                reason="fixture",
                catalog_revision=5,
                capabilities=(
                    "global_menu_v1",
                    "global_menu_resolution_v1",
                ),
            ),
        )
        self.assertGreaterEqual(result["rows_applied"], 1)
        stored = conn.execute(
            "SELECT canonical_name FROM global_menu_items WHERE global_menu_item_id=?",
            ("1795ed65318544a9907caab6202b9d42",),
        ).fetchone()
        self.assertIsNotNone(stored)
        self.assertEqual(stored[0], "Classic Tiramisu")


if __name__ == "__main__":
    unittest.main()
