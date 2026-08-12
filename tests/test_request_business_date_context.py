"""The request-scoped business-date binding must reach the endpoint.

FastAPI runs a *sync* generator dependency's setup and teardown as two separate
threadpool calls, each with its own copied ``contextvars.Context``. A binding
made during setup was therefore discarded before the endpoint ran, and the
teardown reset raised ``Token was created in a different Context``. Both halves
are pinned here because the visible symptom (a logged traceback) was the
harmless one; the silent fallback to ``DEFAULT_TIMEZONE`` was not.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import get_authorized_db, get_db
from src.core.utils.business_date import (
    DEFAULT_TIMEZONE,
    business_date_context,
    current_business_date_context,
)


NON_DEFAULT_TIMEZONE = "America/New_York"


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeProfile:
    restaurant_id = "rest-A"
    timezone = NON_DEFAULT_TIMEZONE
    authorization_state = "authorized"
    is_bound = True


class RequestBusinessDateContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertNotEqual(NON_DEFAULT_TIMEZONE, DEFAULT_TIMEZONE)
        self.connections: list[_FakeConnection] = []

        def fake_open(_profile):
            connection = _FakeConnection()
            self.connections.append(connection)
            return connection, "Connected"

        patcher = patch("src.api.dependencies.get_profile_connection", fake_open)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.app = FastAPI()
        for name, dependency in (("db", get_db), ("authorized", get_authorized_db)):

            def _make(dep):
                def sync_route(conn=Depends(dep)):
                    return {"timezone": current_business_date_context().timezone}

                async def async_route(conn=Depends(dep)):
                    return {"timezone": current_business_date_context().timezone}

                return sync_route, async_route

            sync_route, async_route = _make(dependency)
            self.app.get(f"/{name}/sync")(sync_route)
            self.app.get(f"/{name}/async")(async_route)

        # Both dependencies resolve their profile through these, so the routes
        # exercise the real dependency bodies with a stand-in profile.
        from src.api import dependencies as dependencies_module

        self.app.dependency_overrides[
            dependencies_module.get_restaurant_profile
        ] = lambda: _FakeProfile()
        self.app.dependency_overrides[
            dependencies_module.get_authorized_restaurant_profile
        ] = lambda: _FakeProfile()

    def test_profile_timezone_reaches_sync_and_async_endpoints(self) -> None:
        client = TestClient(self.app)
        for path in ("/db/sync", "/db/async", "/authorized/sync", "/authorized/async"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json()["timezone"],
                    NON_DEFAULT_TIMEZONE,
                    "the request-scoped binding did not reach the endpoint",
                )

    def test_teardown_closes_the_connection_without_a_context_error(self) -> None:
        # TestClient re-raises server exceptions, so a teardown ValueError fails
        # this request rather than only appearing in the log.
        client = TestClient(self.app, raise_server_exceptions=True)
        self.assertEqual(client.get("/db/sync").status_code, 200)
        self.assertEqual(client.get("/authorized/async").status_code, 200)
        self.assertEqual(len(self.connections), 2)
        self.assertTrue(all(connection.closed for connection in self.connections))

    def test_binding_does_not_leak_out_of_the_request(self) -> None:
        client = TestClient(self.app)
        self.assertEqual(client.get("/db/sync").status_code, 200)
        self.assertEqual(current_business_date_context().timezone, DEFAULT_TIMEZONE)

    def test_context_manager_still_nests_inside_one_frame(self) -> None:
        # ScopedReader.read enters and leaves within a single call, which stays
        # the right tool there.
        with business_date_context(timezone=NON_DEFAULT_TIMEZONE) as bound:
            self.assertEqual(bound.timezone, NON_DEFAULT_TIMEZONE)
            self.assertEqual(
                current_business_date_context().timezone, NON_DEFAULT_TIMEZONE
            )
        self.assertEqual(current_business_date_context().timezone, DEFAULT_TIMEZONE)


if __name__ == "__main__":
    unittest.main()
