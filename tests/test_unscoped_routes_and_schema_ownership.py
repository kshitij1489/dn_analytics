"""Phase 1 guards for routes that must stay unscoped, and for schema ownership.

Two invariants the rest of the suite does not cover:

1. `X-Restaurant-ID` is required on scoped routes and **must not** appear on the
   allowed-restaurants selector source or on the unscoped telemetry routes
   (`errors/ingest`, `learning/ingest`, `conversations/sync`). Sending it there
   would either be refused or silently narrow a global upload to one store.
2. `database/schema_sqlite.sql` plus `src/core/db/` own every table definition.
   API routers and `main.py` must not carry their own `CREATE TABLE` copies,
   which is how the pre-Phase-1 startup path drifted from the canonical schema.
"""

import ast
import re
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.central_api import scoped_headers, unscoped_analytics_headers
from src.core.profiles import ProfileSelectionRequired


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESTAURANT_HEADER = "X-Restaurant-ID"


def _bound_connection(restaurant_id: str = "rest-A") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE restaurant_profile_identity (singleton_id INTEGER PRIMARY KEY, restaurant_id TEXT)"
    )
    conn.execute("INSERT INTO restaurant_profile_identity VALUES (1, ?)", (restaurant_id,))
    conn.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany(
        "INSERT INTO system_config (key, value) VALUES (?, ?)",
        [
            ("cloud_sync_url", "https://central.example"),
            ("cloud_sync_api_key", "sync-token"),
        ],
    )
    conn.commit()
    return conn


class UnscopedRouteHeaderTests(unittest.TestCase):
    def test_restaurant_list_headers_carry_api_key_without_selector(self) -> None:
        headers = unscoped_analytics_headers("analytics-key")
        self.assertEqual(headers["X-API-Key"], "analytics-key")
        self.assertNotIn(RESTAURANT_HEADER, headers)
        self.assertNotIn("Authorization", headers)

    def test_orders_verification_probes_the_list_endpoint_unscoped(self) -> None:
        """`/config/verify` runs before any profile exists, so it may not be scoped."""
        from src.api.routers.config import ConfigVerification, verify_config
        from utils.api_client import orders_integration_request_headers

        self.assertNotIn(RESTAURANT_HEADER, orders_integration_request_headers("k"))

        with patch("requests.get") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"restaurants": []}
            verify_config(
                ConfigVerification(
                    type="orders",
                    settings={
                        "integration_orders_url": "https://central.example/analytics",
                        "integration_orders_key": "analytics-key",
                    },
                )
            )

        url = request.call_args.args[0] if request.call_args.args else request.call_args.kwargs["url"]
        self.assertTrue(url.endswith("/restaurants/"), url)
        self.assertNotIn(RESTAURANT_HEADER, request.call_args.kwargs["headers"])
        self.assertNotIn("restaurant", str(request.call_args.kwargs.get("params") or {}).lower())

    def test_error_ingest_stays_unscoped(self) -> None:
        from src.core import error_shipper

        with patch("requests.post") as post, patch.object(
            error_shipper, "_collect_error_files",
            return_value=[(Path("errors.jsonl"), [{"message": "boom"}])],
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"accepted": 1}
            error_shipper.upload_pending(
                endpoint="https://central.example/desktop-analytics-sync/errors/ingest",
                auth="sync-token",
            )

        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sync-token")
        self.assertNotIn(RESTAURANT_HEADER, headers)

    def test_learning_ingest_stays_unscoped(self) -> None:
        from src.core import learning_shipper

        conn = _bound_connection()
        self.addCleanup(conn.close)
        with patch("requests.post") as post, patch.object(
            learning_shipper, "_select_unsent_ai_logs", return_value=[]
        ), patch.object(learning_shipper, "_select_unsent_ai_feedback", return_value=[]):
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"accepted": 0}
            learning_shipper.upload_pending(
                conn,
                endpoint="https://central.example/desktop-analytics-sync/learning/ingest",
                auth="sync-token",
            )

        self.assertTrue(post.called, "learning upload should always POST tier-3 payload")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sync-token")
        self.assertNotIn(RESTAURANT_HEADER, headers)

    def test_conversation_sync_stays_unscoped(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from services import sync_conversations

        conn = _bound_connection()
        self.addCleanup(conn.close)

        post = AsyncMock()
        post.return_value = MagicMock(
            status_code=200, json=MagicMock(return_value={"synced": 1})
        )
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=MagicMock(post=post))
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(sync_conversations.httpx, "AsyncClient", return_value=client), \
                patch.object(
                    sync_conversations, "get_messages_for_conversation",
                    AsyncMock(return_value=[]),
                ):
            asyncio.run(
                sync_conversations.sync_to_master(
                    conn,
                    [{"conversation_id": "c1", "title": "t"}],
                    base_url="https://central.example",
                    auth="sync-token",
                )
            )

        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sync-token")
        self.assertNotIn(RESTAURANT_HEADER, headers)

    def test_scoped_route_still_requires_the_selector(self) -> None:
        """Counterpart: the same helper refuses to build headers without identity."""
        conn = _bound_connection()
        self.addCleanup(conn.close)
        self.assertEqual(
            scoped_headers(conn, auth_kind="sync", credential="sync-token")[RESTAURANT_HEADER],
            "rest-A",
        )

        anonymous = sqlite3.connect(":memory:")
        self.addCleanup(anonymous.close)
        with self.assertRaises(ProfileSelectionRequired):
            scoped_headers(anonymous, auth_kind="sync", credential="sync-token")


