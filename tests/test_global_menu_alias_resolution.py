"""Executable revision-1.8 alias transport ownership tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from src.core.global_menu_alias_resolution import (
    AliasResolutionError,
    alias_decision_status,
    commit_alias_decision,
    fetch_alias_queue,
    fetch_alias_reconciliation_plan,
    fetch_alias_reconciliation_status,
    preview_alias_decision,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "1"
    / "global_menu_alias_fixtures.json"
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"

    def json(self):
        return self._payload


class GlobalMenuAliasResolutionClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.fixtures = {row["name"]: row for row in document["fixtures"]}

    def payload(self, name):
        fixture = self.fixtures[name]
        referenced = fixture.get("payload_fixture")
        return self.payload(referenced) if referenced else fixture["payload"]

    def request(self, name):
        fixture = self.fixtures[name]
        referenced = fixture.get("request_fixture")
        return self.request(referenced) if referenced else fixture["request"]

    def setUp(self):
        self.conn = MagicMock()
        self.capability = SimpleNamespace(
            menu_group_id="group-1",
            resolution_advertised=True,
        )
        self.patches = [
            patch(
                "src.core.global_menu_alias_resolution._require_alias_capability",
                return_value=self.capability,
            ),
            patch(
                "src.core.global_menu_alias_resolution._connection",
                return_value=("https://central.example", "sync-key"),
            ),
            patch(
                "src.core.global_menu_alias_resolution._headers",
                return_value={"X-Global-Menu-Key": "editor-key"},
            ),
        ]
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

    @patch("requests.get")
    def test_queue_uses_editor_transport_and_validates_cursor(self, get):
        get.return_value = _Response(
            self.payload("global_menu_alias_resolution_queue_page")
        )

        result = fetch_alias_queue(
            self.conn,
            status="pending",
            after="opaque-prior-cursor",
            limit=25,
        )

        self.assertEqual(result["rows"][0]["candidate"]["reason"], "unique_itemcode")
        self.assertEqual(
            get.call_args.kwargs["params"],
            {"status": "pending", "after": "opaque-prior-cursor", "limit": 25},
        )
        self.assertEqual(
            get.call_args.kwargs["headers"]["X-Global-Menu-Key"], "editor-key"
        )

    @patch("requests.post")
    def test_preview_and_commit_pin_fixture_semantics(self, post):
        preview_request = self.request("global_menu_alias_decision_preview_approved")
        commit_request = self.request("global_menu_alias_decision_commit_approved")
        post.side_effect = [
            _Response(self.payload("global_menu_alias_decision_preview_approved")),
            _Response(self.payload("global_menu_alias_decision_commit_approved")),
        ]

        preview = preview_alias_decision(self.conn, preview_request)
        committed = commit_alias_decision(self.conn, commit_request)

        self.assertTrue(preview["commit_allowed"])
        self.assertEqual(committed["decision_status"], "approved")
        self.assertEqual(post.call_args.kwargs["json"], commit_request)
        self.conn.execute.assert_not_called()
        self.conn.commit.assert_not_called()

    @patch("requests.get")
    @patch("requests.post")
    def test_commit_timeout_reconciles_by_same_mutation_id(self, post, get):
        request = self.request("global_menu_alias_decision_commit_approved")
        post.side_effect = requests.Timeout("uncertain")
        get.return_value = _Response(
            self.payload("global_menu_alias_decision_status_applied")
        )

        result = commit_alias_decision(self.conn, request)

        self.assertEqual(result["mutation_id"], request["mutation_id"])
        self.assertIn(request["mutation_id"], get.call_args.args[0])

    @patch("requests.get")
    def test_plan_and_status_are_strictly_parsed(self, get):
        get.side_effect = [
            _Response(self.payload("global_menu_alias_reconciliation_plan")),
            _Response(self.payload("global_menu_alias_reconciliation_status")),
        ]

        plan = fetch_alias_reconciliation_plan(self.conn)
        status = fetch_alias_reconciliation_status(self.conn)

        self.assertTrue(plan["execution_ready"])
        self.assertTrue(status["policy"]["capability_advertised"])

    @patch("requests.get")
    def test_central_error_payload_remains_machine_readable(self, get):
        payload = self.payload("error_global_menu_alias_revision_conflict")
        get.return_value = _Response(payload, 409)

        with self.assertRaises(AliasResolutionError) as raised:
            alias_decision_status(
                self.conn, "8c2a5d64-0f31-4a77-bb02-5e9f7c3a1d10"
            )

        self.assertEqual(raised.exception.payload, payload)
        self.assertEqual(
            raised.exception.payload["recommended_action"],
            "refresh_alias_resolution",
        )

    def test_malformed_mutation_id_is_rejected_before_transport(self):
        with self.assertRaises(AliasResolutionError) as raised:
            alias_decision_status(self.conn, "not-a-uuid")
        self.assertEqual(raised.exception.code, "global_menu_alias_mutation_invalid")

    @patch("requests.get")
    def test_unknown_candidate_reason_fails_closed(self, get):
        payload = self.payload("global_menu_alias_resolution_queue_page")
        payload["rows"][0]["candidate"]["reason"] = "fuzzy_guess"
        get.return_value = _Response(payload)
        with self.assertRaises(AliasResolutionError) as raised:
            fetch_alias_queue(self.conn)
        self.assertEqual(raised.exception.code, "global_menu_alias_response_invalid")


if __name__ == "__main__":
    unittest.main()
