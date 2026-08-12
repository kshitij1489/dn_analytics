"""Analytics-side ownership tests for the additive revision-1.8 alias wire."""

from __future__ import annotations

import base64
import json
import sqlite3
import unittest
from pathlib import Path

from src.core.analytics_stream_contract import parse_allowed_restaurants
from src.core.db.connection import apply_analytics_schema
from src.core.global_menu_schema import (
    GLOBAL_MENU_MODE,
    GlobalMenuCapabilityStatus,
)
from src.core.global_menu_sync import apply_global_menu_payload_page


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "1"
    / "global_menu_alias_fixtures.json"
)


class GlobalMenuAliasContractV18Tests(unittest.TestCase):
    """Pin every new fixture to the client behavior that will consume it."""

    EXPECTED_FIXTURES = {
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
        "error_global_menu_alias_invalid_cursor",
        "global_menu_alias_reconciliation_plan",
        "global_menu_alias_reconciliation_status",
        "global_menu_alias_unknown_locator_quarantined",
        "global_menu_shadow_canonical_catalog_for_alias_resolution",
    }

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
        queue = self._payload("global_menu_alias_resolution_queue_page")
        cursor = queue["next_cursor"]
        decoded = json.loads(
            base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        )
        self.assertEqual(decoded["v"], 1)
        self.assertEqual(decoded["locator_type"], "pos_item")
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
        self.assertEqual(
            self._payload("error_global_menu_alias_mutation_not_found")["code"],
            "global_menu_alias_mutation_not_found",
        )
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
