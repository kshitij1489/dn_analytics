from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock, patch

from fastapi import HTTPException
from src.api.routers.menu import (
    _ensure_menu_edit_allowed,
    _ensure_legacy_menu_pull_allowed,
    _global_menu_request_active,
    get_merge_history,
)
from src.core.analytics_stream_contract import (
    AnalyticsStreamContractError,
    parse_allowed_restaurants,
)
from src.core.db.connection import apply_analytics_schema
from src.core.db.control import ensure_control_schema
from src.core.global_menu_identity import (
    GlobalIdentityResolution,
    GlobalMenuIdentityError,
    ensure_variant_projection_owner,
    global_ids_for_local,
    resolve_redirect_chain,
    resolve_global_identity_for_ingest,
)
from src.core.global_menu_history import (
    apply_global_menu_history_page,
    pull_global_menu_history,
)
from src.core.global_menu_mutation import (
    GlobalMenuMutationError,
    _normalize_mutation_payload,
    _headers,
    _apply_accepted_projection,
    build_global_action_from_local,
    commit_global_mutation,
    global_resolution_context,
    preview_global_mutation,
)
from src.core.global_menu_schema import (
    GLOBAL_MENU_MODE,
    GlobalMenuCapabilityError,
    GlobalMenuCapabilityStatus,
    require_global_menu_capability,
    resolve_global_menu_capability,
    update_global_menu_state,
)
from src.core.global_menu_sync import (
    _contract_rule_id,
    _fetch_page,
    apply_global_assignment_rows,
    apply_global_menu_payload_page,
    pull_global_assignment_snapshot,
    pull_global_menu_status,
    pull_global_menu_state,
)
from src.core.profiles import RestaurantProfile, get_profile, upsert_allowed_restaurants
from src.core.queries.multi_store_reducers import (
    First,
    Min,
    Sum,
    group_menu_identity_rows,
)
from src.core.queries.menu_queries import (
    fetch_resolution_counts,
    fetch_unverified_items,
)
from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK
from src.core.sync_identity import get_menu_state_revision
from utils.id_generator import generate_deterministic_id
from utils.menu_item_variant_enforcement import (
    addon_seeded_mapping_order_item_id,
    catalog_stub_order_item_id,
)
from services.clustering_service import OrderItemCluster


FIXTURE = Path(__file__).parent / "fixtures" / "global_menu_v1_snapshot.json"
CONTRACT_FIXTURE = (
    Path(__file__).parents[1] / "contracts" / "fixtures" / "1" / "global_menu_fixtures.json"
)
CONTRACT_FIXTURE_DIR = Path(__file__).parents[1] / "contracts" / "fixtures" / "1"


def contract_fixture(name: str) -> Dict[str, Any]:
    document = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    return next(
        fixture
        for fixture in document["fixtures"]
        if fixture.get("name") == name
    )


def named_contract_fixture(filename: str, name: str) -> Dict[str, Any]:
    document = json.loads((CONTRACT_FIXTURE_DIR / filename).read_text(encoding="utf-8"))
    return next(
        fixture
        for fixture in document["fixtures"]
        if fixture.get("name") == name
    )


def capability(
    restaurant_id: str = "rest-1",
    *,
    revision: int = 0,
    complete: bool = True,
    group_id: str = "group-desserts",
    capabilities: tuple[str, ...] = (
        "global_menu_v1",
        "global_menu_resolution_v1",
        "global_menu_aggregation_v1",
        "global_menu_mutations_v1",
    ),
) -> GlobalMenuCapabilityStatus:
    return GlobalMenuCapabilityStatus(
        mode=GLOBAL_MENU_MODE,
        restaurant_id=restaurant_id,
        menu_group_id=group_id,
        schema_version=1,
        server_advertised=True,
        authorized=True,
        selected=True,
        reason="active",
        catalog_revision=revision,
        mutation_revision=revision,
        bootstrap_status="complete" if complete else "not_started",
        coverage_linked=2 if complete else 0,
        coverage_total=2 if complete else 0,
        capabilities=capabilities,
    )




def profile(restaurant_id: str) -> RestaurantProfile:
    return RestaurantProfile(
        restaurant_id=restaurant_id,
        display_name=restaurant_id,
        timezone="Asia/Kolkata",
        database_path=f"/{restaurant_id}.db",
        authorization_state="authorized",
        is_bound=True,
        menu_group_id="group-desserts",
        menu_capabilities=("global_menu_v1",),
    )


