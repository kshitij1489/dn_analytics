"""Phase 5: customer merge API conflict handling and strict-mode blocking."""

import sqlite3
import unittest

from fastapi import HTTPException

from src.api.routers.orders import _ensure_customer_edit_allowed, _finalize_customer_edit_response
from src.core.customer_mutation_commit import (
    CUSTOMER_CONFLICT_USER_MESSAGE,
    NETWORK_ERROR_MESSAGE,
    STRICT_MODE_NOT_READY_MESSAGE,
    build_customer_edit_http_exception,
    extract_conflict_attribution,
)
from src.core.sync_identity import (
    ensure_sync_identity_tables,
    set_customer_state_revision,
    set_customer_strict_mode_enabled,
)


class CustomerEditHttpMappingTests(unittest.TestCase):
    def test_success_response_passes_through(self) -> None:
        res = {"status": "success", "message": "Merged", "merge_id": 42}
        self.assertIsNone(build_customer_edit_http_exception(res))
        self.assertEqual(_finalize_customer_edit_response(res), res)

    def test_conflict_maps_to_409_with_revision_and_attribution(self) -> None:
        conflicting_events = [
            {
                "remote_event_id": "evt-1",
                "server_seq": 456,
                "customer_keys": ["phone-hash-1"],
                "attribution": {
                    "employee": {"employee_id": "e1", "name": "Alex"},
                    "device": {"device_id": "d1", "device_name": "Front Counter"},
                },
            }
        ]
        res = {
            "status": "conflict",
            "message": CUSTOMER_CONFLICT_USER_MESSAGE,
            "current_customer_revision": 124,
            "conflicting_events": conflicting_events,
        }
        exc = build_customer_edit_http_exception(res)
        self.assertIsInstance(exc, HTTPException)
        assert isinstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 409)
        detail = exc.detail
        assert isinstance(detail, dict)
        self.assertEqual(detail["message"], CUSTOMER_CONFLICT_USER_MESSAGE)
        self.assertEqual(detail["current_customer_revision"], 124)
        self.assertEqual(detail["conflicting_events"], conflicting_events)
        self.assertEqual(len(detail["attribution"]), 1)
        self.assertEqual(detail["attribution"][0]["employee"]["name"], "Alex")

        with self.assertRaises(HTTPException) as ctx:
            _finalize_customer_edit_response(res)
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
        exc = build_customer_edit_http_exception(res)
        self.assertIsInstance(exc, HTTPException)
        assert isinstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.detail, STRICT_MODE_NOT_READY_MESSAGE)

    def test_network_error_maps_to_503(self) -> None:
        res = {"status": "error", "message": NETWORK_ERROR_MESSAGE}
        exc = build_customer_edit_http_exception(res)
        assert isinstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.detail, NETWORK_ERROR_MESSAGE)

    def test_validation_error_maps_to_400(self) -> None:
        res = {"status": "error", "message": "Source or target customer not found"}
        exc = build_customer_edit_http_exception(res)
        assert isinstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 400)


class CustomerEditStrictModeBlockTests(unittest.TestCase):
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_sync_identity_tables(conn)
        return conn

    def test_router_blocks_when_strict_flag_on_but_not_ready(self) -> None:
        conn = self._conn()
        set_customer_strict_mode_enabled(conn, True)
        conn.commit()
        try:
            with self.assertRaises(HTTPException) as ctx:
                _ensure_customer_edit_allowed(conn)
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertEqual(ctx.exception.detail, STRICT_MODE_NOT_READY_MESSAGE)
        finally:
            conn.close()

    def test_router_allows_when_strict_flag_off(self) -> None:
        conn = self._conn()
        set_customer_strict_mode_enabled(conn, False)
        conn.commit()
        try:
            _ensure_customer_edit_allowed(conn)
        finally:
            conn.close()

    def test_router_allows_when_strict_flag_on_and_ready(self) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('cloud_sync_url', 'https://cloud.example'), ('cloud_sync_api_key', 'secret')"
        )
        set_customer_strict_mode_enabled(conn, True)
        set_customer_state_revision(conn, 10)
        conn.commit()
        try:
            _ensure_customer_edit_allowed(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