class SchemaOwnershipTests(unittest.TestCase):
    """Routers and API startup must not carry table DDL of their own."""

    DDL = re.compile(r"CREATE\s+(TABLE|INDEX|VIEW|TRIGGER)", re.IGNORECASE)

    def _string_literals(self, path: Path):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield node.value

    def test_api_layer_contains_no_table_ddl(self) -> None:
        api_files = sorted((PROJECT_ROOT / "src" / "api").rglob("*.py"))
        self.assertGreater(len(api_files), 5, "expected the API package to be discovered")

        offenders = []
        for path in api_files:
            for literal in self._string_literals(path):
                if self.DDL.search(literal):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {literal.strip()[:60]}")
        self.assertEqual(
            offenders,
            [],
            "API routers/startup must defer to the canonical schema owner "
            "(database/schema_sqlite.sql via apply_analytics_schema, and "
            "src/core/db/control.py for the control database):\n"
            + "\n".join(offenders),
        )

    def test_canonical_owners_hold_the_profile_and_control_schemas(self) -> None:
        profile_schema = (PROJECT_ROOT / "database" / "schema_sqlite.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS restaurant_profile_identity", profile_schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS global_menu_history", profile_schema)
        self.assertIn("history_cursor TEXT", profile_schema)
        self.assertIn(
            "price DECIMAL(10,2) CHECK (price IS NULL OR price >= 0)",
            profile_schema,
        )

        from src.core.db.control import CONTROL_SCHEMA_SQL

        for table in ("restaurant_profiles", "app_selection", "global_config"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", CONTROL_SCHEMA_SQL)

        # The control tables belong to the control database only — never mixed
        # into an analytics profile file.
        for table in ("restaurant_profiles", "app_selection", "global_config"):
            self.assertNotIn(f"CREATE TABLE IF NOT EXISTS {table}", profile_schema)

    def test_startup_applies_schema_through_the_shared_helper(self) -> None:
        main_source = (PROJECT_ROOT / "src" / "api" / "main.py").read_text(encoding="utf-8")
        self.assertIn("ensure_control_schema", main_source)
        self.assertIn("get_profile_connection", main_source)
        self.assertNotIn("executescript", main_source)

    def test_order_loader_repairs_partial_schema_through_shared_helper(self) -> None:
        from services.load_orders import create_schema_if_needed
        from src.core.db.connection import apply_analytics_schema

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        apply_analytics_schema(conn)
        conn.execute("DROP TABLE weather_daily")
        conn.commit()

        create_schema_if_needed(conn)

        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weather_daily'"
            ).fetchone()
        )

    def test_backend_package_includes_schema_but_not_shared_weather_export(self) -> None:
        spec = (PROJECT_ROOT / "installer" / "backend.spec").read_text(encoding="utf-8")
        self.assertIn("(os.path.join(project_root, 'database'), 'database')", spec)
        self.assertNotIn("'weather_history.csv'", spec)


if __name__ == "__main__":
    unittest.main()
