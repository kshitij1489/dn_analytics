"""Phase 5: menu edit API conflict handling and strict-mode blocking."""

import sqlite3
import unittest

from fastapi import HTTPException

from src.api.routers.menu import _ensure_menu_edit_allowed, _finalize_menu_edit_response, get_sync_conflicts
from src.core.menu_mutation_commit import (
    MENU_CONFLICT_USER_MESSAGE,
    STRICT_MODE_NOT_READY_MESSAGE,
    build_menu_edit_http_exception,
    extract_conflict_attribution,
)
from src.core.menu_sync_quarantine import list_sync_conflicts, quarantine_event
from src.core.sync_identity import ensure_sync_identity_tables, set_menu_state_revision


class MenuEditHttpMappingTests(unittest.TestCase):
    def test_success_response_passes_through(self) -> None:
        res = {"status": "success", "message": "Merged"}
        self.assertIsNone(build_menu_edit_http_exception(res))
        self.assertEqual(_finalize_menu_edit_response(res), res)

    def test_conflict_maps_to_409_with_revision_and_attribution(self) -> None:
        conflicting_events = [
            {
                "remote_event_id": "evt-1",
                "server_seq": 456,
                "order_item_ids": ["42"],
                "attribution": {
                    "employee": {"employee_id": "e1", "name": "Alex"},
                    "device": {"device_id": "d1", "device_name": "Front Counter"},
                },
            }
        ]
        res = {
            "status": "conflict",
            "message": MENU_CONFLICT_USER_MESSAGE,
            "current_menu_revision": 124,
            "conflicting_events": conflicting_events,
        }
        exc = build_menu_edit_http_exception(res)
        self.assertIsInstance(exc, HTTPException)
        assert isinstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 409)
        detail = exc.detail
        assert isinstance(detail, dict)
        self.assertEqual(detail["message"], MENU_CONFLICT_USER_MESSAGE)
        self.assertEqual(detail["current_menu_revision"], 124)
        self.assertEqual(detail["conflicting_events"], conflicting_events)
        self.assertEqual(len(detail["attribution"]), 1)
        self.assertEqual(detail["attribution"][0]["employee"]["name"], "Alex")

        with self.assertRaises(HTTPException) as ctx:
            _finalize_menu_edit_response(res)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_extract_conflict_attribution_deduplicates(self) -> None:
        attr = {
            "employee": {"employee_id": "e1", "name": "Alex"},
            "device": {"device_id": "d1"},
        }
        events = [{"attribution": attr}, {"attribution": attr}]
        self.assertEqual(len(extract_conflict_attribution(events)), 1)

    def test_strict_mode_not_ready_maps_to_503(self) -> None:
        res = {
            "status": "error",
            "message": STRICT_MODE_NOT_READY_MESSAGE,
            "code": "strict_mode_not_ready",
        }
        exc = build_menu_edit_http_exception(res)
        self.assertIsInstance(exc, HTTPException)
        assert isinstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.detail, STRICT_MODE_NOT_READY_MESSAGE)

    def test_validation_error_maps_to_400(self) -> None:
        res = {"status": "error", "message": "Source or Target item not found"}
        exc = build_menu_edit_http_exception(res)
        assert isinstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 400)


class MenuEditStrictModeBlockTests(unittest.TestCase):
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_sync_identity_tables(conn)
        return conn

    def test_router_blocks_when_not_ready(self) -> None:
        conn = self._conn()
        conn.commit()
        try:
            with self.assertRaises(HTTPException) as ctx:
                _ensure_menu_edit_allowed(conn)
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertEqual(ctx.exception.detail, STRICT_MODE_NOT_READY_MESSAGE)
        finally:
            conn.close()

    def test_router_allows_when_ready(self) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example'), ('cloud_sync_api_key', 'secret')"
        )
        set_menu_state_revision(conn, 10)
        conn.commit()
        try:
            _ensure_menu_edit_allowed(conn)
        finally:
            conn.close()


class MenuSyncConflictsEndpointTests(unittest.TestCase):
    def test_sync_conflicts_endpoint_still_lists_quarantine(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from src.core.menu_assignment_schema import ensure_assignment_sync_schema

        ensure_assignment_sync_schema(conn)
        quarantine_event(
            conn,
            "menu_merge",
            "evt-quarantine",
            {
                "remote_event_id": "evt-quarantine",
                "event_type": "menu_merge.applied",
                "source_item": {"name": "Iced Coffee"},
                "target_item": {"name": "Cold Coffee"},
            },
            "apply failed",
        )
        conn.commit()
        try:
            payload = get_sync_conflicts(include_resolved=False, conn=conn)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(len(payload["conflicts"]), 1)
            self.assertEqual(payload["conflicts"][0]["remote_event_id"], "evt-quarantine")
            self.assertEqual(payload["supersede_notices"], [])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