class GlobalMenuSchemaAndRegistryTests(unittest.TestCase):
    def test_schema_create_and_reapply_are_idempotent_and_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(conn)
        apply_analytics_schema(conn)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM global_menu_items").fetchone()[0], 0
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM global_menu_mapping_rules").fetchone()[0], 0
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0], 0
        )
        state_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(global_menu_state)")
        }
        rule_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(global_menu_mapping_rules)")
        }
        mapping_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(menu_item_variants)")
        }
        self.assertIn("history_cursor", state_columns)
        self.assertIn("price", rule_columns)
        self.assertFalse(
            {"shared_pos_rule_tombstoned", "shared_pos_prior_is_active"}
            & mapping_columns
        )
        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='idx_global_menu_history_order'"
            ).fetchone()
        )
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()

    def test_mapping_rule_price_is_nullable_non_negative_decimal(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(conn)
        conn.execute(
            """
            INSERT INTO global_menu_items (
                global_menu_item_id, menu_group_id, canonical_name,
                canonical_type, server_revision
            ) VALUES ('global-item', 'group-1', 'Cold Coffee', 'Beverage', 1)
            """
        )
        values = (
            "rule-1",
            "group-1",
            "group",
            "pos-item",
            "101",
            "101",
            "global-item",
            "fixture",
            1,
        )
        conn.execute(
            """
            INSERT INTO global_menu_mapping_rules (
                rule_id, menu_group_id, locator_scope, locator_kind,
                locator_value, normalized_locator, target_global_menu_item_id,
                price, provenance, server_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '290.00', ?, ?)
            """,
            values,
        )
        self.assertEqual(
            conn.execute(
                "SELECT printf('%.2f', price) FROM global_menu_mapping_rules"
            ).fetchone()[0],
            "290.00",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE global_menu_mapping_rules SET price=-0.01 WHERE rule_id='rule-1'
                """
            )
        conn.close()

    def test_schema_upgrade_allows_blank_global_item_type_and_preserves_rows(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        schema_sql = (
            Path(__file__).parents[1] / "database" / "schema_sqlite.sql"
        ).read_text(encoding="utf-8")
        legacy_schema_sql = schema_sql.replace(
            "canonical_type TEXT NOT NULL DEFAULT '',",
            "canonical_type TEXT NOT NULL CHECK (TRIM(canonical_type) <> ''),",
            1,
        )
        self.assertNotEqual(legacy_schema_sql, schema_sql)
        conn.executescript(legacy_schema_sql)
        conn.execute(
            """
            INSERT INTO global_menu_items (
                global_menu_item_id, menu_group_id, canonical_name,
                canonical_type, lifecycle_state, server_revision
            ) VALUES ('existing-item', 'group-1', 'Existing', 'Dessert', 'active', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type)
            VALUES ('local-existing', 'Existing', 'Dessert')
            """
        )
        conn.execute(
            """
            INSERT INTO menu_item_global_links (
                local_menu_item_id, global_menu_item_id, provenance,
                server_revision, is_projection_owner
            ) VALUES ('local-existing', 'existing-item', 'projection', 1, 1)
            """
        )
        conn.commit()

        apply_analytics_schema(conn)
        conn.execute(
            """
            INSERT INTO global_menu_items (
                global_menu_item_id, menu_group_id, canonical_name,
                canonical_type, lifecycle_state, server_revision
            ) VALUES ('redirected-item', 'group-1', 'Coconut Pineapple (110gm)',
                      '', 'redirected', 1)
            """
        )

        rows = conn.execute(
            """
            SELECT global_menu_item_id, canonical_type
            FROM global_menu_items
            ORDER BY global_menu_item_id
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [("existing-item", "Dessert"), ("redirected-item", "")],
        )
        self.assertEqual(
            conn.execute(
                "SELECT global_menu_item_id FROM menu_item_global_links "
                "WHERE local_menu_item_id='local-existing'"
            ).fetchone()[0],
            "existing-item",
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()

    def test_pre_revision_17_profile_upgrades_global_menu_columns_in_place(self) -> None:
        """A profile written by the previous release must still open.

        ``CREATE TABLE IF NOT EXISTS`` cannot add ``global_menu_state.history_cursor``
        or ``global_menu_mapping_rules.price`` to tables an older build already
        created, so the additive upgrade has to run before the projection
        validator. Without it every pre-revision-1.7 profile fails to open.
        """
        from src.core.db.connection import analytics_schema_path

        schema_sql = analytics_schema_path().read_text(encoding="utf-8")
        legacy_sql = schema_sql.replace(
            "    history_cursor TEXT,\n", ""
        ).replace(
            "    price DECIMAL(10,2) CHECK (price IS NULL OR price >= 0),\n", ""
        )
        self.assertNotEqual(legacy_sql, schema_sql)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.executescript(legacy_sql)
        self.assertNotIn(
            "history_cursor",
            {row[1] for row in conn.execute("PRAGMA table_info(global_menu_state)")},
        )

        apply_analytics_schema(conn)

        self.assertIn(
            "history_cursor",
            {row[1] for row in conn.execute("PRAGMA table_info(global_menu_state)")},
        )
        self.assertIn(
            "price",
            {
                row[1]
                for row in conn.execute("PRAGMA table_info(global_menu_mapping_rules)")
            },
        )
        # The dormant projection is still initialized and usable after upgrade.
        update_global_menu_state(conn, history_cursor="cursor-1")
        self.assertEqual(
            conn.execute(
                "SELECT history_cursor FROM global_menu_state WHERE singleton_id=1"
            ).fetchone()[0],
            "cursor-1",
        )

    def test_control_schema_upgrades_old_registry_additively(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE restaurant_profiles (
                restaurant_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                timezone TEXT NOT NULL,
                database_path TEXT NOT NULL UNIQUE,
                local_address TEXT,
                authorization_state TEXT NOT NULL,
                last_listed_at TEXT,
                last_sync_status TEXT,
                last_sync_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        ensure_control_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(restaurant_profiles)")}
        self.assertIn("menu_group_id", columns)
        self.assertIn("menu_capabilities", columns)
        self.assertIn("clean_rebuild_status", columns)
        self.assertIn("last_archive_path", columns)

    def test_registry_parser_is_additive_and_rejects_capability_without_group(self) -> None:
        legacy = parse_allowed_restaurants(
            {
                "restaurants": [
                    {
                        "restaurant_id": "rest-1",
                        "display_name": "One",
                        "timezone": "Asia/Kolkata",
                    }
                ]
            }
        )
        self.assertIsNone(legacy[0]["menu_group_id"])
        self.assertEqual(legacy[0]["menu_capabilities"], [])
        with self.assertRaises(AnalyticsStreamContractError):
            parse_allowed_restaurants(
                {
                    "restaurants": [
                        {
                            "restaurant_id": "rest-1",
                            "display_name": "One",
                            "timezone": "Asia/Kolkata",
                            "menu_capabilities": ["global_menu_v1"],
                        }
                    ]
                }
            )

    def test_registry_refresh_clears_removed_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "ANALYTICS_APP_DATA_ROOT": tmp,
                "ANALYTICS_CONTROL_DB_PATH": str(Path(tmp) / "control.db"),
                "ANALYTICS_DB_PATH": str(Path(tmp) / "analytics.db"),
            }
            with patch.dict(os.environ, env, clear=False):
                upsert_allowed_restaurants(
                    [
                        {
                            "restaurant_id": "rest-1",
                            "display_name": "One",
                            "timezone": "Asia/Kolkata",
                            "menu_group_id": "group-desserts",
                            "menu_capabilities": ["global_menu_v1"],
                        }
                    ]
                )
                self.assertEqual(get_profile("rest-1").menu_capabilities, ("global_menu_v1",))
                upsert_allowed_restaurants(
                    [
                        {
                            "restaurant_id": "rest-1",
                            "display_name": "One",
                            "timezone": "Asia/Kolkata",
                        }
                    ]
                )
                refreshed = get_profile("rest-1")
                self.assertIsNone(refreshed.menu_group_id)
                self.assertEqual(refreshed.menu_capabilities, ())

    def test_missing_projection_schema_degrades_to_legacy(self) -> None:
        conn = sqlite3.connect(":memory:")
        status = resolve_global_menu_capability(conn)
        self.assertEqual(status.mode, "legacy_restaurant_v1")
        self.assertEqual(status.reason, "projection_schema_missing")

    def test_a_narrower_capability_set_exposes_narrower_readiness(self) -> None:
        resolution_only = capability(
            capabilities=("global_menu_v1", "global_menu_resolution_v1")
        )
        self.assertTrue(resolution_only.active)
        self.assertTrue(resolution_only.resolution_advertised)
        self.assertFalse(resolution_only.aggregation_ready)
        self.assertFalse(resolution_only.mutation_ready)
        with_aggregation = capability(
            capabilities=(
                "global_menu_v1",
                "global_menu_resolution_v1",
                "global_menu_aggregation_v1",
            )
        )
        self.assertTrue(with_aggregation.resolution_advertised)
        self.assertTrue(with_aggregation.aggregation_ready)
        self.assertFalse(with_aggregation.mutation_ready)
        active = capability()
        self.assertTrue(active.aggregation_ready)
        self.assertTrue(active.mutation_ready)


class Revision17ContractFixtureOwnershipTests(unittest.TestCase):
    """Pin each revision-1.7 fixture addition to an analytics-side owner."""

    OWNED_FIXTURES = {
        ("analytics_stream_fixtures.json", "allowed_restaurants"),
        ("menu_bootstrap_fixtures.json", "menu_bootstrap_ingest_request"),
        ("menu_bootstrap_fixtures.json", "menu_bootstrap_ingest_response"),
        (
            "menu_bootstrap_fixtures.json",
            "menu_bootstrap_ingest_response_seed_only_existing",
        ),
        ("global_menu_fixtures.json", "global_menu_snapshot_rules"),
        ("global_menu_fixtures.json", "global_menu_events_page"),
        ("global_menu_fixtures.json", "global_menu_status"),
        ("global_menu_fixtures.json", "global_menu_history_page"),
        ("global_menu_fixtures.json", "global_menu_mutation_preview_locator_map"),
        ("global_menu_fixtures.json", "error_global_menu_alias_locator_retired"),
        ("global_menu_fixtures.json", "error_global_menu_itemcode_variant_target"),
        (
            "menu_mutations_fixtures.json",
            "menu_mutations_commit_request_global_canonical_write",
        ),
        (
            "menu_mutations_fixtures.json",
            "menu_mutations_commit_response_global_canonical_write_409",
        ),
    }

    def test_owner_manifest_names_existing_fixtures(self) -> None:
        for filename, name in sorted(self.OWNED_FIXTURES):
            self.assertEqual(named_contract_fixture(filename, name)["name"], name)

    def test_snapshot_and_event_rules_are_restaurant_scoped_and_priceless(self) -> None:
        snapshot = contract_fixture("global_menu_snapshot_rules")["payload"]["rows"][0]
        event = contract_fixture("global_menu_events_page")["payload"]["events"][0]
        event_rule = event["payload"]["mapping_rules"][0]
        for rule in (snapshot, event_rule):
            self.assertEqual(rule["rule_scope"], "restaurant")
            self.assertEqual(rule["locator_type"], "pos_item")
            self.assertEqual(rule["restaurant_id"], "1c8w7fp500")
            self.assertNotIn("price", rule)

    def test_history_fixture_fits_the_canonical_cache_and_order(self) -> None:
        payload = contract_fixture("global_menu_history_page")["payload"]
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        for row in payload["rows"]:
            conn.execute(
                """
                INSERT INTO global_menu_history (
                    history_id, menu_group_id, source_event_id, source_kind,
                    event_type, origin_restaurant_id, actor, attribution,
                    occurred_at, server_ingested_at, source, target,
                    mutation_id, is_undoable, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["history_id"],
                    payload["menu_group_id"],
                    row["source_event_id"],
                    row["source_kind"],
                    row["event_type"],
                    row["origin_restaurant_id"],
                    row["actor"],
                    json.dumps(row["attribution"], sort_keys=True),
                    row["occurred_at"],
                    row["server_ingested_at"],
                    json.dumps(row["source"], sort_keys=True),
                    json.dumps(row["target"], sort_keys=True),
                    row["mutation_id"],
                    int(row["is_undoable"]),
                    json.dumps(row["detail"], sort_keys=True),
                ),
            )
        ordered = conn.execute(
            """
            SELECT history_id FROM global_menu_history
            ORDER BY occurred_at DESC, source_kind ASC, source_event_id DESC
            """
        ).fetchall()
        self.assertEqual(
            [row[0] for row in ordered],
            ["global:1", "legacy:9zz9zz9zz9:legacy-super-2"],
        )
        conn.close()

    def test_canonical_write_refusal_fixture_is_machine_actionable(self) -> None:
        request = named_contract_fixture(
            "menu_mutations_fixtures.json",
            "menu_mutations_commit_request_global_canonical_write",
        )
        response = named_contract_fixture(
            "menu_mutations_fixtures.json",
            "menu_mutations_commit_response_global_canonical_write_409",
        )
        self.assertEqual(request["payload"]["mutation_type"], "catalog_update")
        self.assertEqual(response["http_status"], 409)
        self.assertEqual(
            response["payload"]["code"], "global_menu_canonical_write_blocked"
        )
        self.assertEqual(
            response["payload"]["recommended_action"], "use_global_menu_mutations"
        )

    def test_allowed_restaurants_fixture_advertises_one_active_group(self) -> None:
        fixture = named_contract_fixture(
            "analytics_stream_fixtures.json", "allowed_restaurants"
        )
        restaurants = parse_allowed_restaurants(fixture["payload"])
        self.assertEqual({row["menu_group_id"] for row in restaurants}, {"group-1"})
        self.assertEqual(
            {
                tuple(row["menu_capabilities"])
                for row in restaurants
            },
            {
                tuple(
                    sorted(
                        {
                            "global_menu_v1",
                            "global_menu_resolution_v1",
                            "global_menu_aggregation_v1",
                            "global_menu_mutations_v1",
                        }
                    )
                )
            },
        )


class PhaseFCleanRebuildDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_expose_counts_coverage_cursors_quarantine_and_digests(self) -> None:
        from src.core.queries.global_menu_diagnostics import (
            fetch_global_menu_diagnostics,
        )

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        update_global_menu_state(
            conn,
            menu_group_id="group-1",
            bootstrap_status="complete",
            catalog_revision=7,
            coverage_linked=9,
            coverage_total=10,
            history_cursor="history-page-2",
        )
        conn.execute(
            """
            INSERT INTO global_menu_items (
                global_menu_item_id, menu_group_id, canonical_name,
                canonical_type, is_verified, lifecycle_state, server_revision
            ) VALUES ('item-1', 'group-1', 'Vanilla', 'Ice Cream', 1, 'active', 7)
            """
        )
        conn.execute(
            """
            INSERT INTO global_variants (
                global_variant_id, menu_group_id, canonical_name, unit, value,
                is_verified, lifecycle_state, server_revision
            ) VALUES ('variant-1', 'group-1', 'Tub', 'GMS', 500, 1, 'active', 7)
            """
        )
        conn.execute(
            """
            INSERT INTO global_menu_mapping_rules (
                rule_id, menu_group_id, locator_scope, restaurant_id,
                locator_kind, locator_value, normalized_locator,
                target_global_menu_item_id, target_global_variant_id, price,
                provenance, is_verified, lifecycle_state, server_revision
            ) VALUES (
                'rule-1', 'group-1', 'restaurant', '1c8w7fp500', 'pos-item', 'pos-1', 'pos-1',
                'item-1', 'variant-1', NULL, 'operator', 1, 'active', 7
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_menu_history (
                history_id, menu_group_id, source_event_id, source_kind,
                event_type, occurred_at, server_ingested_at, is_undoable, detail
            ) VALUES (
                'history-1', 'group-1', 'event-1', 'global_menu_event',
                'global_item.merge', '2026-08-12T00:00:00Z',
                '2026-08-12T00:00:01Z', 0, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_menu_sync_quarantine (
                payload_key, menu_group_id, stream, payload,
                error_code, error_message
            ) VALUES ('bad-page', 'group-1', 'events', '{}', 'bad', 'bad page')
            """
        )
        conn.commit()

        first = fetch_global_menu_diagnostics(conn)
        second = fetch_global_menu_diagnostics(conn)
        self.assertEqual(first["bootstrap_state"], "complete")
        self.assertEqual(first["catalog_revision"], 7)
        self.assertEqual(first["mapping_count"], 1)
        self.assertEqual(first["price_count"], 0)
        self.assertEqual(
            first["assignment_coverage"],
            {"linked": 9, "total": 10, "complete": False},
        )
        self.assertEqual(first["history_count"], 1)
        self.assertEqual(first["history_cursor"], "history-page-2")
        self.assertEqual(first["quarantine_count"], 1)
        for key in ("catalog_digest", "matrix_digest", "history_digest"):
            self.assertRegex(first[key], r"^[0-9a-f]{64}$")
            self.assertEqual(first[key], second[key])
        from src.api.routers.menu import get_global_menu_status

        status_payload = get_global_menu_status(conn)
        self.assertIsNone(status_payload["menu_group_id"])
        self.assertEqual(status_payload["diagnostics"]["menu_group_id"], "group-1")
        conn.close()


class GlobalMenuCapabilityGuardTests(unittest.TestCase):
    def test_resolution_write_is_ready_before_coverage_is_complete(self) -> None:
        resolution_only = replace(
            capability(
                capabilities=("global_menu_v1", "global_menu_resolution_v1")
            ),
            coverage_linked=1,
            coverage_total=2,
        )
        self.assertTrue(resolution_only.resolution_ready)
        self.assertFalse(resolution_only.coverage_complete)
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=resolution_only,
        ):
            self.assertIs(
                require_global_menu_capability(
                    Mock(), for_write=True, allow_resolution_write=True
                ),
                resolution_only,
            )
            with self.assertRaises(GlobalMenuCapabilityError):
                require_global_menu_capability(Mock(), for_write=True)

    def test_a_member_without_mutations_blocks_legacy_canonical_writes(self) -> None:
        resolution_only = capability(
            capabilities=("global_menu_v1", "global_menu_resolution_v1")
        )
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=resolution_only,
        ):
            with self.assertRaises(HTTPException) as caught:
                _ensure_menu_edit_allowed(Mock())
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"], "global_menu_canonical_write_blocked"
        )

        from utils import menu_utils
        from utils.menu_utils import _strict_mode_edit_blocked_response

        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=resolution_only,
        ):
            blocked = _strict_mode_edit_blocked_response(Mock())
            replay = _strict_mode_edit_blocked_response(
                Mock(), emit_sync_event=False
            )
        self.assertEqual(blocked["code"], "global_menu_canonical_write_blocked")
        self.assertIsNone(replay)

        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=resolution_only,
        ):
            utility_blocked = menu_utils.update_menu_variant_mapping(
                Mock(), "item-1", "variant-1", "variant-2"
            )
        self.assertEqual(
            utility_blocked["code"], "global_menu_canonical_write_blocked"
        )

    def test_removed_capability_fails_closed_for_writes(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        conn.execute(
            """
            INSERT INTO restaurant_profile_identity
                (singleton_id, restaurant_id, bound_at, profile_schema_version)
            VALUES (1, 'rest-1', CURRENT_TIMESTAMP, 1)
            """
        )
        update_global_menu_state(
            conn,
            bootstrap_status="complete",
            coverage_linked=1,
            coverage_total=1,
        )
        with patch(
            "src.core.global_menu_schema._runtime_registry_entry",
            return_value=None,
        ):
            with self.assertRaises(GlobalMenuCapabilityError):
                require_global_menu_capability(conn, for_write=True)
        conn.close()

    def test_trusted_profile_sync_does_not_require_the_profile_to_be_selected(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        conn.execute(
            """
            INSERT INTO restaurant_profile_identity
                (singleton_id, restaurant_id, bound_at, profile_schema_version)
            VALUES (1, 'rest-1', CURRENT_TIMESTAMP, 1)
            """
        )
        entry = {
            "restaurant_id": "rest-1",
            "authorization_state": "authorized",
            "menu_group_id": "group-desserts",
            "menu_capabilities": ["global_menu_v1", "global_menu_resolution_v1"],
            "selected": False,
            "selection_mode": "all",
        }
        blocked = resolve_global_menu_capability(conn, registry_entry=entry)
        allowed = resolve_global_menu_capability(
            conn, registry_entry=entry, allow_profile_sync=True
        )
        self.assertFalse(blocked.active)
        self.assertIn("restaurant_not_selected", blocked.reason)
        self.assertTrue(allowed.active)
        conn.close()

    def test_global_ingest_uses_global_resolution_and_not_legacy_itemcode(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(conn)
        conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('global-owner', 'Canonical Cake', 'Dessert', 1)"
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES ('global-variant', 'Regular', 1)"
        )
        resolution_only = capability(
            capabilities=("global_menu_v1", "global_menu_resolution_v1")
        )
        resolution = GlobalIdentityResolution(
            True,
            local_menu_item_id="global-owner",
            local_variant_id="global-variant",
            global_menu_item_id="global-item",
            global_variant_id="global-variant-id",
            canonical_name="Canonical Cake",
            canonical_type="Dessert",
            provenance="restaurant-pos",
        )
        cluster = OrderItemCluster(conn)
        with patch(
            "services.clustering_service.resolve_global_menu_capability",
            return_value=resolution_only,
        ), patch(
            "services.clustering_service.resolve_global_identity_for_ingest",
            return_value=resolution,
        ) as global_resolver, patch(
            "services.clustering_service.observe_itemcode_assignment"
        ) as legacy_observer:
            match = cluster.add(
                "Store Cake",
                "pos-1001",
                itemcode="sku-cake",
                restaurant_id="rest-1",
            )
        self.assertEqual(match.menu_item_id, "global-owner")
        global_resolver.assert_called_once()
        legacy_observer.assert_not_called()
        conn.close()

    def test_legacy_manual_menu_pulls_are_blocked_in_global_mode(self) -> None:
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=capability(),
        ):
            with self.assertRaises(HTTPException) as caught:
                _ensure_legacy_menu_pull_allowed(Mock())
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"], "global_menu_legacy_pull_disabled"
        )
        not_ready = replace(
            capability(), mode="legacy_restaurant_v1", reason="projection_group_mismatch"
        )
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=not_ready,
        ):
            with self.assertRaises(HTTPException):
                _ensure_legacy_menu_pull_allowed(Mock())

    def test_global_preview_reference_never_falls_back_to_legacy_mutation(self) -> None:
        request = Mock(
            global_mutation_id="mutation-1",
            global_preview_digest="digest-1",
            global_menu_group_id="group-desserts",
            global_preview_revision=7,
            global_coverage_complete=True,
            global_mutation_type="global_item.merge",
            global_mutation_payload={"source_global_item_id": "a", "target_global_item_id": "b"},
        )
        with patch(
            "src.api.routers.menu._global_menu_active", return_value=False
        ):
            with self.assertRaises(HTTPException) as caught:
                _global_menu_request_active(Mock(), request)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"], "global_menu_capability_required"
        )


class GlobalMenuHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = contract_fixture("global_menu_history_page")["payload"]

    @staticmethod
    def _connection():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        return conn

    def _pull_fixture(self, conn) -> Dict[str, Any]:
        first_page = {
            **self.payload,
            "rows": self.payload["rows"][:1],
            "next_cursor": "opaque-history-page-2",
            "has_more": True,
        }
        second_page = {
            **self.payload,
            "rows": self.payload["rows"][1:],
            "next_cursor": None,
            "has_more": False,
        }

        def fetch(_conn, _endpoint, *, cursor, **_kwargs):
            page = first_page if cursor is None else second_page
            return {"error": None, **page}

        with patch(
            "src.core.global_menu_history.require_global_menu_capability",
            return_value=capability(group_id="group-1"),
        ), patch(
            "src.core.global_menu_history.get_global_menu_history_endpoint",
            return_value="https://cloud/global-menu/history",
        ), patch(
            "src.core.global_menu_history._fetch_page", side_effect=fetch
        ):
            return pull_global_menu_history(conn, auth="sync-key")

    def test_two_blank_profiles_hydrate_identical_ordered_history_idempotently(self) -> None:
        connections = [self._connection(), self._connection()]
        self.addCleanup(connections[0].close)
        self.addCleanup(connections[1].close)
        digests = []
        for conn in connections:
            first = self._pull_fixture(conn)
            second = self._pull_fixture(conn)
            self.assertEqual(
                first,
                {
                    "status": "applied",
                    "pages": 2,
                    "rows_applied": 2,
                    "rows_written": 2,
                    "rows_pruned": 0,
                },
            )
            # A caught-up install still reads the whole feed — no page proves
            # the pages below it are unchanged — but it writes nothing.
            self.assertEqual(
                second,
                {
                    "status": "applied",
                    "pages": 2,
                    "rows_applied": 2,
                    "rows_written": 0,
                    "rows_pruned": 0,
                },
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0],
                2,
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT history_cursor FROM global_menu_state WHERE singleton_id=1"
                ).fetchone()[0]
            )
            digests.append(
                [
                    tuple(row)
                    for row in conn.execute(
                        """
                        SELECT history_id, source_event_id, source_kind, event_type,
                               occurred_at, source, target, mutation_id, is_undoable, detail
                        FROM global_menu_history
                        ORDER BY occurred_at DESC, source_kind ASC, source_event_id DESC
                        """
                    ).fetchall()
                ]
            )
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(
            [row[0] for row in digests[0]],
            ["global:1", "legacy:9zz9zz9zz9:legacy-super-2"],
        )

    def _pull_restated_row(self, conn, index: int) -> Dict[str, Any]:
        """Re-serve the two-page feed with one row restated by the server.

        Both edits here are live projections the server recomputes per request:
        a global row's `is_undoable` follows the undo preview, and a legacy
        row's snapshot global id resolves once the catalog gains one. Either
        can move on any page without touching the pages above it.
        """
        changed = json.loads(json.dumps(self.payload))
        row = changed["rows"][index]
        if row["mutation_id"]:
            row["is_undoable"] = not row["is_undoable"]
        else:
            row["source"]["global_item_id"] = "1795ed65318544a9907caab6202b9d42"
        first_page = {
            **changed,
            "rows": changed["rows"][:1],
            "next_cursor": "opaque-history-page-2",
            "has_more": True,
        }
        second_page = {
            **changed,
            "rows": changed["rows"][1:],
            "next_cursor": None,
            "has_more": False,
        }

        def fetch(_conn, _endpoint, *, cursor, **_kwargs):
            page = first_page if cursor is None else second_page
            return {"error": None, **page}

        with patch(
            "src.core.global_menu_history.require_global_menu_capability",
            return_value=capability(group_id="group-1"),
        ), patch(
            "src.core.global_menu_history.get_global_menu_history_endpoint",
            return_value="https://cloud/global-menu/history",
        ), patch(
            "src.core.global_menu_history._fetch_page", side_effect=fetch
        ):
            result = pull_global_menu_history(conn, auth="sync-key")
        return {"result": result, "row": changed["rows"][index]}

    def test_a_changed_row_is_applied_from_any_page_of_the_feed(self) -> None:
        for index, position in ((0, "head page"), (1, "page below the head")):
            with self.subTest(position=position):
                conn = self._connection()
                self.addCleanup(conn.close)
                self.assertEqual(self._pull_fixture(conn)["rows_written"], 2)

                pulled = self._pull_restated_row(conn, index)

                # The drain reaches the changed row even when every page above
                # it was already cached unchanged.
                self.assertEqual(
                    pulled["result"],
                    {
                        "status": "applied",
                        "pages": 2,
                        "rows_applied": 2,
                        "rows_written": 1,
                        "rows_pruned": 0,
                    },
                )
                cached = conn.execute(
                    "SELECT is_undoable, source FROM global_menu_history WHERE history_id=?",
                    (pulled["row"]["history_id"],),
                ).fetchone()
                self.assertEqual(cached[0], int(pulled["row"]["is_undoable"]))
                self.assertEqual(
                    json.loads(cached[1])["global_item_id"],
                    pulled["row"]["source"]["global_item_id"],
                )
                self.assertIsNone(
                    conn.execute(
                        "SELECT history_cursor FROM global_menu_state WHERE singleton_id=1"
                    ).fetchone()[0]
                )

    def test_a_complete_drain_retires_rows_the_projection_stopped_serving(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        self.assertEqual(self._pull_fixture(conn)["rows_written"], 2)
        retired = self.payload["rows"][1]["history_id"]

        # A member leaving the group takes its legacy rows out of the page.
        remaining = json.loads(json.dumps(self.payload))
        remaining["rows"] = remaining["rows"][:1]
        remaining["next_cursor"] = None
        remaining["has_more"] = False

        with patch(
            "src.core.global_menu_history.require_global_menu_capability",
            return_value=capability(group_id="group-1"),
        ), patch(
            "src.core.global_menu_history.get_global_menu_history_endpoint",
            return_value="https://cloud/global-menu/history",
        ), patch(
            "src.core.global_menu_history._fetch_page",
            return_value={"error": None, **remaining},
        ):
            result = pull_global_menu_history(conn, auth="sync-key")

        self.assertEqual(
            result,
            {
                "status": "applied",
                "pages": 1,
                "rows_applied": 1,
                "rows_written": 0,
                "rows_pruned": 1,
            },
        )
        self.assertEqual(
            [
                row[0]
                for row in conn.execute(
                    "SELECT history_id FROM global_menu_history"
                ).fetchall()
            ],
            [self.payload["rows"][0]["history_id"]],
        )
        # Resolution History reads the cache, so the retired row must be gone
        # from what the user sees, not just from the pull's bookkeeping.
        active = capability(group_id="group-1")
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=active,
        ):
            history = get_merge_history(conn=conn)
        self.assertEqual(history["total"], 1)
        self.assertNotIn(
            retired, [entry["history_id"] for entry in history["entries"]]
        )

    def test_an_empty_page_never_blanks_the_cached_audit_view(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        self._pull_fixture(conn)
        empty = {**self.payload, "rows": [], "next_cursor": None, "has_more": False}

        with patch(
            "src.core.global_menu_history.require_global_menu_capability",
            return_value=capability(group_id="group-1"),
        ), patch(
            "src.core.global_menu_history.get_global_menu_history_endpoint",
            return_value="https://cloud/global-menu/history",
        ), patch(
            "src.core.global_menu_history._fetch_page",
            return_value={"error": None, **empty},
        ):
            result = pull_global_menu_history(conn, auth="sync-key")

        self.assertEqual(result["rows_pruned"], 0)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0], 2
        )

    def test_a_drain_cut_short_mid_feed_prunes_nothing(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        self.assertEqual(self._pull_fixture(conn)["rows_written"], 2)

        # Page 1 is served without the row page 2 carries, then the tail fails.
        # The unread tail and a retired row look identical from here, so the
        # cache must be left whole.
        first_page = {
            **self.payload,
            "rows": self.payload["rows"][:1],
            "next_cursor": "opaque-history-page-2",
            "has_more": True,
        }

        def fetch(_conn, _endpoint, *, cursor, **_kwargs):
            if cursor is None:
                return {"error": None, **first_page}
            return {"error": "history tail unreachable"}

        with patch(
            "src.core.global_menu_history.require_global_menu_capability",
            return_value=capability(group_id="group-1"),
        ), patch(
            "src.core.global_menu_history.get_global_menu_history_endpoint",
            return_value="https://cloud/global-menu/history",
        ), patch(
            "src.core.global_menu_history._fetch_page", side_effect=fetch
        ):
            result = pull_global_menu_history(conn, auth="sync-key")

        self.assertEqual(result["status"], "error")
        self.assertNotIn("rows_pruned", result)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0], 2
        )

    def test_a_drain_cut_short_mid_feed_hydrates_the_tail_on_the_next_pull(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        first_page = {
            **self.payload,
            "rows": self.payload["rows"][:1],
            "next_cursor": "opaque-history-page-2",
            "has_more": True,
        }
        second_page = {
            **self.payload,
            "rows": self.payload["rows"][1:],
            "next_cursor": None,
            "has_more": False,
        }

        def fetch_with_broken_tail(_conn, _endpoint, *, cursor, **_kwargs):
            if cursor is None:
                return {"error": None, **first_page}
            return {"error": "history tail unreachable"}

        def fetch(_conn, _endpoint, *, cursor, **_kwargs):
            page = first_page if cursor is None else second_page
            return {"error": None, **page}

        def pull(side_effect):
            with patch(
                "src.core.global_menu_history.require_global_menu_capability",
                return_value=capability(group_id="group-1"),
            ), patch(
                "src.core.global_menu_history.get_global_menu_history_endpoint",
                return_value="https://cloud/global-menu/history",
            ), patch(
                "src.core.global_menu_history._fetch_page", side_effect=side_effect
            ):
                return pull_global_menu_history(conn, auth="sync-key")

        interrupted = pull(fetch_with_broken_tail)
        self.assertEqual(interrupted["status"], "error")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0], 1
        )

        # The next drain must still reach the tail the failure left behind: an
        # unchanged head page is not evidence that the rest ever arrived.
        recovered = pull(fetch)
        self.assertEqual(
            recovered,
            {
                "status": "applied",
                "pages": 2,
                "rows_applied": 2,
                "rows_written": 1,
                "rows_pruned": 0,
            },
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0], 2
        )

    def test_malformed_page_retains_previous_cursor_and_data(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        first_row_page = {
            **self.payload,
            "rows": [self.payload["rows"][0]],
            "next_cursor": "opaque-history-page-2",
            "has_more": True,
        }
        apply_global_menu_history_page(
            conn,
            first_row_page,
            capability=capability(group_id="group-1"),
            page_cursor=None,
        )
        malformed = json.loads(json.dumps(self.payload))
        malformed["rows"][1]["is_undoable"] = "false"
        with patch(
            "src.core.global_menu_history.require_global_menu_capability",
            return_value=replace(
                capability(group_id="group-1"),
                history_cursor="opaque-history-page-2",
            ),
        ), patch(
            "src.core.global_menu_history.get_global_menu_history_endpoint",
            return_value="https://cloud/global-menu/history",
        ), patch(
            "src.core.global_menu_history._fetch_page",
            return_value={"error": None, **malformed},
        ):
            result = pull_global_menu_history(conn, auth="sync-key")

        self.assertEqual(result["status"], "error")
        self.assertIn("is_undoable must be a boolean", result["error"])
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0], 1
        )
        self.assertEqual(
            conn.execute(
                "SELECT history_cursor FROM global_menu_state WHERE singleton_id=1"
            ).fetchone()[0],
            "opaque-history-page-2",
        )

    def test_global_history_route_preserves_envelope_and_never_offers_false_undo(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        apply_global_menu_history_page(
            conn,
            self.payload,
            capability=capability(group_id="group-1"),
            page_cursor=None,
        )
        active = capability(group_id="group-1")
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=active,
        ):
            history = get_merge_history(conn=conn)

        self.assertEqual(
            set(history), {"entries", "total", "limit", "offset"}
        )
        self.assertEqual(history["total"], 2)
        global_row, legacy_row = history["entries"]
        self.assertTrue(global_row["is_undoable"])
        self.assertEqual(
            global_row["global_mutation_id"],
            "cd1e814c-ff28-4af2-9c82-d4e5226c26b2",
        )
        self.assertFalse(legacy_row["is_undoable"])
        self.assertIsNone(legacy_row["global_mutation_id"])
        self.assertLess(global_row["merge_id"], 2**53)

        resolution_only = capability(
            group_id="group-1",
            capabilities=("global_menu_v1", "global_menu_resolution_v1"),
        )
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=resolution_only,
        ):
            narrowed_history = get_merge_history(conn=conn)
        self.assertFalse(narrowed_history["entries"][0]["is_undoable"])
        self.assertIsNone(narrowed_history["entries"][0]["global_mutation_id"])

    def test_global_history_route_does_not_fall_back_on_capability_error(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            side_effect=RuntimeError("capability state is corrupt"),
        ):
            with self.assertRaisesRegex(RuntimeError, "capability state is corrupt"):
                get_merge_history(conn=conn)


class GlobalMenuProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO restaurant_profile_identity
                (singleton_id, restaurant_id, bound_at, profile_schema_version)
            VALUES (1, 'rest-1', CURRENT_TIMESTAMP, 1)
            """
        )
        self.fixture = json.loads(FIXTURE.read_text())

    def tearDown(self) -> None:
        self.conn.close()

    def test_snapshot_is_idempotent_and_uses_immutable_projection_owner(self) -> None:
        first = apply_global_menu_payload_page(
            self.conn, self.fixture, stream="snapshot", capability=capability(complete=False)
        )
        second = apply_global_menu_payload_page(
            self.conn, self.fixture, stream="snapshot", capability=capability(revision=7)
        )
        self.conn.commit()
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "applied")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM global_menu_items").fetchone()[0], 1)
        owner = self.conn.execute(
            "SELECT local_menu_item_id FROM menu_item_global_links WHERE is_projection_owner=1"
        ).fetchone()[0]
        self.assertEqual(global_ids_for_local(self.conn, owner)[0], "global-vanilla")
        state = self.conn.execute(
            "SELECT snapshot_cursor, event_cursor FROM global_menu_state WHERE singleton_id=1"
        ).fetchone()
        self.assertEqual(tuple(state), ("snapshot-7", "event-7"))

    def test_snapshot_does_not_implicitly_link_same_label_store_rows(self) -> None:
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('store-item', 'Eggless Vanilla Ice Cream', 'Ice Cream', 1)"
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, unit, value, is_verified) "
            "VALUES ('store-variant', 'Regular Tub', 'GMS', 300, 1)"
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified
            ) VALUES ('store-pos', 'store-item', 'store-variant', 1)
            """
        )
        apply_global_menu_payload_page(
            self.conn, self.fixture, stream="snapshot", capability=capability(complete=False)
        )
        self.assertEqual(
            global_ids_for_local(self.conn, "store-item", "store-variant"),
            (None, None),
        )
        owners = self.conn.execute(
            """
            SELECT l.local_menu_item_id, v.local_variant_id
            FROM menu_item_global_links l
            CROSS JOIN variant_global_links v
            WHERE l.is_projection_owner=1 AND v.is_projection_owner=1
            """
        ).fetchone()
        self.assertNotEqual(owners[0], "store-item")
        self.assertNotEqual(owners[1], "store-variant")

    def test_frozen_v17_snapshot_and_rules_fixtures_apply_without_translation_errors(self) -> None:
        item_page = contract_fixture("global_menu_snapshot_items")["payload"]
        item_result = apply_global_menu_payload_page(
            self.conn,
            item_page,
            stream="snapshot",
            capability=capability(complete=False, group_id="group-1"),
        )
        self.assertEqual(item_result["rows_applied"], 2)
        rules_page = contract_fixture("global_menu_snapshot_rules")["payload"]
        rule_result = apply_global_menu_payload_page(
            self.conn,
            rules_page,
            stream="snapshot",
            capability=capability(
                revision=0,
                complete=False,
                group_id="group-1",
                capabilities=(
                    "global_menu_v1",
                    "global_menu_resolution_v1",
                ),
            ),
        )
        self.assertEqual(rule_result["status"], "applied")
        rows = self.conn.execute(
            """
            SELECT locator_scope, restaurant_id, locator_kind,
                   target_global_menu_item_id
            FROM global_menu_mapping_rules
            ORDER BY locator_kind, locator_value
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                ("group", None, "itemcode", "1795ed65318544a9907caab6202b9d42"),
                ("restaurant", "1c8w7fp500", "pos-item", "1795ed65318544a9907caab6202b9d42"),
                ("restaurant", "1c8w7fp500", "pos-item", "9c6cc3eed9eb410b8540cfae2318fc10"),
            ],
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM menu_item_variants WHERE order_item_id='1001'"
            ).fetchone()
        )

    def test_snapshot_accepts_redirected_item_with_blank_canonical_type(self) -> None:
        item_page = json.loads(
            json.dumps(contract_fixture("global_menu_snapshot_items")["payload"])
        )
        item_page["rows"][0].update(
            {
                "global_item_id": "redirected-coconut-pineapple",
                "canonical_name": "Coconut Pineapple (110gm)",
                "canonical_type": "",
                "lifecycle_state": "redirected",
            }
        )

        result = apply_global_menu_payload_page(
            self.conn,
            item_page,
            stream="snapshot",
            capability=capability(complete=False, group_id="group-1"),
        )

        self.assertEqual(result["rows_applied"], 2)
        row = self.conn.execute(
            """
            SELECT canonical_name, canonical_type, lifecycle_state
            FROM global_menu_items
            WHERE global_menu_item_id='redirected-coconut-pineapple'
            """
        ).fetchone()
        self.assertEqual(
            tuple(row),
            ("Coconut Pineapple (110gm)", "", "redirected"),
        )

    def test_snapshot_still_rejects_item_with_blank_canonical_name(self) -> None:
        item_page = json.loads(
            json.dumps(contract_fixture("global_menu_snapshot_items")["payload"])
        )
        item_page["rows"][0]["canonical_name"] = ""

        with self.assertRaisesRegex(RuntimeError, "missing canonical name"):
            apply_global_menu_payload_page(
                self.conn,
                item_page,
                stream="snapshot",
                capability=capability(complete=False, group_id="group-1"),
            )

    def test_frozen_v16_event_fixture_uses_event_sequence_cursor(self) -> None:
        page = contract_fixture("global_menu_events_page")["payload"]
        result = apply_global_menu_payload_page(
            self.conn,
            page,
            stream="events",
            capability=capability(
                complete=False,
                group_id="group-1",
                capabilities=(
                    "global_menu_v1",
                    "global_menu_resolution_v1",
                                ),
            ),
        )
        self.assertEqual(result["rows_applied"], 1)
        self.assertEqual(result["next_cursor"], "1")
        event = self.conn.execute(
            "SELECT event_id, mutation_id, catalog_revision FROM global_menu_events"
        ).fetchone()
        self.assertEqual(tuple(event), (
            "global-event:1",
            "cd1e814c-ff28-4af2-9c82-d4e5226c26b2",
            1,
        ))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0],
            0,
        )

    def test_v14_undo_tombstones_remove_redirects_and_rules(self) -> None:
        merge_page = contract_fixture("global_menu_events_page")["payload"]
        apply_global_menu_payload_page(
            self.conn,
            merge_page,
            stream="events",
            capability=capability(
                complete=False,
                group_id="group-1",
                capabilities=(
                    "global_menu_v1",
                    "global_menu_resolution_v1",
                                ),
            ),
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM global_menu_redirects").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM global_menu_mapping_rules"
            ).fetchone()[0],
            2,
        )

        source_id = "1795ed65318544a9907caab6202b9d42"
        undo_page = {
            "schema_version": 1,
            "menu_group_id": "group-1",
            "menu_group_revision": 2,
            "event_head_seq": 2,
            "events": [
                {
                    "event_seq": 2,
                    "menu_group_revision": 2,
                    "event_type": "global_menu.undo",
                    "entity_type": "",
                    "mutation_id": "undo-mutation-2",
                    "origin_restaurant_id": "1c8w7fp500",
                    "actor": "ops",
                    "occurred_at": "2026-08-09T08:10:00+00:00",
                    "server_ingested_at": "2026-08-09T08:10:00+00:00",
                    "payload": {
                        "action": {
                            "mutation_type": "global_menu.undo",
                            "undo_mutation_id": (
                                "cd1e814c-ff28-4af2-9c82-d4e5226c26b2"
                            ),
                        },
                        "items": [
                            {
                                "global_item_id": source_id,
                                "canonical_name": "Vanilla Ice Cream",
                                "canonical_type": "Dessert",
                                "is_verified": False,
                                "lifecycle_state": "active",
                                "updated_at": "2026-08-09T08:10:00+00:00",
                            }
                        ],
                        "variants": [],
                        "redirects": [],
                        "mapping_rules": [],
                        "tombstones": {
                            "redirects": [
                                {
                                    "entity_type": "item",
                                    "source_global_id": source_id,
                                }
                            ],
                            "mapping_rules": [
                                {
                                    "rule_scope": "restaurant",
                                    "restaurant_id": "1c8w7fp500",
                                    "locator_type": "pos_item",
                                    "locator_value": "1001",
                                },
                                {
                                    "rule_scope": "group",
                                    "restaurant_id": "",
                                    "locator_type": "itemcode",
                                    "locator_value": "IC-VAN",
                                },
                            ],
                        },
                        "assignment_snapshot_required": True,
                        "assignment_restaurants": [
                            "1c8w7fp500",
                            "9zz9zz9zz9",
                        ],
                        "menu_group_revision": 2,
                    },
                }
            ],
            "next_cursor": 2,
            "has_more": False,
        }
        apply_global_menu_payload_page(
            self.conn,
            undo_page,
            stream="events",
            capability=capability(
                revision=1,
                group_id="group-1",
                capabilities=(
                    "global_menu_v1",
                    "global_menu_resolution_v1",
                                ),
            ),
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM global_menu_redirects").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM global_menu_mapping_rules"
            ).fetchone()[0],
            0,
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM menu_item_variants WHERE order_item_id='1001'"
            ).fetchone()
        )
        lifecycle = self.conn.execute(
            "SELECT lifecycle_state FROM global_menu_items "
            "WHERE global_menu_item_id=?",
            (source_id,),
        ).fetchone()[0]
        self.assertEqual(lifecycle, "active")

    def test_frozen_v17_status_fixture_uses_an_unpaged_status_request(self) -> None:
        page = contract_fixture("global_menu_status")["payload"]
        cap = capability(
            restaurant_id="1c8w7fp500",
            group_id="group-1",
            complete=False,
            capabilities=(
                "global_menu_v1",
                "global_menu_resolution_v1",
                            "global_menu_aggregation_v1",
                "global_menu_mutations_v1",
            ),
        )
        with patch(
            "src.core.global_menu_sync.require_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.get_global_menu_status_endpoint",
            return_value="https://cloud/global-menu/status",
        ), patch(
            "src.core.global_menu_sync._fetch_page",
            return_value={"error": None, **page},
        ) as transport:
            result = pull_global_menu_status(self.conn, auth="sync-key")
        self.assertEqual(result["status"], "applied")
        self.assertIsNone(transport.call_args.kwargs["limit"])
        state = self.conn.execute(
            "SELECT coverage_linked, coverage_total FROM global_menu_state WHERE singleton_id=1"
        ).fetchone()
        self.assertEqual(tuple(state), (2, 2))

    def test_snapshot_restarts_if_the_pinned_watermark_moves(self) -> None:
        first_page = contract_fixture("global_menu_snapshot_items")["payload"]
        moved_page = {
            **first_page,
            "rows": [],
            "menu_group_revision": 1,
            "snapshot_watermark": {
                "event_seq": 1,
                "menu_group_revision": 1,
            },
            "section": "variants",
        }
        cap = capability(complete=False, group_id="group-1")
        with patch(
            "src.core.global_menu_sync.require_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.resolve_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.pull_global_menu_status",
            return_value={"status": "applied"},
        ), patch(
            "src.core.global_menu_sync.get_cloud_sync_config",
            return_value=("https://cloud", "key"),
        ), patch(
            "src.core.global_menu_sync.get_global_menu_snapshot_endpoint",
            return_value="https://cloud/snapshot",
        ), patch(
            "src.core.global_menu_sync.get_global_menu_events_endpoint",
            return_value="https://cloud/events",
        ), patch(
            "src.core.global_menu_sync._fetch_page",
            side_effect=[
                {"error": None, **first_page},
                {"error": None, **moved_page},
            ],
        ):
            result = pull_global_menu_state(self.conn)
        self.assertEqual(result["status"], "error")
        self.assertIn("changed during snapshot", result["error"])
        state = self.conn.execute(
            "SELECT bootstrap_status, snapshot_cursor FROM global_menu_state "
            "WHERE singleton_id=1"
        ).fetchone()
        self.assertEqual(tuple(state), ("error", None))

    def test_cycle_rejected_without_advancing_prior_revision_or_cursor(self) -> None:
        payload = dict(self.fixture)
        payload["items"] = [
            *self.fixture["items"],
            {
                "global_menu_item_id": "global-chocolate",
                "canonical_name": "Chocolate Ice Cream",
                "canonical_type": "Ice Cream",
                "server_revision": 7,
            },
        ]
        payload["redirects"] = [
            {
                "redirect_id": "r1",
                "entity_type": "item",
                "source_global_menu_item_id": "global-vanilla",
                "target_global_menu_item_id": "global-chocolate",
                "server_revision": 7,
            },
            {
                "redirect_id": "r2",
                "entity_type": "item",
                "source_global_menu_item_id": "global-chocolate",
                "target_global_menu_item_id": "global-vanilla",
                "server_revision": 7,
            },
        ]
        with self.assertRaises(GlobalMenuIdentityError):
            apply_global_menu_payload_page(
                self.conn, payload, stream="snapshot", capability=capability(complete=False)
            )
        row = self.conn.execute(
            "SELECT catalog_revision, snapshot_cursor FROM global_menu_state WHERE singleton_id=1"
        ).fetchone()
        self.assertEqual(int(row[0]), 0)
        self.assertIsNone(row[1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM global_menu_redirects").fetchone()[0], 0)

    def test_group_mismatch_and_stale_revision_fail_closed(self) -> None:
        mismatch = dict(self.fixture)
        mismatch["menu_group_id"] = "other-group"
        with self.assertRaisesRegex(RuntimeError, "Cross-group"):
            apply_global_menu_payload_page(
                self.conn, mismatch, stream="snapshot", capability=capability(complete=False)
            )
        stale = dict(self.fixture)
        stale["catalog_revision"] = 6
        result = apply_global_menu_payload_page(
            self.conn, stale, stream="snapshot", capability=capability(revision=7)
        )
        self.assertEqual(result["status"], "stale")

        unsupported = dict(self.fixture)
        unsupported["schema_version"] = 2
        with self.assertRaisesRegex(RuntimeError, "Unsupported global menu schema"):
            apply_global_menu_payload_page(
                self.conn,
                unsupported,
                stream="snapshot",
                capability=capability(complete=False),
            )

    def test_redirect_chain_and_event_replay_update_canonical_projection_once(self) -> None:
        snapshot = json.loads(json.dumps(self.fixture))
        snapshot["items"].append(
            {
                "global_menu_item_id": "global-chocolate",
                "canonical_name": "Chocolate Ice Cream",
                "canonical_type": "Ice Cream",
                "is_verified": True,
                "server_revision": 7,
            }
        )
        snapshot["redirects"] = [
            {
                "redirect_id": "redirect-vanilla-chocolate",
                "entity_type": "item",
                "source_global_menu_item_id": "global-vanilla",
                "target_global_menu_item_id": "global-chocolate",
                "server_revision": 7,
            }
        ]
        apply_global_menu_payload_page(
            self.conn, snapshot, stream="snapshot", capability=capability(complete=False)
        )
        self.assertEqual(
            resolve_redirect_chain(self.conn, "item", "global-vanilla"),
            "global-chocolate",
        )

        event_page = {
            "schema_version": 1,
            "menu_group_id": "group-desserts",
            "catalog_revision": 8,
            "mutation_revision": 8,
            "next_cursor": "event-8",
            "has_more": False,
            "coverage": {"linked": 2, "total": 2},
            "events": [
                {
                    "event_id": "event-8",
                    "mutation_id": "mutation-8",
                    "event_type": "global_menu.rename",
                    "catalog_revision": 8,
                    "origin_restaurant_id": "rest-2",
                    "payload": {
                        "items": [
                            {
                                "global_menu_item_id": "global-chocolate",
                                "canonical_name": "Dark Chocolate Ice Cream",
                                "canonical_type": "Ice Cream",
                                "is_verified": True,
                                "server_revision": 8,
                            }
                        ],
                        "action": {
                            "source": {"global_menu_item_id": "global-vanilla"},
                            "target": {"global_menu_item_id": "global-chocolate"},
                        },
                        "tombstones": {"redirects": [], "mapping_rules": []},
                    },
                }
            ],
        }
        first = apply_global_menu_payload_page(
            self.conn, event_page, stream="events", capability=capability(revision=7)
        )
        second = apply_global_menu_payload_page(
            self.conn, event_page, stream="events", capability=capability(revision=8)
        )
        owner = self.conn.execute(
            """
            SELECT m.name FROM menu_items m
            JOIN menu_item_global_links l ON l.local_menu_item_id=m.menu_item_id
            WHERE l.global_menu_item_id='global-chocolate' AND l.is_projection_owner=1
            """
        ).fetchone()
        self.assertEqual(first["rows_applied"], 1)
        self.assertEqual(second["rows_applied"], 0)
        self.assertEqual(owner[0], "Dark Chocolate Ice Cream")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM global_menu_events").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM global_menu_history").fetchone()[0],
            0,
        )

    def test_pull_quarantines_cycle_and_keeps_good_cursor(self) -> None:
        cycle = dict(self.fixture)
        cycle["items"] = [
            *self.fixture["items"],
            {
                "global_menu_item_id": "global-chocolate",
                "canonical_name": "Chocolate Ice Cream",
                "canonical_type": "Ice Cream",
                "server_revision": 7,
            },
        ]
        cycle["redirects"] = [
            {
                "redirect_id": "r1",
                "entity_type": "item",
                "source_global_menu_item_id": "global-vanilla",
                "target_global_menu_item_id": "global-chocolate",
                "server_revision": 7,
            },
            {
                "redirect_id": "r2",
                "entity_type": "item",
                "source_global_menu_item_id": "global-chocolate",
                "target_global_menu_item_id": "global-vanilla",
                "server_revision": 7,
            },
        ]
        with patch("src.core.global_menu_sync.require_global_menu_capability", return_value=capability(complete=False)), patch(
            "src.core.global_menu_sync.resolve_global_menu_capability", return_value=capability(complete=False)
        ), patch(
            "src.core.global_menu_sync.pull_global_menu_status",
            return_value={"status": "applied"},
        ), patch("src.core.global_menu_sync.get_cloud_sync_config", return_value=("https://cloud", "key")), patch(
            "src.core.global_menu_sync.get_global_menu_snapshot_endpoint", return_value="https://cloud/snapshot"
        ), patch("src.core.global_menu_sync.get_global_menu_events_endpoint", return_value="https://cloud/events"), patch(
            "src.core.global_menu_sync._fetch_page", return_value={"error": None, **cycle}
        ):
            result = pull_global_menu_state(self.conn)
        self.assertEqual(result["status"], "error")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM global_menu_sync_quarantine WHERE resolved_at IS NULL"
            ).fetchone()[0],
            1,
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT snapshot_cursor FROM global_menu_state WHERE singleton_id=1"
            ).fetchone()[0]
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT bootstrap_status FROM global_menu_state WHERE singleton_id=1"
            ).fetchone()[0],
            "error",
        )

    def test_two_restaurant_pos_ids_resolve_but_historical_alias_is_inert(self) -> None:
        for restaurant_id, pos_id in (("rest-1", "1001"), ("rest-2", "8442")):
            conn = self.conn if restaurant_id == "rest-1" else sqlite3.connect(":memory:")
            if conn is not self.conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                apply_analytics_schema(conn)
            apply_global_menu_payload_page(
                conn,
                self.fixture,
                stream="snapshot",
                capability=capability(restaurant_id, complete=False),
            )
            with patch(
                "src.core.global_menu_identity.resolve_global_menu_capability",
                return_value=capability(restaurant_id),
            ):
                resolution = resolve_global_identity_for_ingest(
                    conn,
                    order_item_id=pos_id,
                    raw_name="POS label",
                )
                historical_alias = resolve_global_identity_for_ingest(
                    conn,
                    order_item_id=f"new-{restaurant_id}",
                    raw_name="Vanilla---Ice Cream",
                )
                unknown = resolve_global_identity_for_ingest(
                    conn,
                    order_item_id=f"unknown-{restaurant_id}",
                    raw_name="Vanila Ice Creme",
                )
            self.assertEqual(resolution.global_menu_item_id, "global-vanilla")
            self.assertFalse(historical_alias.resolved)
            self.assertFalse(unknown.resolved)
            if conn is not self.conn:
                conn.close()

    def test_alias_retirement_tombstone_removes_a_cached_historical_rule(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM global_menu_mapping_rules "
                "WHERE locator_kind='alias'"
            ).fetchone()[0],
            1,
        )
        alias_identity = {
            "rule_scope": "group",
            "restaurant_id": "",
            "locator_type": "alias",
            "locator_value": "vanilla ice cream",
        }
        self.conn.execute(
            "UPDATE global_menu_mapping_rules SET rule_id=? "
            "WHERE locator_kind='alias'",
            (_contract_rule_id(alias_identity, "group-desserts"),),
        )
        event_page = {
            "schema_version": 1,
            "menu_group_id": "group-desserts",
            "menu_group_revision": 8,
            "event_head_seq": 8,
            "events": [
                {
                    "event_seq": 8,
                    "menu_group_revision": 8,
                    "event_type": "global_mapping_rules.aliases_retired",
                    "entity_type": "mapping_rule",
                    "mutation_id": "migration-0034-test",
                    "origin_restaurant_id": "",
                    "actor": "system:migration-0034",
                    "occurred_at": "2026-08-14T00:00:00+00:00",
                    "server_ingested_at": "2026-08-14T00:00:00+00:00",
                    "payload": {
                        "action": {
                            "kind": "system_rule_retirement",
                            "reason": "display_alias_retired_1_10",
                        },
                        "items": [],
                        "variants": [],
                        "redirects": [],
                        "mapping_rules": [],
                        "tombstones": {
                            "redirects": [],
                            "mapping_rules": [
                                alias_identity
                            ],
                        },
                        "assignment_snapshot_required": False,
                        "assignment_restaurants": [],
                        "menu_group_revision": 8,
                    },
                }
            ],
            "next_cursor": 8,
            "has_more": False,
        }
        apply_global_menu_payload_page(
            self.conn,
            event_page,
            stream="events",
            capability=capability(revision=7, complete=False),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM global_menu_mapping_rules "
                "WHERE locator_kind='alias'"
            ).fetchone()[0],
            0,
        )

    def test_historical_itemcode_variant_target_is_discarded(self) -> None:
        snapshot = json.loads(json.dumps(self.fixture))
        snapshot["mapping_rules"].append(
            {
                "rule_id": "legacy-itemcode-rule",
                "locator_scope": "group",
                "restaurant_id": None,
                "locator_kind": "itemcode",
                "locator_value": "IC-VAN",
                "normalized_locator": "IC-VAN",
                "target_global_menu_item_id": "global-vanilla",
                "target_global_variant_id": "global-regular",
                "provenance": "legacy",
                "is_verified": True,
                "server_revision": 7,
            }
        )
        apply_global_menu_payload_page(
            self.conn,
            snapshot,
            stream="snapshot",
            capability=capability(complete=False),
        )
        stored = self.conn.execute(
            "SELECT target_global_variant_id FROM global_menu_mapping_rules "
            "WHERE rule_id='legacy-itemcode-rule'"
        ).fetchone()
        self.assertIsNone(stored[0])
        with patch(
            "src.core.global_menu_identity.resolve_global_menu_capability",
            return_value=capability(),
        ):
            resolution = resolve_global_identity_for_ingest(
                self.conn,
                order_item_id="new-pos-id",
                raw_name="Anything",
                itemcode="IC-VAN",
            )
        self.assertEqual(resolution.global_menu_item_id, "global-vanilla")
        self.assertIsNone(resolution.global_variant_id)
        self.assertEqual(resolution.provenance, "group-itemcode")

    def test_restaurant_pos_rule_completes_a_parent_only_assignment(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        owner = self.conn.execute(
            "SELECT local_menu_item_id FROM menu_item_global_links "
            "WHERE global_menu_item_id='global-vanilla' AND is_projection_owner=1"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name) "
            "VALUES ('local-unlinked-mini', 'Mini')"
        )
        self.conn.execute(
            "INSERT INTO menu_item_variants "
            "(order_item_id, menu_item_id, variant_id, is_verified) "
            "VALUES ('1001', ?, 'local-unlinked-mini', 1)",
            (owner,),
        )
        with patch(
            "src.core.global_menu_identity.resolve_global_menu_capability",
            return_value=capability(),
        ):
            resolution = resolve_global_identity_for_ingest(
                self.conn,
                order_item_id="1001",
                raw_name="Vanilla",
                itemcode="IC-VAN",
            )
        self.assertEqual(resolution.global_menu_item_id, "global-vanilla")
        self.assertEqual(resolution.global_variant_id, "global-regular")
        self.assertEqual(resolution.provenance, "restaurant-pos")

    def test_assignment_adapter_preserves_price_and_existing_seq_guard(self) -> None:
        apply_global_menu_payload_page(
            self.conn, self.fixture, stream="snapshot", capability=capability(complete=False)
        )
        owner = self.conn.execute(
            "SELECT local_menu_item_id FROM menu_item_global_links WHERE is_projection_owner=1"
        ).fetchone()[0]
        variant = self.conn.execute(
            "SELECT local_variant_id FROM variant_global_links WHERE is_projection_owner=1"
        ).fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, price, is_verified, assignment_seq
            ) VALUES ('1001', ?, ?, 299, 0, 4)
            """,
            (owner, variant),
        )
        result = apply_global_assignment_rows(
            self.conn,
            [
                {
                    "order_item_id": "1001",
                    "global_menu_item_id": "global-vanilla",
                    "global_variant_id": "global-regular",
                    "is_verified": True,
                    "assignment_seq": 9,
                }
            ],
        )
        row = self.conn.execute(
            "SELECT price, assignment_seq, is_verified FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        self.assertEqual(result["rows_applied"], 1)
        self.assertEqual(float(row[0]), 299.0)
        self.assertEqual(int(row[1]), 4)
        self.assertEqual(int(row[2]), 1)

    def test_legacy_blanket_sequence_is_recovered_for_projection_only(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('legacy-item', 'Legacy Vanilla', 'Ice Cream', 1)"
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES ('legacy-variant', 'Legacy Regular', 1)"
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, price, is_verified,
                assignment_seq, pending_local
            ) VALUES ('1001', 'legacy-item', 'legacy-variant', 299, 1, 668, 0)
            """
        )
        assignment = {
            "order_item_id": "1001",
            "menu_item_id": "legacy-item",
            "variant_id": "legacy-variant",
            "global_menu_item_id": "global-vanilla",
            "global_variant_id": "global-regular",
            "is_verified": 1,
            "last_seq": 25,
            "last_verification_seq": 25,
        }

        first = apply_global_assignment_rows(
            self.conn, [assignment], server_revision=7
        )
        projected = self.conn.execute(
            """
            SELECT menu_item_id, variant_id, price, assignment_seq, pending_local
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()
        self.assertEqual(first["rows_applied"], 1)
        self.assertEqual(first["rows_stale"], 0)
        self.assertEqual(first["rows_recovered_legacy_watermark"], 1)
        self.assertEqual(
            global_ids_for_local(self.conn, projected[0], projected[1]),
            ("global-vanilla", "global-regular"),
        )
        self.assertEqual(float(projected[2]), 299.0)
        self.assertEqual(int(projected[3]), 668)
        self.assertEqual(int(projected[4]), 0)

        # The blanket sequence remains a safe future-event guard, while an
        # unchanged snapshot recognizes the already-correct projection as a
        # no-op rather than reporting it stale or recovering it again.
        second = apply_global_assignment_rows(
            self.conn, [assignment], server_revision=7
        )
        rerun = self.conn.execute(
            """
            SELECT menu_item_id, variant_id, price, assignment_seq, pending_local
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()
        self.assertEqual(second["rows_stale"], 0)
        self.assertEqual(second["rows_recovered_legacy_watermark"], 0)
        self.assertEqual(tuple(rerun), tuple(projected))

    def test_reviewed_pos_rule_moves_an_older_global_projection(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        owner = self.conn.execute(
            "SELECT local_menu_item_id FROM menu_item_global_links "
            "WHERE global_menu_item_id='global-vanilla' AND is_projection_owner=1"
        ).fetchone()[0]
        old_variant = self.conn.execute(
            "SELECT local_variant_id FROM variant_global_links "
            "WHERE global_variant_id='global-regular' AND is_projection_owner=1"
        ).fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            ) VALUES ('1001', ?, ?, 0, 668, 0)
            """,
            (owner, old_variant),
        )
        self.conn.execute(
            """
            INSERT INTO global_variants (
                global_variant_id, menu_group_id, canonical_name, unit, value,
                lifecycle_state, server_revision
            ) VALUES ('global-mini', 'group-desserts', 'Mini Tub', 'GMS', 160,
                      'active', 8)
            """
        )
        ensure_variant_projection_owner(self.conn, "global-mini")
        self.conn.execute(
            """
            UPDATE global_menu_mapping_rules
            SET target_global_variant_id='global-mini', server_revision=8
            WHERE restaurant_id='rest-1' AND locator_kind='pos-item'
              AND locator_value='1001'
            """
        )

        result = apply_global_assignment_rows(
            self.conn,
            [{
                "order_item_id": "1001",
                "global_menu_item_id": "global-vanilla",
                "global_variant_id": "global-mini",
                "last_seq": 25,
            }],
            server_revision=8,
            capability=capability(revision=8, complete=False),
        )
        row = self.conn.execute(
            """
            SELECT menu_item_id, variant_id, assignment_seq, pending_local
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()

        self.assertEqual(result["rows_applied"], 1)
        self.assertEqual(result["rows_stale"], 0)
        self.assertEqual(result["rows_recovered_reviewed_locator"], 1)
        self.assertEqual(
            global_ids_for_local(self.conn, row[0], row[1]),
            ("global-vanilla", "global-mini"),
        )
        self.assertEqual(tuple(row[2:]), (668, 0))

    def test_a_peer_rule_for_the_same_pos_id_never_moves_this_restaurant(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        owner = self.conn.execute(
            "SELECT local_menu_item_id FROM menu_item_global_links "
            "WHERE global_menu_item_id='global-vanilla' AND is_projection_owner=1"
        ).fetchone()[0]
        old_variant = self.conn.execute(
            "SELECT local_variant_id FROM variant_global_links "
            "WHERE global_variant_id='global-regular' AND is_projection_owner=1"
        ).fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            ) VALUES ('1001', ?, ?, 0, 668, 0)
            """,
            (owner, old_variant),
        )
        self.conn.execute(
            """
            INSERT INTO global_variants (
                global_variant_id, menu_group_id, canonical_name, unit, value,
                lifecycle_state, server_revision
            ) VALUES ('global-mini', 'group-desserts', 'Mini Tub', 'GMS', 160,
                      'active', 8)
            """
        )
        ensure_variant_projection_owner(self.conn, "global-mini")
        # The peer restaurant sells a different product under the same numeric
        # Petpooja id, so its reviewed rule must never authorize a move here.
        self.conn.execute(
            """
            INSERT INTO global_menu_mapping_rules (
                rule_id, menu_group_id, locator_scope, restaurant_id,
                locator_kind, locator_value, normalized_locator,
                target_global_menu_item_id, target_global_variant_id,
                provenance, is_verified, lifecycle_state, server_revision
            ) VALUES ('rule-rest-2-collides', 'group-desserts', 'restaurant',
                      'rest-2', 'pos-item', '1001', '1001', 'global-vanilla',
                      'global-mini', 'reviewed-pos', 1, 'active', 8)
            """
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM global_menu_mapping_rules "
                "WHERE locator_kind='pos-item' AND normalized_locator='1001'"
            ).fetchone()[0],
            2,
        )

        result = apply_global_assignment_rows(
            self.conn,
            [{
                "order_item_id": "1001",
                "global_menu_item_id": "global-vanilla",
                "global_variant_id": "global-mini",
                "last_seq": 25,
            }],
            server_revision=8,
            capability=capability(revision=8, complete=False),
        )
        row = self.conn.execute(
            """
            SELECT menu_item_id, variant_id, assignment_seq, pending_local
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()

        self.assertEqual(result["rows_recovered_reviewed_locator"], 0)
        self.assertEqual(result["rows_applied"], 0)
        self.assertEqual(result["rows_stale"], 1)
        self.assertEqual(
            global_ids_for_local(self.conn, row[0], row[1]),
            ("global-vanilla", "global-regular"),
        )
        self.assertEqual(tuple(row[2:]), (668, 0))
        self.assertEqual(
            self.conn.execute(
                "SELECT target_global_variant_id FROM global_menu_mapping_rules "
                "WHERE rule_id='rule-rest-1-pos'"
            ).fetchone()[0],
            "global-regular",
        )

        # Control: the identical payload is authorized once the selected
        # restaurant is the one that owns the colliding rule.
        peer = apply_global_assignment_rows(
            self.conn,
            [{
                "order_item_id": "1001",
                "global_menu_item_id": "global-vanilla",
                "global_variant_id": "global-mini",
                "last_seq": 25,
            }],
            server_revision=8,
            capability=capability("rest-2", revision=8, complete=False),
        )
        self.assertEqual(peer["rows_recovered_reviewed_locator"], 1)

    def test_pending_local_row_is_not_legacy_recovered(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('pending-item', 'Pending Vanilla', 'Ice Cream', 1)"
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES ('pending-variant', 'Pending Regular', 1)"
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            ) VALUES ('1001', 'pending-item', 'pending-variant', 1, 668, 1)
            """
        )
        result = apply_global_assignment_rows(
            self.conn,
            [{
                "order_item_id": "1001",
                "menu_item_id": "pending-item",
                "variant_id": "pending-variant",
                "global_menu_item_id": "global-vanilla",
                "global_variant_id": "global-regular",
                "last_seq": 25,
            }],
            server_revision=7,
        )
        row = self.conn.execute(
            """
            SELECT menu_item_id, variant_id, assignment_seq, pending_local
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()
        self.assertEqual(result["rows_applied"], 0)
        self.assertEqual(result["rows_stale"], 1)
        self.assertEqual(result["rows_recovered_legacy_watermark"], 0)
        self.assertEqual(tuple(row), ("pending-item", "pending-variant", 668, 1))

    def test_newer_semantic_assignment_remains_sequence_protected(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES (?, ?, 'Ice Cream', 1)",
            [
                ("central-item", "Central Vanilla"),
                ("newer-item", "Newer Chocolate"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO variants (variant_id, variant_name, is_verified) VALUES (?, ?, 1)",
            [
                ("central-variant", "Central Regular"),
                ("newer-variant", "Newer Large"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            ) VALUES ('1001', 'newer-item', 'newer-variant', 1, 668, 0)
            """
        )
        result = apply_global_assignment_rows(
            self.conn,
            [{
                "order_item_id": "1001",
                "menu_item_id": "central-item",
                "variant_id": "central-variant",
                "global_menu_item_id": "global-vanilla",
                "global_variant_id": "global-regular",
                "last_seq": 25,
            }],
            server_revision=7,
        )
        row = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        self.assertEqual(result["rows_applied"], 0)
        self.assertEqual(result["rows_stale"], 1)
        self.assertEqual(result["rows_recovered_legacy_watermark"], 0)
        self.assertEqual(tuple(row), ("newer-item", "newer-variant"))

    def test_assignment_snapshot_reconciles_linked_rows_back_to_unlinked(self) -> None:
        apply_global_menu_payload_page(
            self.conn, self.fixture, stream="snapshot", capability=capability(complete=False)
        )
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('store-item', 'Store Vanilla', 'Dessert', 1)"
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, unit, value, is_verified) "
            "VALUES ('store-variant', 'Store Tub', 'GMS', 300, 1)"
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, price, is_verified,
                assignment_seq, verification_seq
            ) VALUES ('1001', 'store-item', 'store-variant', 299, 1, 4, 4)
            """
        )
        apply_global_assignment_rows(
            self.conn,
            [{
                "order_item_id": "1001",
                "menu_item_id": "store-item",
                "variant_id": "store-variant",
                "global_menu_item_id": "global-vanilla",
                "global_variant_id": "global-regular",
                "is_verified": 1,
                "last_seq": 9,
                "last_verification_seq": 9,
            }],
            server_revision=7,
        )
        linked = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        self.assertEqual(global_ids_for_local(self.conn, linked[0], linked[1]), (
            "global-vanilla", "global-regular"
        ))

        variant_unlink = apply_global_assignment_rows(
            self.conn,
            [{
                "order_item_id": "1001",
                "menu_item_id": "store-item",
                "variant_id": "store-variant",
                "global_menu_item_id": "global-vanilla",
                "is_verified": 1,
                "last_seq": 9,
                "last_verification_seq": 9,
            }],
            server_revision=8,
        )
        partly_linked = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        self.assertEqual(global_ids_for_local(
            self.conn, partly_linked[0], partly_linked[1]
        ), ("global-vanilla", None))
        self.assertEqual(variant_unlink["rows_applied"], 1)

        unlinked = apply_global_assignment_rows(
            self.conn,
            [{
                "order_item_id": "1001",
                "menu_item_id": "store-item",
                "variant_id": "store-variant",
                "is_verified": 1,
                "last_seq": 9,
                "last_verification_seq": 9,
            }],
            server_revision=9,
        )
        local = self.conn.execute(
            "SELECT menu_item_id, variant_id, price FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        self.assertEqual(global_ids_for_local(self.conn, local[0], local[1]), (None, None))
        self.assertEqual(float(local[2]), 299.0)
        self.assertEqual(unlinked["rows_unlinked"], 1)

    def test_assignment_move_does_not_overwrite_newer_verification(self) -> None:
        apply_global_menu_payload_page(
            self.conn, self.fixture, stream="snapshot", capability=capability(complete=False)
        )
        self.conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('store-item', 'Store Vanilla', 'Dessert', 1)"
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES ('store-variant', 'Store Regular', 1)"
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, price, is_verified,
                assignment_seq, verification_seq
            ) VALUES ('1001', 'store-item', 'store-variant', 299, 0, 4, 10)
            """
        )
        base = {
            "order_item_id": "1001",
            "menu_item_id": "store-item",
            "variant_id": "store-variant",
            "global_menu_item_id": "global-vanilla",
            "global_variant_id": "global-regular",
            "is_verified": 1,
            "last_seq": 9,
        }
        apply_global_assignment_rows(
            self.conn,
            [{**base, "last_verification_seq": 5}],
            server_revision=7,
        )
        stale = self.conn.execute(
            "SELECT is_verified, verification_seq FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        self.assertEqual(tuple(stale), (0, 10))
        apply_global_assignment_rows(
            self.conn,
            [{**base, "last_verification_seq": 11}],
            server_revision=7,
        )
        newer = self.conn.execute(
            "SELECT is_verified, verification_seq FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        self.assertEqual(tuple(newer), (1, 11))

    def test_duplicate_variant_label_with_distinct_dimension_projects_safely(self) -> None:
        snapshot = json.loads(json.dumps(self.fixture))
        snapshot["variants"].append({
            "global_variant_id": "global-regular-500",
            "canonical_name": "Regular Tub",
            "unit": "GMS",
            "value": 500,
            "is_verified": True,
            "lifecycle_state": "active",
            "server_revision": 7,
        })
        apply_global_menu_payload_page(
            self.conn, snapshot, stream="snapshot", capability=capability(complete=False)
        )
        rows = self.conn.execute(
            """
            SELECT gv.canonical_name, gv.value, v.variant_name
            FROM global_variants gv
            JOIN variant_global_links l ON l.global_variant_id=gv.global_variant_id
            JOIN variants v ON v.variant_id=l.local_variant_id
            WHERE l.is_projection_owner=1
            ORDER BY gv.value
            """
        ).fetchall()
        self.assertEqual([row[0] for row in rows], ["Regular Tub", "Regular Tub"])
        self.assertEqual([int(row[1]) for row in rows], [300, 500])
        self.assertEqual(len({row[2] for row in rows}), 2)

    def test_assignment_pull_validates_the_v14_group_envelope(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        owner = self.conn.execute(
            "SELECT local_menu_item_id FROM menu_item_global_links "
            "WHERE is_projection_owner=1"
        ).fetchone()[0]
        variant = self.conn.execute(
            "SELECT local_variant_id FROM variant_global_links "
            "WHERE is_projection_owner=1"
        ).fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, price,
                is_verified, assignment_seq
            ) VALUES ('101', ?, ?, 299, 0, 4)
            """,
            (owner, variant),
        )
        page = {
            "schema_version": 1,
            "menu_group_id": "group-desserts",
            "menu_group_revision": 7,
            "menu_revision": 55,
            "assignments": [
                {
                    "order_item_id": "101",
                    "menu_item_id": owner,
                    "variant_id": variant,
                    "is_verified": 1,
                    "last_seq": 7,
                    "last_verification_seq": 7,
                    "last_event_id": "event-7",
                    "global_menu_item_id": "global-vanilla",
                    "global_variant_id": "global-regular",
                }
            ],
            "next_page": None,
        }
        cap = capability(revision=7)
        with patch(
            "src.core.global_menu_sync.require_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.resolve_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.get_global_menu_assignment_endpoint",
            return_value="https://cloud/menu-assignments/snapshot",
        ), patch(
            "src.core.global_menu_sync._fetch_page",
            return_value={"error": None, **page},
        ):
            result = pull_global_assignment_snapshot(self.conn, auth="sync-key")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(get_menu_state_revision(self.conn), 55)

        wrong_group = {**page, "menu_group_id": "other-group"}
        with patch(
            "src.core.global_menu_sync.require_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.resolve_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.get_global_menu_assignment_endpoint",
            return_value="https://cloud/menu-assignments/snapshot",
        ), patch(
            "src.core.global_menu_sync._fetch_page",
            return_value={"error": None, **wrong_group},
        ):
            refused = pull_global_assignment_snapshot(self.conn, auth="sync-key")
        self.assertEqual(refused["status"], "error")
        self.assertIn("Cross-group", refused["error"])

    def test_assignment_pull_aggregates_stale_and_legacy_recovered_counts(self) -> None:
        cap = capability(revision=7)
        pages = [
            {
                "schema_version": 1,
                "menu_group_id": "group-desserts",
                "menu_group_revision": 7,
                "assignments": [],
                "next_page": "page-2",
            },
            {
                "schema_version": 1,
                "menu_group_id": "group-desserts",
                "menu_group_revision": 7,
                "assignments": [],
                "next_page": None,
            },
        ]
        apply_results = [
            {
                "rows_applied": 4,
                "rows_missing": 2,
                "rows_stale": 3,
                "rows_recovered_legacy_watermark": 1,
            },
            {
                "rows_applied": 5,
                "rows_missing": 6,
                "rows_stale": 7,
                "rows_recovered_legacy_watermark": 8,
            },
        ]
        with patch(
            "src.core.global_menu_sync.require_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.resolve_global_menu_capability",
            return_value=cap,
        ), patch(
            "src.core.global_menu_sync.get_global_menu_assignment_endpoint",
            return_value="https://cloud/menu-assignments/snapshot",
        ), patch(
            "src.core.global_menu_sync._fetch_page",
            side_effect=pages,
        ), patch(
            "src.core.global_menu_sync.apply_global_assignment_rows",
            side_effect=apply_results,
        ):
            result = pull_global_assignment_snapshot(self.conn, auth="sync-key")

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["rows_applied"], 9)
        self.assertEqual(result["rows_missing"], 8)
        self.assertEqual(result["rows_stale"], 10)
        self.assertEqual(result["rows_recovered_legacy_watermark"], 9)

    def test_global_merge_and_undo_recompute_stats_and_clear_forecasts(self) -> None:
        snapshot = json.loads(json.dumps(self.fixture))
        snapshot["items"].append(
            {
                "global_menu_item_id": "global-source",
                "canonical_name": "Vanilla Scoop",
                "canonical_type": "Ice Cream",
                "is_verified": True,
                "server_revision": 7,
            }
        )
        apply_global_menu_payload_page(
            self.conn, snapshot, stream="snapshot", capability=capability(complete=False)
        )
        owners = {
            row["global_menu_item_id"]: row["local_menu_item_id"]
            for row in self.conn.execute(
                """
                SELECT global_menu_item_id, local_menu_item_id
                FROM menu_item_global_links
                WHERE is_projection_owner=1
                """
            )
        }
        variant = self.conn.execute(
            "SELECT local_variant_id FROM variant_global_links WHERE is_projection_owner=1"
        ).fetchone()[0]
        source = owners["global-source"]
        target = owners["global-vanilla"]
        self.conn.execute(
            """
            INSERT INTO orders (
                order_id, petpooja_order_id, stream_id, event_id, occurred_at,
                created_on, order_type, order_from, order_status
            ) VALUES (1, 1, 1, 'event-1', '2026-08-09T10:00:00Z',
                      '2026-08-09 15:30:00', 'Delivery', 'POS', 'Success')
            """
        )
        self.conn.execute(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id, petpooja_itemid, name_raw,
                quantity, unit_price, total_price
            ) VALUES (1001, 1, ?, ?, 1001, 'Vanilla Scoop', 2, 300, 600)
            """,
            (source, variant),
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, price, is_verified, assignment_seq
            ) VALUES ('1001', ?, ?, 300, 1, 7)
            """,
            (source, variant),
        )

        def seed_forecasts() -> None:
            for item_id in (source, target):
                self.conn.execute(
                    "INSERT INTO item_forecast_cache (forecast_date, item_id, generated_on) VALUES ('2026-08-10', ?, '2026-08-09')",
                    (item_id,),
                )
                self.conn.execute(
                    "INSERT INTO item_backtest_cache (forecast_date, item_id, model_trained_through) VALUES ('2026-08-10', ?, '2026-08-09')",
                    (item_id,),
                )
                self.conn.execute(
                    "INSERT INTO volume_forecast_cache (forecast_date, item_id, generated_on, volume_value, unit) VALUES ('2026-08-10', ?, '2026-08-09', 2, 'units')",
                    (item_id,),
                )
                self.conn.execute(
                    "INSERT INTO volume_backtest_cache (forecast_date, item_id, model_trained_through) VALUES ('2026-08-10', ?, '2026-08-09')",
                    (item_id,),
                )
            self.conn.commit()

        def assert_projection(expected_item: str, expected_seq: int) -> None:
            mapping = self.conn.execute(
                "SELECT menu_item_id, variant_id, price, is_verified, assignment_seq FROM menu_item_variants WHERE order_item_id='1001'"
            ).fetchone()
            self.assertEqual(mapping["menu_item_id"], expected_item)
            self.assertEqual(mapping["variant_id"], variant)
            self.assertEqual(float(mapping["price"]), 300.0)
            self.assertEqual(int(mapping["is_verified"]), 1)
            self.assertEqual(int(mapping["assignment_seq"]), expected_seq)
            stats = {
                row["menu_item_id"]: (int(row["total_sold"]), float(row["total_revenue"]))
                for row in self.conn.execute(
                    "SELECT menu_item_id, total_sold, total_revenue FROM menu_items WHERE menu_item_id IN (?, ?)",
                    (source, target),
                )
            }
            other = source if expected_item == target else target
            self.assertEqual(stats[expected_item], (2, 600.0))
            self.assertEqual(stats[other], (0, 0.0))
            for table in (
                "item_forecast_cache",
                "item_backtest_cache",
                "volume_forecast_cache",
                "volume_backtest_cache",
            ):
                self.assertEqual(
                    self.conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE item_id IN (?, ?)",
                        (source, target),
                    ).fetchone()[0],
                    0,
                )

        with patch("utils.menu_utils._clear_impacted_models"):
            seed_forecasts()
            apply_global_assignment_rows(
                self.conn,
                [{
                    "order_item_id": "1001",
                    "global_menu_item_id": "global-vanilla",
                    "global_variant_id": "global-regular",
                    "is_verified": True,
                    "assignment_seq": 8,
                }],
            )
            assert_projection(target, 7)

            seed_forecasts()
            apply_global_assignment_rows(
                self.conn,
                [{
                    "order_item_id": "1001",
                    "global_menu_item_id": "global-source",
                    "global_variant_id": "global-regular",
                    "is_verified": True,
                    "assignment_seq": 9,
                }],
            )
            assert_projection(source, 7)

    def test_mutation_builder_emits_only_stable_global_identity(self) -> None:
        apply_global_menu_payload_page(
            self.conn, self.fixture, stream="snapshot", capability=capability(complete=False)
        )
        owner = self.conn.execute(
            "SELECT local_menu_item_id FROM menu_item_global_links WHERE is_projection_owner=1"
        ).fetchone()[0]
        variant = self.conn.execute(
            "SELECT local_variant_id FROM variant_global_links WHERE is_projection_owner=1"
        ).fetchone()[0]
        action = build_global_action_from_local(
            self.conn,
            mutation_type="merge",
            source_local_menu_item_id=owner,
            source_local_variant_id=variant,
            target_local_menu_item_id=owner,
            target_local_variant_id=variant,
        )
        encoded = json.dumps(action, sort_keys=True)
        self.assertIn("global_item_id", encoded)
        self.assertIn("global_item.merge", encoded)
        self.assertNotIn("local_menu_item_id", encoded)
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=capability(),
        ):
            with self.assertRaises(GlobalMenuMutationError):
                preview_global_mutation(
                    self.conn,
                    action={"mutation_type": "global_item.merge", "source_id": owner},
                )

    def test_itemcode_mutation_builder_drops_the_variant_target(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            self.fixture,
            stream="snapshot",
            capability=capability(complete=False),
        )
        owner = self.conn.execute(
            "SELECT local_menu_item_id FROM menu_item_global_links "
            "WHERE global_menu_item_id='global-vanilla' AND is_projection_owner=1"
        ).fetchone()[0]
        variant = self.conn.execute(
            "SELECT local_variant_id FROM variant_global_links "
            "WHERE global_variant_id='global-regular' AND is_projection_owner=1"
        ).fetchone()[0]
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=capability(),
        ):
            action = build_global_action_from_local(
                self.conn,
                mutation_type="global_locator.map",
                target_local_menu_item_id=owner,
                target_local_variant_id=variant,
                details={
                    "locator_type": "itemcode",
                    "locator_value": "IC-VAN",
                    "rule_scope": "group",
                    "confirm_group_wide": True,
                },
            )
        self.assertEqual(action["payload"]["global_variant_id"], "")
        self.assertEqual(action["payload"]["rule_scope"], "group")

    def test_transport_retries_a_transient_failure(self) -> None:
        response = Mock(status_code=200, content=b"{}")
        response.json.return_value = {
            "schema_version": 1,
            "menu_group_id": "group-desserts",
            "catalog_revision": 7,
        }
        with patch("src.core.central_api.scoped_headers", return_value={}), patch(
            "requests.get", side_effect=[TimeoutError("temporary"), response]
        ) as request:
            result = _fetch_page(
                self.conn,
                "https://cloud.example/global-menu/snapshot",
                auth="key",
                cursor=None,
                limit=100,
            )
        self.assertIsNone(result["error"])
        self.assertEqual(request.call_count, 2)


class GlobalMenuAggregationAndMutationTests(unittest.TestCase):
    def test_variant_create_can_be_previewed_before_a_local_row_exists(self) -> None:
        action = build_global_action_from_local(
            Mock(),
            mutation_type="variant_create",
            details={"canonical_name": "Party Tub", "unit": "GMS", "value": 500},
        )
        self.assertEqual(action, {
            "mutation_type": "global_variant.create",
            "payload": {
                "canonical_name": "Party Tub",
                "dimension": {"unit": "GMS", "value": 500},
            },
        })

    def test_global_commit_holds_cloud_pull_lock_through_reconciliation(self) -> None:
        def assert_locked(_conn, *, preview):
            self.assertTrue(CLOUD_PULL_LOCK.locked())
            return {"status": "success", "preview": preview}

        with patch(
            "src.core.global_menu_mutation._commit_global_mutation_locked",
            side_effect=assert_locked,
        ):
            result = commit_global_mutation(Mock(), preview={"mutation_id": "m-1"})
        self.assertEqual(result["status"], "success")
        self.assertFalse(CLOUD_PULL_LOCK.locked())

    def test_item_create_preview_is_committable_with_incomplete_coverage(self) -> None:
        incomplete_coverage = replace(
            capability(
                capabilities=("global_menu_v1", "global_menu_resolution_v1")
            ),
            coverage_linked=1,
            coverage_total=2,
        )
        payload = {
            "canonical_name": "Pistachio Kulfi",
            "canonical_type": "Dessert",
            "is_verified": True,
        }
        response = {
            "schema_version": 1,
            "menu_group_id": incomplete_coverage.menu_group_id,
            "menu_group_revision": incomplete_coverage.mutation_revision,
            "mutation_type": "global_item.create",
            "payload": payload,
            "preview_digest": "digest-create",
            "revision_current": True,
            "conflicts": [],
            "commit_allowed": True,
        }
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=incomplete_coverage,
        ) as gate, patch(
            "src.core.global_menu_mutation._urls",
            return_value=("https://cloud/mutations", "sync-key"),
        ), patch(
            "src.core.global_menu_mutation._headers",
            return_value={"X-Global-Menu-Key": "editor-key"},
        ), patch(
            "src.core.global_menu_mutation._request_json",
            return_value=(200, response),
        ):
            preview = preview_global_mutation(
                Mock(),
                action={"mutation_type": "global_item.create", "payload": payload},
            )
        self.assertTrue(preview["commit_allowed"])
        self.assertFalse(preview["coverage_complete"])
        gate.assert_called_once_with(
            unittest.mock.ANY,
            for_write=True,
            allow_resolution_write=True,
        )

    def test_accepted_mutation_refreshes_catalog_and_assignments(self) -> None:
        with patch(
            "src.core.global_menu_sync.pull_global_menu_state",
            return_value={"status": "applied"},
        ) as catalog, patch(
            "src.core.global_menu_sync.pull_global_assignment_snapshot",
            return_value={"status": "applied"},
        ) as assignments, patch(
            "src.core.global_menu_history.pull_global_menu_history",
            return_value={"status": "applied"},
        ) as history:
            _apply_accepted_projection(Mock(), {})
        catalog.assert_called_once()
        assignments.assert_called_once()
        history.assert_called_once()

    def test_resolution_context_uses_restaurant_qualified_item_and_addon_ids(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(conn)
        conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('local-kulfi', 'Kulfi', 'Dessert', 1)"
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name, unit, value) "
            "VALUES ('local-regular', 'Regular', 'count', 1)"
        )
        conn.executemany(
            "INSERT INTO menu_item_variants "
            "(order_item_id, menu_item_id, variant_id, is_verified) "
            "VALUES (?, 'local-kulfi', 'local-regular', 1)",
            [("7777",), ("addon-88",), ("generated-name-key",)],
        )
        conn.execute(
            """
            INSERT INTO orders (
                order_id, petpooja_order_id, stream_id, event_id, occurred_at,
                created_on, order_type, order_from, order_status
            ) VALUES (1, 1, 1, 'event-1', '2026-08-10T10:00:00Z',
                      '2026-08-10 15:30:00', 'Delivery', 'POS', 'Success')
            """
        )
        conn.execute(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id,
                petpooja_itemid, itemcode, name_raw, quantity, unit_price, total_price
            ) VALUES (1, 1, 'local-kulfi', 'local-regular', 7777,
                      'IC-KULFI', 'Kulfi', 1, 100, 100)
            """
        )
        conn.execute(
            """
            INSERT INTO order_item_addons (
                order_item_addon_id, order_item_id, menu_item_id, variant_id,
                petpooja_addonid, name_raw, quantity, price
            ) VALUES (1, 1, 'local-kulfi', 'local-regular', 'addon-88',
                      'Kulfi Addon', 1, 20)
            """
        )
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=capability(),
        ):
            context = global_resolution_context(
                conn,
                local_menu_item_id="local-kulfi",
                local_variant_id="local-regular",
            )
        self.assertEqual(
            {(row["locator_type"], row["locator_value"]) for row in context["locators"]},
            {
                ("pos_item", "7777"),
                ("pos_addon", "addon-88"),
                ("itemcode", "IC-KULFI"),
            },
        )
        scopes = {row["locator_type"]: row["rule_scope"] for row in context["locators"]}
        self.assertEqual(scopes["pos_item"], "restaurant")
        self.assertEqual(scopes["pos_addon"], "restaurant")
        self.assertEqual(scopes["itemcode"], "group")

    def test_verified_unlinked_pair_enters_the_resolution_queue_only(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(conn)
        conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('local-kulfi', 'Kulfi', 'Dessert', 1)"
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name) VALUES ('local-regular', 'Regular')"
        )
        conn.execute(
            "INSERT INTO menu_item_variants "
            "(order_item_id, menu_item_id, variant_id, is_verified) "
            "VALUES ('7777', 'local-kulfi', 'local-regular', 1)"
        )
        self.assertTrue(fetch_unverified_items(conn).empty)
        rows = fetch_unverified_items(conn, include_global_identity_gaps=True)
        self.assertEqual(rows.iloc[0]["resolution_kind"], "global_identity_gap")
        self.assertEqual(rows.iloc[0]["assignment_order_item_ids"], ["7777"])
        self.assertEqual(
            fetch_resolution_counts(conn, include_global_identity_gaps=True),
            {
                "local_unverified": 0,
                "globally_unlinked": 1,
                "mapped_verified": 0,
            },
        )
        conn.execute(
            "UPDATE menu_item_variants SET is_verified=0 WHERE order_item_id='7777'"
        )
        self.assertEqual(
            fetch_resolution_counts(conn, include_global_identity_gaps=True),
            {
                "local_unverified": 1,
                "globally_unlinked": 0,
                "mapped_verified": 0,
            },
        )
        conn.execute(
            "UPDATE menu_item_variants SET is_verified=1 WHERE order_item_id='7777'"
        )
        self.assertEqual(
            fetch_resolution_counts(conn, include_global_identity_gaps=False),
            {
                "local_unverified": 0,
                "globally_unlinked": 0,
                "mapped_verified": 1,
            },
        )
        conn.close()

    def test_verified_non_pos_synthetic_rows_are_not_global_identity_gaps(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(conn)
        conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES (?, ?, 'Dessert', 1)",
            [
                ("catalog-item", "Catalog Only"),
                ("addon-item", "Addon Only"),
            ],
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES ('local-regular', 'Regular', 1)"
        )
        conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            ) VALUES (?, ?, 'local-regular', 1, NULL, 0)
            """,
            [
                (catalog_stub_order_item_id("catalog-item"), "catalog-item"),
                (
                    addon_seeded_mapping_order_item_id(
                        "addon-item", "local-regular"
                    ),
                    "addon-item",
                ),
            ],
        )

        rows = fetch_unverified_items(conn, include_global_identity_gaps=True)

        self.assertTrue(rows.empty)
        conn.close()

    def test_pos_backed_missing_global_link_remains_visible(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(conn)
        conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('pos-item', 'POS Kulfi', 'Dessert', 1)"
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES ('pos-variant', 'Regular', 1)"
        )
        conn.execute(
            """
            INSERT INTO orders (
                order_id, petpooja_order_id, stream_id, event_id, occurred_at,
                created_on, order_type, order_from, order_status
            ) VALUES (1, 1, 1, 'event-1', '2026-08-10T10:00:00Z',
                      '2026-08-10 15:30:00', 'Delivery', 'POS', 'Success')
            """
        )
        conn.execute(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id,
                petpooja_itemid, name_raw, quantity, unit_price, total_price
            ) VALUES (1, 1, 'pos-item', 'pos-variant', 7777,
                      'POS Kulfi', 1, 100, 100)
            """
        )
        conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified
            ) VALUES ('7777', 'pos-item', 'pos-variant', 1)
            """
        )

        rows = fetch_unverified_items(conn, include_global_identity_gaps=True)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]["menu_item_id"], "pos-item")
        self.assertEqual(rows.iloc[0]["resolution_kind"], "global_identity_gap")
        self.assertEqual(rows.iloc[0]["assignment_order_item_ids"], ["7777"])
        conn.close()

    def test_unverified_synthetic_rows_keep_existing_resolution_kinds(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(conn)
        conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES (?, ?, 'Dessert', 0)",
            [
                ("catalog-item", "Unverified Catalog"),
                ("addon-item", "Unverified Addon"),
            ],
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES ('local-regular', 'Regular', 0)"
        )
        conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            ) VALUES (?, ?, 'local-regular', 0, NULL, 0)
            """,
            [
                (catalog_stub_order_item_id("catalog-item"), "catalog-item"),
                (
                    addon_seeded_mapping_order_item_id(
                        "addon-item", "local-regular"
                    ),
                    "addon-item",
                ),
            ],
        )

        rows = fetch_unverified_items(conn, include_global_identity_gaps=True)
        kinds = {
            str(row["menu_item_id"]): str(row["resolution_kind"])
            for _, row in rows.iterrows()
        }

        self.assertEqual(kinds["catalog-item"], "unverified_mapping")
        self.assertEqual(kinds["addon-item"], "addon_gap")
        conn.close()

    def test_mutation_headers_use_the_separate_editor_credential(self) -> None:
        with patch(
            "src.core.central_api.scoped_headers",
            return_value={"Authorization": "Bearer sync"},
        ), patch(
            "src.core.global_menu_mutation.get_global_menu_editor_key",
            return_value="editor-key",
        ):
            headers = _headers(Mock(), "sync-key", for_write=True)
        self.assertEqual(headers["X-Global-Menu-Key"], "editor-key")

    def test_pos_mutation_overwrites_a_foreign_restaurant_id(self) -> None:
        normalized = _normalize_mutation_payload(
            "global_locator.map",
            {
                "locator_type": "pos_item",
                "locator_value": "7777",
                "restaurant_id": "rest-2",
            },
            capability=capability("rest-1"),
        )
        self.assertEqual(normalized["rule_scope"], "restaurant")
        self.assertEqual(normalized["restaurant_id"], "rest-1")
        self.assertFalse(normalized["confirm_group_wide"])

    def test_alias_mutation_is_rejected_before_transport(self) -> None:
        with self.assertRaisesRegex(
            GlobalMenuMutationError,
            "aliases are suggestions",
        ):
            _normalize_mutation_payload(
                "global_locator.map",
                {
                    "locator_type": "alias",
                    "locator_value": "kulfi",
                    "rule_scope": "group",
                    "confirm_group_wide": True,
                    "global_item_id": "global-kulfi",
                },
                capability=capability("rest-1"),
            )

    def test_itemcode_variant_target_is_rejected_before_transport(self) -> None:
        with self.assertRaisesRegex(
            GlobalMenuMutationError,
            "global_variant_id must be blank",
        ):
            _normalize_mutation_payload(
                "global_locator.map",
                {
                    "locator_type": "itemcode",
                    "locator_value": "IC-KULFI",
                    "rule_scope": "group",
                    "confirm_group_wide": True,
                    "global_item_id": "global-kulfi",
                    "global_variant_id": "global-mini",
                },
                capability=capability("rest-1"),
            )

    def test_missing_editor_credential_remains_an_authorization_error(self) -> None:
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=capability(),
        ), patch(
            "src.core.global_menu_mutation._urls",
            return_value=("https://cloud/mutations", "sync-key"),
        ), patch(
            "src.core.central_api.scoped_headers",
            return_value={"Authorization": "Bearer sync"},
        ), patch(
            "src.core.global_menu_mutation.get_global_menu_editor_key",
            return_value=None,
        ), patch(
            "src.core.global_menu_mutation._request_json"
        ) as transport:
            with self.assertRaises(GlobalMenuMutationError) as raised:
                preview_global_mutation(
                    Mock(),
                    action={
                        "mutation_type": "global_item.verify",
                        "payload": {
                            "global_item_id": "global-vanilla",
                            "is_verified": True,
                        },
                    },
                )
        self.assertEqual(raised.exception.code, "global_menu_editor_required")
        transport.assert_not_called()

    def test_frozen_v16_preview_fixture_is_the_client_wire_shape(self) -> None:
        fixture = contract_fixture("global_menu_mutation_preview_merge")
        request = fixture["request"]
        response = fixture["payload"]
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=capability(revision=0, group_id="group-1"),
        ), patch(
            "src.core.global_menu_mutation._urls",
            return_value=("https://cloud/mutations", "sync-key"),
        ), patch(
            "src.core.global_menu_mutation._headers",
            return_value={"X-Global-Menu-Key": "editor-key"},
        ), patch(
            "src.core.global_menu_mutation._request_json",
            return_value=(200, response),
        ) as transport:
            preview = preview_global_mutation(
                Mock(),
                action={
                    "mutation_type": request["mutation_type"],
                    "payload": request["payload"],
                },
                mutation_id="cd1e814c-ff28-4af2-9c82-d4e5226c26b2",
            )
        sent = transport.call_args.kwargs["payload"]
        self.assertEqual(sent, request)
        self.assertEqual(preview["preview_digest"], response["preview_digest"])
        self.assertEqual(preview["mutation_id"], "cd1e814c-ff28-4af2-9c82-d4e5226c26b2")

    def test_frozen_v16_commit_fixture_is_the_client_wire_shape(self) -> None:
        preview_fixture = contract_fixture("global_menu_mutation_preview_merge")
        commit_fixture = contract_fixture("global_menu_mutation_commit_merge")
        preview = {
            **preview_fixture["payload"],
            "mutation_id": commit_fixture["request"]["mutation_id"],
            "coverage_complete": True,
        }
        attribution = {
            "employee": {"employee_id": "ops", "name": "ops"},
            "device": {"install_id": "dev-1"},
        }
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=capability(revision=0, group_id="group-1"),
        ), patch(
            "src.core.global_menu_mutation._urls",
            return_value=("https://cloud/mutations", "sync-key"),
        ), patch(
            "src.core.global_menu_mutation._headers",
            return_value={"X-Global-Menu-Key": "editor-key"},
        ), patch(
            "src.core.global_menu_mutation.get_sync_attribution",
            return_value=attribution,
        ), patch(
            "src.core.global_menu_mutation._request_json",
            return_value=(200, commit_fixture["payload"]),
        ) as transport, patch(
            "src.core.global_menu_mutation._apply_accepted_projection"
        ):
            result = commit_global_mutation(Mock(), preview=preview)
        sent = transport.call_args.kwargs["payload"]
        expected = {
            **commit_fixture["request"],
            # Preview returns the server-normalized payload. Echoing that
            # canonical form keeps the approved digest stable.
            "payload": preview_fixture["payload"]["payload"],
            "uploaded_by": attribution["employee"],
            "uploaded_from": attribution["device"],
        }
        self.assertEqual(sent, expected)
        self.assertEqual(result["status"], "success")

    def test_variant_create_commit_restores_the_previewed_dimension_shape(self) -> None:
        mutation_id = "variant-create-1"
        preview = {
            "mutation_id": mutation_id,
            "mutation_type": "global_variant.create",
            "menu_group_id": "group-1",
            "menu_group_revision": 0,
            "coverage_complete": False,
            "conflicts": [],
            "preview_digest": "variant-create-digest",
            "payload": {
                "canonical_name": "Mini Tub",
                "unit": "GMS",
                "value": 160.0,
                "is_verified": False,
            },
        }
        accepted = {"status": "accepted", "mutation_id": mutation_id}
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=capability(revision=0, group_id="group-1"),
        ), patch(
            "src.core.global_menu_mutation._urls",
            return_value=("https://cloud/mutations", "sync-key"),
        ), patch(
            "src.core.global_menu_mutation._headers",
            return_value={"X-Global-Menu-Key": "editor-key"},
        ), patch(
            "src.core.global_menu_mutation.get_sync_attribution",
            return_value={"employee": None, "device": {}},
        ), patch(
            "src.core.global_menu_mutation._request_json",
            return_value=(200, accepted),
        ) as transport, patch(
            "src.core.global_menu_mutation._apply_accepted_projection"
        ):
            result = commit_global_mutation(Mock(), preview=preview)

        self.assertEqual(
            transport.call_args.kwargs["payload"]["payload"],
            {
                "canonical_name": "Mini Tub",
                "dimension": {"unit": "GMS", "value": 160.0},
                "is_verified": False,
            },
        )
        self.assertEqual(result["status"], "success")

    def test_global_ids_group_different_names_and_leave_unlinked_rows_distinct(self) -> None:
        ready = {
            "active": True,
            "aggregation_ready": True,
            "coverage_linked": 2,
            "coverage_total": 2,
            "quarantine_count": 0,
        }
        pairs = [
            (
                profile("rest-1"),
                {
                    "identity": ready,
                    "rows": [
                        {
                            "menu_item_id": "local-a",
                            "name": "Vanilla",
                            "type": "Dessert",
                            "price": 100,
                            "qty": 2,
                            "global_menu_item_id": "global-vanilla",
                            "canonical_name": "Eggless Vanilla Ice Cream",
                            "canonical_type": "Ice Cream",
                        },
                        {"menu_item_id": "unlinked-a", "name": "Mystery", "type": "Other", "price": 10, "qty": 1},
                    ],
                },
            ),
            (
                profile("rest-2"),
                {
                    "identity": ready,
                    "rows": [
                        {
                            "menu_item_id": "local-b",
                            "name": "Vanilla Scoop",
                            "type": "Frozen",
                            "price": 120,
                            "qty": 3,
                            "global_menu_item_id": "global-vanilla",
                            "canonical_name": "Eggless Vanilla Ice Cream",
                            "canonical_type": "Ice Cream",
                        },
                        {"menu_item_id": "unlinked-b", "name": "Mystery", "type": "Other", "price": 20, "qty": 1},
                    ],
                },
            ),
        ]
        rows, coverage = group_menu_identity_rows(
            pairs,
            legacy_group_by=("name", "type"),
            spec={"qty": Sum(), "price": Min()},
            item_id_field="menu_item_id",
            canonical_item_name_field="name",
            canonical_item_type_field="type",
            contributor_fields=("menu_item_id", "price"),
            sort_by="name",
            descending=False,
        )
        linked = [row for row in rows if row.get("global_menu_item_id")]
        unlinked = [row for row in rows if row.get("identity_unlinked")]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["name"], "Eggless Vanilla Ice Cream")
        self.assertEqual(linked[0]["qty"], 5)
        self.assertEqual({c["price"] for c in linked[0]["contributors"]}, {100, 120})
        self.assertEqual(len(unlinked), 2)
        self.assertTrue(coverage["global_aggregation_active"])

    def test_item_without_variant_still_uses_global_item_identity(self) -> None:
        ready = {
            "active": True,
            "aggregation_ready": True,
            "coverage_linked": 1,
            "coverage_total": 1,
            "quarantine_count": 0,
        }
        pairs = [
            (
                profile("rest-1"),
                {
                    "identity": ready,
                    "rows": [
                        {
                            "menu_item_id": "local-a",
                            "variant_id": None,
                            "name": "Vanilla",
                            "global_menu_item_id": "global-vanilla",
                            "global_variant_id": None,
                            "qty": 1,
                        }
                    ],
                },
            )
        ]
        rows, _coverage = group_menu_identity_rows(
            pairs,
            legacy_group_by=("name",),
            spec={"qty": Sum()},
            item_id_field="menu_item_id",
            variant_id_field="variant_id",
        )
        self.assertEqual(rows[0]["global_menu_item_id"], "global-vanilla")
        self.assertFalse(rows[0]["identity_unlinked"])

    def test_timeout_reconciles_status_without_reposting(self) -> None:
        preview = {
            "mutation_id": "mutation-1",
            "preview_digest": "preview-digest",
            "menu_group_id": "group-desserts",
            "menu_group_revision": 7,
            "coverage_complete": True,
            "conflicts": [],
            "mutation_type": "global_item.merge",
            "payload": {
                "source_global_item_id": "global-source",
                "target_global_item_id": "global-target",
            },
        }
        accepted = {
            "status": "accepted",
            "mutation_id": "mutation-1",
            "menu_group_id": "group-desserts",
        }
        cap = capability(revision=7)
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability", return_value=cap
        ), patch(
            "src.core.global_menu_mutation._urls", return_value=("https://cloud/mutations", "key")
        ), patch(
            "src.core.global_menu_mutation._headers", return_value={}
        ), patch(
            "src.core.global_menu_mutation._request_json", side_effect=TimeoutError("timeout")
        ) as post, patch(
            "src.core.global_menu_mutation.get_sync_attribution",
            return_value={"employee": None, "device": {}},
        ), patch(
            "src.core.global_menu_mutation.global_mutation_status", return_value=accepted
        ) as status, patch(
            "src.core.global_menu_mutation._apply_accepted_projection"
        ):
            result = commit_global_mutation(Mock(), preview=preview)
        self.assertEqual(result["status"], "success")
        self.assertEqual(post.call_count, 1)
        status.assert_called_once_with(unittest.mock.ANY, "mutation-1")


if __name__ == "__main__":
    unittest.main()
