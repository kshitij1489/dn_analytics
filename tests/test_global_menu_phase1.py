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
    _headers,
    _apply_accepted_projection,
    build_global_action_from_local,
    commit_global_mutation,
    global_resolution_context,
    preview_global_mutation,
)
from src.core.global_menu_schema import (
    GLOBAL_MENU_MODE,
    GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
    GlobalMenuCapabilityError,
    GlobalMenuCapabilityStatus,
    require_global_menu_capability,
    resolve_global_menu_capability,
    update_global_menu_state,
)
from src.core.global_menu_sync import (
    _fetch_page,
    apply_global_assignment_rows,
    apply_global_menu_payload_page,
    materialize_shared_pos_catalog,
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
from src.core.queries.menu_queries import fetch_unverified_items
from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK
from utils.id_generator import generate_deterministic_id
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


def shared_capability(
    restaurant_id: str = "rest-1",
    *,
    revision: int = 0,
    complete: bool = False,
    group_id: str = "group-desserts",
) -> GlobalMenuCapabilityStatus:
    return capability(
        restaurant_id,
        revision=revision,
        complete=complete,
        group_id=group_id,
        capabilities=(
            "global_menu_v1",
            "global_menu_resolution_v1",
            GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
        ),
    )


def shared_catalog_payload(*, revision: int = 7, price: Any = "290.00") -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "menu_group_id": "group-desserts",
        "catalog_revision": revision,
        "mutation_revision": revision,
        "next_cursor": f"snapshot-{revision}",
        "event_cursor": str(revision),
        "has_more": False,
        "coverage": {"linked": 2, "total": 2},
        "items": [
            {
                "global_menu_item_id": "global-vanilla",
                "canonical_name": "Eggless Vanilla Ice Cream",
                "canonical_type": "Ice Cream",
                "is_verified": True,
                "lifecycle_state": "active",
                "server_revision": revision,
            }
        ],
        "variants": [
            {
                "global_variant_id": "global-regular",
                "canonical_name": "Regular Tub",
                "unit": "GMS",
                "value": 300,
                "is_verified": True,
                "lifecycle_state": "active",
                "server_revision": revision,
            }
        ],
        "links": {"menu_items": [], "variants": []},
        "redirects": [],
        "mapping_rules": [
            {
                "rule_id": "shared-item-1001",
                "locator_scope": "group",
                "restaurant_id": None,
                "locator_kind": "pos-item",
                "locator_value": "1001",
                "normalized_locator": "1001",
                "target_global_menu_item_id": "global-vanilla",
                "target_global_variant_id": "global-regular",
                "price": price,
                "provenance": "reviewed-shared-pos",
                "is_verified": True,
                "lifecycle_state": "active",
                "server_revision": revision,
            },
            {
                "rule_id": "shared-addon-2001",
                "locator_scope": "group",
                "restaurant_id": None,
                "locator_kind": "pos-addon",
                "locator_value": "2001",
                "normalized_locator": "2001",
                "target_global_menu_item_id": "global-vanilla",
                "target_global_variant_id": None,
                "price": "40.00",
                "provenance": "reviewed-shared-pos",
                "is_verified": True,
                "lifecycle_state": "active",
                "server_revision": revision,
            },
        ],
    }


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
        self.assertTrue(
            {"shared_pos_rule_tombstoned", "shared_pos_prior_is_active"}
            <= mapping_columns
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

    def test_capability_ladder_exposes_narrow_shadow_resolution(self) -> None:
        shadow = capability(
            capabilities=("global_menu_v1", "global_menu_resolution_v1")
        )
        self.assertTrue(shadow.active)
        self.assertTrue(shadow.resolution_advertised)
        self.assertFalse(shadow.aggregation_ready)
        self.assertFalse(shadow.mutation_ready)
        aggregating = capability(
            capabilities=(
                "global_menu_v1",
                "global_menu_resolution_v1",
                "global_menu_aggregation_v1",
            )
        )
        self.assertTrue(aggregating.resolution_advertised)
        self.assertTrue(aggregating.aggregation_ready)
        self.assertFalse(aggregating.mutation_ready)
        active = capability()
        self.assertTrue(active.aggregation_ready)
        self.assertTrue(active.mutation_ready)

    def test_shared_pos_capability_is_advertised_separately_and_fails_closed(self) -> None:
        absent = capability()
        self.assertFalse(absent.shared_pos_catalog_advertised)
        self.assertFalse(absent.shared_pos_catalog_ready)

        advertised = capability(
            capabilities=(*absent.capabilities, GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY)
        )
        self.assertTrue(advertised.shared_pos_catalog_advertised)
        self.assertTrue(advertised.shared_pos_catalog_ready)
        self.assertTrue(advertised.to_dict()["shared_pos_catalog_ready"])

        bootstrapping = replace(advertised, bootstrap_status="in_progress")
        self.assertTrue(bootstrapping.shared_pos_catalog_advertised)
        self.assertFalse(bootstrapping.shared_pos_catalog_ready)


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
        ("global_menu_fixtures.json", "global_menu_mutation_preview_price_update"),
        (
            "menu_mutations_fixtures.json",
            "menu_mutations_commit_request_global_shadow_write",
        ),
        (
            "menu_mutations_fixtures.json",
            "menu_mutations_commit_response_global_shadow_write_409",
        ),
    }

    def test_owner_manifest_names_existing_fixtures(self) -> None:
        for filename, name in sorted(self.OWNED_FIXTURES):
            self.assertEqual(named_contract_fixture(filename, name)["name"], name)

    def test_shared_pos_observation_and_acknowledgements_are_pinned(self) -> None:
        request = named_contract_fixture(
            "menu_bootstrap_fixtures.json", "menu_bootstrap_ingest_request"
        )["payload"]
        observation = request["shared_pos_catalog"]
        self.assertEqual(len(observation), 1)
        self.assertEqual(
            set(observation[0]),
            {
                "locator_type",
                "locator_value",
                "menu_item_id",
                "variant_id",
                "item_name",
                "item_type",
                "variant_name",
                "variant_unit",
                "variant_value",
                "price",
            },
        )
        self.assertEqual(observation[0]["variant_value"], "500.00")
        self.assertEqual(observation[0]["price"], "290.00")
        for name in (
            "menu_bootstrap_ingest_response",
            "menu_bootstrap_ingest_response_seed_only_existing",
        ):
            response = named_contract_fixture("menu_bootstrap_fixtures.json", name)
            self.assertIs(response["payload"]["shared_pos_catalog_updated"], True)

    def test_price_bearing_snapshot_and_event_rules_are_pinned(self) -> None:
        snapshot = contract_fixture("global_menu_snapshot_rules")["payload"]["rows"][0]
        event = contract_fixture("global_menu_events_page")["payload"]["events"][0]
        event_rule = event["payload"]["mapping_rules"][0]
        for rule in (snapshot, event_rule):
            self.assertEqual(rule["rule_scope"], "group")
            self.assertEqual(rule["locator_type"], "pos_item")
            self.assertEqual(rule["price"], "290.00")
            self.assertIsInstance(rule["price"], str)

    def test_status_fixture_pins_shared_pos_parity_fields(self) -> None:
        payload = contract_fixture("global_menu_status")["payload"]
        self.assertIn(
            GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
            payload["menu_capabilities"],
        )
        self.assertTrue(payload["shared_pos_catalog_complete"])
        self.assertRegex(
            payload["catalog"]["shared_pos_catalog_digest"], r"^[0-9a-f]{64}$"
        )
        expected_fields = {
            "shared_pos_entries_observed",
            "shared_pos_entries_matching",
            "shared_pos_entries_missing",
            "shared_pos_peer_extras",
            "shared_pos_identity_mismatches",
            "shared_pos_price_mismatches",
            "shared_pos_catalog_complete",
        }
        for restaurant in payload["restaurants"]:
            self.assertTrue(expected_fields.issubset(restaurant))

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

    def test_price_preview_fixture_is_the_exact_generic_preview_wire(self) -> None:
        fixture = contract_fixture("global_menu_mutation_preview_price_update")
        request = fixture["request"]
        shared_capability = capability(
            revision=5,
            group_id="group-1",
            capabilities=(
                "global_menu_v1",
                "global_menu_mutations_v1",
                GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
            ),
        )
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=shared_capability,
        ), patch(
            "src.core.global_menu_mutation._urls",
            return_value=("https://cloud/mutations", "sync-key"),
        ), patch(
            "src.core.global_menu_mutation._headers",
            return_value={"X-Global-Menu-Key": "editor-key"},
        ), patch(
            "src.core.global_menu_mutation._request_json",
            return_value=(200, fixture["payload"]),
        ) as transport:
            preview = preview_global_mutation(
                Mock(),
                action={
                    "mutation_type": request["mutation_type"],
                    "payload": request["payload"],
                },
            )
        self.assertEqual(transport.call_args.kwargs["payload"], request)
        self.assertEqual(preview["payload"]["price"], "310.00")

    def test_shadow_refusal_fixture_is_machine_actionable(self) -> None:
        request = named_contract_fixture(
            "menu_mutations_fixtures.json",
            "menu_mutations_commit_request_global_shadow_write",
        )
        response = named_contract_fixture(
            "menu_mutations_fixtures.json",
            "menu_mutations_commit_response_global_shadow_write_409",
        )
        self.assertEqual(request["payload"]["mutation_type"], "menu_merge.applied")
        self.assertEqual(response["http_status"], 409)
        self.assertEqual(
            response["payload"]["code"], "global_menu_shadow_write_blocked"
        )
        self.assertEqual(
            response["payload"]["recommended_action"], "use_global_menu_mutations"
        )

    def test_allowed_restaurants_fixture_advertises_one_shared_group_policy(self) -> None:
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
                            GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
                            "global_menu_aggregation_v1",
                            "global_menu_mutations_v1",
                        }
                    )
                )
            },
        )


class GlobalMenuCapabilityGuardTests(unittest.TestCase):
    def test_shadow_resolution_is_ready_before_coverage_is_complete(self) -> None:
        shadow = replace(
            capability(
                capabilities=("global_menu_v1", "global_menu_resolution_v1")
            ),
            coverage_linked=1,
            coverage_total=2,
        )
        self.assertTrue(shadow.resolution_ready)
        self.assertFalse(shadow.coverage_complete)
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=shadow,
        ):
            self.assertIs(
                require_global_menu_capability(
                    Mock(), for_write=True, allow_resolution_write=True
                ),
                shadow,
            )
            with self.assertRaises(GlobalMenuCapabilityError):
                require_global_menu_capability(Mock(), for_write=True)

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

    def test_shadow_ingest_uses_global_resolution_and_not_legacy_itemcode(self) -> None:
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
        shadow = capability(
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
            return_value=shadow,
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
            self.assertEqual(first, {"status": "applied", "pages": 2, "rows_applied": 2})
            self.assertEqual(second, first)
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

        shadow = capability(
            group_id="group-1",
            capabilities=("global_menu_v1", "global_menu_resolution_v1"),
        )
        with patch(
            "src.core.global_menu_schema.resolve_global_menu_capability",
            return_value=shadow,
        ):
            shadow_history = get_merge_history(conn=conn)
        self.assertFalse(shadow_history["entries"][0]["is_undoable"])
        self.assertIsNone(shadow_history["entries"][0]["global_mutation_id"])

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
        self.assertEqual(item_result["rows_applied"], 1)
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
                    GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
                ),
            ),
        )
        self.assertEqual(rule_result["status"], "applied")
        row = self.conn.execute(
            """
            SELECT locator_scope, restaurant_id, locator_kind,
                   target_global_menu_item_id
            FROM global_menu_mapping_rules
            """
        ).fetchone()
        self.assertEqual(tuple(row), (
            "group",
            None,
            "pos-item",
            "1795ed65318544a9907caab6202b9d42",
        ))
        projected = self.conn.execute(
            """
            SELECT printf('%.2f', price), is_active, addon_eligible,
                   delivery_eligible, is_verified
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()
        self.assertEqual(tuple(projected), ("290.00", 1, 0, 1, 1))

    def test_shared_catalog_materializes_identically_into_two_blank_profiles(self) -> None:
        other = sqlite3.connect(":memory:")
        other.row_factory = sqlite3.Row
        other.execute("PRAGMA foreign_keys=ON")
        apply_analytics_schema(other)
        try:
            projections = []
            for conn, restaurant_id in ((self.conn, "rest-1"), (other, "rest-2")):
                result = apply_global_menu_payload_page(
                    conn,
                    shared_catalog_payload(),
                    stream="snapshot",
                    capability=shared_capability(restaurant_id),
                )
                self.assertEqual(result["rows_materialized"], 2)
                repeat = apply_global_menu_payload_page(
                    conn,
                    shared_catalog_payload(),
                    stream="snapshot",
                    capability=shared_capability(restaurant_id, revision=7),
                )
                self.assertEqual(repeat["rows_materialized"], 2)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM menu_item_variants").fetchone()[0],
                    2,
                )
                projections.append(
                    [
                        tuple(row)
                        for row in conn.execute(
                            """
                            SELECT mv.order_item_id, il.global_menu_item_id,
                                   COALESCE(vl.global_variant_id, ''),
                                   v.variant_name, printf('%.2f', mv.price),
                                   mv.is_active, mv.addon_eligible,
                                   mv.delivery_eligible, mv.is_verified
                            FROM menu_item_variants mv
                            JOIN menu_item_global_links il
                              ON il.local_menu_item_id=mv.menu_item_id
                            JOIN variants v ON v.variant_id=mv.variant_id
                            LEFT JOIN variant_global_links vl
                              ON vl.local_variant_id=mv.variant_id
                            ORDER BY mv.order_item_id
                            """
                        ).fetchall()
                    ]
                )
            self.assertEqual(projections[0], projections[1])
            self.assertEqual(
                projections[0],
                [
                    (
                        "1001",
                        "global-vanilla",
                        "global-regular",
                        "Regular Tub",
                        "290.00",
                        1,
                        0,
                        1,
                        1,
                    ),
                    (
                        "2001",
                        "global-vanilla",
                        "",
                        "UNKNOWN",
                        "40.00",
                        1,
                        0,
                        1,
                        1,
                    ),
                ],
            )
        finally:
            other.close()

    def test_shared_pos_rules_require_capability_scope_and_exact_price(self) -> None:
        invalid_prices = [None, "290", "290.0", 290.0, "-1.00", "NaN", "01.00"]
        for invalid_price in invalid_prices:
            with self.subTest(price=invalid_price):
                with self.assertRaisesRegex(RuntimeError, "price"):
                    apply_global_menu_payload_page(
                        self.conn,
                        shared_catalog_payload(price=invalid_price),
                        stream="snapshot",
                        capability=shared_capability(),
                    )
                self.assertEqual(
                    self.conn.execute("SELECT COUNT(*) FROM global_menu_mapping_rules").fetchone()[0],
                    0,
                )

        with self.assertRaisesRegex(RuntimeError, "requires global_menu_shared_pos_catalog_v1"):
            apply_global_menu_payload_page(
                self.conn,
                shared_catalog_payload(),
                stream="snapshot",
                capability=capability(complete=False),
            )
        wrong_scope = shared_catalog_payload()
        wrong_scope["mapping_rules"][0].update(
            {"locator_scope": "restaurant", "restaurant_id": "rest-1"}
        )
        with self.assertRaisesRegex(RuntimeError, "must be group-scoped"):
            apply_global_menu_payload_page(
                self.conn,
                wrong_scope,
                stream="snapshot",
                capability=shared_capability(),
            )

    def test_shared_projection_rejects_collisions_and_invalid_targets(self) -> None:
        collision = shared_catalog_payload()
        collision["mapping_rules"][1]["locator_value"] = "1001"
        collision["mapping_rules"][1]["normalized_locator"] = "1001"
        with self.assertRaisesRegex(RuntimeError, "both item and addon"):
            apply_global_menu_payload_page(
                self.conn,
                collision,
                stream="snapshot",
                capability=shared_capability(),
            )

        missing = shared_catalog_payload()
        missing["items"] = []
        with self.assertRaisesRegex(RuntimeError, "resolves to missing"):
            apply_global_menu_payload_page(
                self.conn,
                missing,
                stream="snapshot",
                capability=shared_capability(),
            )

        tombstoned = shared_catalog_payload()
        tombstoned["items"][0]["lifecycle_state"] = "tombstoned"
        with self.assertRaisesRegex(RuntimeError, "non-active"):
            apply_global_menu_payload_page(
                self.conn,
                tombstoned,
                stream="snapshot",
                capability=shared_capability(),
            )

    def test_shared_projection_resolves_redirected_rule_targets(self) -> None:
        payload = shared_catalog_payload()
        payload["items"].append(
            {
                "global_menu_item_id": "global-old-vanilla",
                "canonical_name": "Vanilla Ice Cream",
                "canonical_type": "Ice Cream",
                "is_verified": True,
                "lifecycle_state": "redirected",
                "server_revision": 7,
            }
        )
        payload["redirects"] = [
            {
                "redirect_id": "redirect-old-vanilla",
                "entity_type": "item",
                "source_global_menu_item_id": "global-old-vanilla",
                "target_global_menu_item_id": "global-vanilla",
                "server_revision": 7,
            }
        ]
        payload["mapping_rules"][0][
            "target_global_menu_item_id"
        ] = "global-old-vanilla"
        apply_global_menu_payload_page(
            self.conn,
            payload,
            stream="snapshot",
            capability=shared_capability(),
        )
        projected = self.conn.execute(
            """
            SELECT il.global_menu_item_id
            FROM menu_item_variants mv
            JOIN menu_item_global_links il ON il.local_menu_item_id=mv.menu_item_id
            WHERE mv.order_item_id='1001'
            """
        ).fetchone()[0]
        self.assertEqual(projected, "global-vanilla")

    def test_shared_pos_rule_precedes_a_conflicting_local_assignment(self) -> None:
        payload = shared_catalog_payload()
        payload["items"].append(
            {
                "global_menu_item_id": "global-chocolate",
                "canonical_name": "Chocolate Ice Cream",
                "canonical_type": "Ice Cream",
                "is_verified": True,
                "lifecycle_state": "active",
                "server_revision": 7,
            }
        )
        apply_global_menu_payload_page(
            self.conn,
            payload,
            stream="snapshot",
            capability=shared_capability(),
        )
        chocolate_owner = self.conn.execute(
            """
            SELECT local_menu_item_id FROM menu_item_global_links
            WHERE global_menu_item_id='global-chocolate' AND is_projection_owner=1
            """
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE menu_item_variants SET menu_item_id=? WHERE order_item_id='1001'",
            (chocolate_owner,),
        )
        with patch(
            "src.core.global_menu_identity.resolve_global_menu_capability",
            return_value=shared_capability(complete=True, revision=7),
        ):
            resolution = resolve_global_identity_for_ingest(
                self.conn,
                order_item_id="1001",
                raw_name="conflicting local assignment",
            )
        self.assertEqual(resolution.global_menu_item_id, "global-vanilla")
        self.assertEqual(resolution.provenance, "group-pos")

    def test_shared_pos_no_variant_rule_returns_the_local_unknown_sentinel(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            shared_catalog_payload(),
            stream="snapshot",
            capability=shared_capability(),
        )
        with patch(
            "src.core.global_menu_identity.resolve_global_menu_capability",
            return_value=shared_capability(complete=True, revision=7),
        ):
            resolution = resolve_global_identity_for_ingest(
                self.conn,
                order_item_id="2001",
                raw_name="Addon name containing 200ML",
                is_addon=True,
            )
        self.assertEqual(resolution.provenance, "group-pos")
        self.assertIsNone(resolution.global_variant_id)
        self.assertEqual(
            resolution.local_variant_id,
            generate_deterministic_id("UNKNOWN"),
        )

    def test_tombstoned_shared_rule_restores_prior_local_availability_on_readd(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            shared_catalog_payload(),
            stream="snapshot",
            capability=shared_capability(),
        )
        self.conn.execute(
            """
            UPDATE menu_item_variants
            SET addon_eligible=1, delivery_eligible=0
            WHERE order_item_id='1001'
            """
        )
        self.conn.execute(
            "DELETE FROM global_menu_mapping_rules WHERE rule_id='shared-item-1001'"
        )
        materialize_shared_pos_catalog(
            self.conn,
            shared_capability(revision=8),
            tombstoned_locators=(("pos-item", "1001"),),
        )
        tombstoned = self.conn.execute(
            """
            SELECT is_active, addon_eligible, delivery_eligible,
                   shared_pos_rule_tombstoned, shared_pos_prior_is_active
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()
        self.assertEqual(tuple(tombstoned), (0, 1, 0, 1, 1))

        apply_global_menu_payload_page(
            self.conn,
            shared_catalog_payload(revision=9, price="315.00"),
            stream="snapshot",
            capability=shared_capability(revision=8),
        )
        restored = self.conn.execute(
            """
            SELECT printf('%.2f', price), is_active, addon_eligible,
                   delivery_eligible, shared_pos_rule_tombstoned,
                   shared_pos_prior_is_active
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()
        self.assertEqual(tuple(restored), ("315.00", 1, 1, 0, 0, None))

    def test_shared_pos_rule_rejects_a_conflicting_assignment_snapshot_row(self) -> None:
        payload = shared_catalog_payload()
        payload["items"].append(
            {
                "global_menu_item_id": "global-chocolate",
                "canonical_name": "Chocolate Ice Cream",
                "canonical_type": "Ice Cream",
                "is_verified": True,
                "lifecycle_state": "active",
                "server_revision": 7,
            }
        )
        apply_global_menu_payload_page(
            self.conn,
            payload,
            stream="snapshot",
            capability=shared_capability(),
        )
        current = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        with self.assertRaisesRegex(RuntimeError, "conflicts with its shared POS rule"):
            apply_global_assignment_rows(
                self.conn,
                [
                    {
                        "order_item_id": "1001",
                        "menu_item_id": current[0],
                        "variant_id": current[1],
                        "global_menu_item_id": "global-chocolate",
                        "global_variant_id": "global-regular",
                        "last_seq": 8,
                    }
                ],
                server_revision=7,
                capability=shared_capability(revision=7, complete=True),
            )
        projected = self.conn.execute(
            """
            SELECT il.global_menu_item_id
            FROM menu_item_variants mv
            JOIN menu_item_global_links il ON il.local_menu_item_id=mv.menu_item_id
            WHERE mv.order_item_id='1001'
            """
        ).fetchone()[0]
        self.assertEqual(projected, "global-vanilla")

    def test_price_event_preserves_store_flags_and_historical_order_prices(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            shared_catalog_payload(),
            stream="snapshot",
            capability=shared_capability(),
        )
        mapping = self.conn.execute(
            "SELECT menu_item_id, variant_id FROM menu_item_variants WHERE order_item_id='1001'"
        ).fetchone()
        self.conn.execute(
            """
            UPDATE menu_item_variants
            SET is_active=0, addon_eligible=1, delivery_eligible=0,
                is_verified=0, assignment_seq=44, verification_seq=45,
                pending_local=1
            WHERE order_item_id='1001'
            """
        )
        self.conn.execute(
            """
            INSERT INTO orders (
                order_id, petpooja_order_id, stream_id, event_id, occurred_at,
                created_on, order_type, order_from, order_status
            ) VALUES (1, 1, 1, 'historical-event', '2026-08-09T10:00:00Z',
                      '2026-08-09 15:30:00', 'Delivery', 'POS', 'Success')
            """
        )
        self.conn.execute(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id,
                petpooja_itemid, name_raw, quantity, unit_price, total_price
            ) VALUES (1, 1, ?, ?, 1001, 'Vanilla', 2, 290, 580)
            """,
            tuple(mapping),
        )
        self.conn.commit()

        updated_rule = dict(shared_catalog_payload(revision=8, price="310.00")["mapping_rules"][0])
        event_page = {
            "schema_version": 1,
            "menu_group_id": "group-desserts",
            "catalog_revision": 8,
            "mutation_revision": 8,
            "next_cursor": "8",
            "has_more": False,
            "events": [
                {
                    "event_id": "price-event-8",
                    "mutation_id": "price-mutation-8",
                    "event_type": "global_locator.price_update",
                    "catalog_revision": 8,
                    "payload": {
                        "items": [],
                        "variants": [],
                        "redirects": [],
                        "mapping_rules": [updated_rule],
                        "tombstones": {"redirects": [], "mapping_rules": []},
                        "action": {"mutation_type": "global_locator.price_update"},
                    },
                }
            ],
        }
        result = apply_global_menu_payload_page(
            self.conn,
            event_page,
            stream="events",
            capability=shared_capability(revision=7, complete=True),
        )
        self.assertEqual(result["rows_materialized"], 2)
        refreshed = self.conn.execute(
            """
            SELECT printf('%.2f', price), is_active, addon_eligible,
                   delivery_eligible, is_verified, assignment_seq,
                   verification_seq, pending_local
            FROM menu_item_variants WHERE order_item_id='1001'
            """
        ).fetchone()
        self.assertEqual(tuple(refreshed), ("310.00", 0, 1, 0, 0, 44, 45, 1))
        historical = self.conn.execute(
            "SELECT printf('%.2f', unit_price), printf('%.2f', total_price) FROM order_items"
        ).fetchone()
        self.assertEqual(tuple(historical), ("290.00", "580.00"))

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

        self.assertEqual(result["rows_applied"], 1)
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
                    GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
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
                    GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
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
            1,
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
                                    "rule_scope": "group",
                                    "restaurant_id": "",
                                    "locator_type": "pos_item",
                                    "locator_value": "1001",
                                }
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
                    GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
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
        self.assertEqual(
            self.conn.execute(
                "SELECT is_active FROM menu_item_variants WHERE order_item_id='1001'"
            ).fetchone()[0],
            0,
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
                GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY,
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
            **contract_fixture("global_menu_snapshot_items_empty")["payload"],
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

    def test_shared_snapshot_materialization_failure_rolls_back_and_quarantines(self) -> None:
        apply_global_menu_payload_page(
            self.conn,
            shared_catalog_payload(),
            stream="snapshot",
            capability=shared_capability(),
        )
        update_global_menu_state(
            self.conn,
            bootstrap_status="not_started",
            snapshot_cursor=None,
        )
        self.conn.commit()

        def snapshot_page(section: str, rows: list[Dict[str, Any]]) -> Dict[str, Any]:
            return {
                "schema_version": 1,
                "menu_group_id": "group-desserts",
                "menu_group_revision": 8,
                "snapshot_watermark": {
                    "event_seq": 8,
                    "menu_group_revision": 8,
                },
                "section": section,
                "rows": rows,
                "next_cursor": None,
                "has_more": False,
            }

        item = {
            "global_item_id": "global-vanilla",
            "canonical_name": "Eggless Vanilla Ice Cream",
            "canonical_type": "Ice Cream",
            "is_verified": True,
            "lifecycle_state": "active",
        }
        variant = {
            "global_variant_id": "global-regular",
            "canonical_name": "Regular Tub",
            "unit": "GMS",
            "value": "300.00",
            "is_verified": True,
            "lifecycle_state": "active",
        }
        colliding_rules = [
            {
                "rule_scope": "group",
                "restaurant_id": "",
                "locator_type": locator_type,
                "locator_value": "3001",
                "global_item_id": "global-vanilla",
                "global_variant_id": "global-regular",
                "price": price,
                "provenance": "fixture",
                "menu_group_revision": 8,
            }
            for locator_type, price in (("pos_item", "310.00"), ("pos_addon", "40.00"))
        ]
        cap = shared_capability(revision=7)
        pages = [
            snapshot_page("items", [item]),
            snapshot_page("variants", [variant]),
            snapshot_page("redirects", []),
            snapshot_page("rules", colliding_rules),
        ]
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
            "src.core.global_menu_sync.get_global_menu_snapshot_endpoint",
            return_value="https://cloud/snapshot",
        ), patch(
            "src.core.global_menu_sync.get_global_menu_events_endpoint",
            return_value="https://cloud/events",
        ), patch(
            "src.core.global_menu_sync._fetch_page",
            side_effect=[{"error": None, **page} for page in pages],
        ):
            result = pull_global_menu_state(self.conn, auth="sync-key")

        self.assertEqual(result["status"], "error")
        self.assertIn("both item and addon", result["error"])
        self.assertEqual(
            self.conn.execute(
                "SELECT printf('%.2f', price) FROM menu_item_variants WHERE order_item_id='1001'"
            ).fetchone()[0],
            "290.00",
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM global_menu_mapping_rules").fetchone()[0],
            2,
        )
        quarantine = self.conn.execute(
            """
            SELECT error_code FROM global_menu_sync_quarantine
            WHERE resolved_at IS NULL
            """
        ).fetchone()
        self.assertEqual(quarantine[0], "global_menu_locator_kind_collision")
        state = self.conn.execute(
            "SELECT bootstrap_status, catalog_revision FROM global_menu_state WHERE singleton_id=1"
        ).fetchone()
        self.assertEqual(tuple(state), ("error", 7))

    def test_shared_pull_commits_four_sections_then_drains_the_pinned_tail(self) -> None:
        payload = shared_catalog_payload()

        def snapshot_page(section: str, rows: list[Dict[str, Any]]) -> Dict[str, Any]:
            return {
                "schema_version": 1,
                "menu_group_id": "group-desserts",
                "menu_group_revision": 7,
                "snapshot_watermark": {
                    "event_seq": 7,
                    "menu_group_revision": 7,
                },
                "section": section,
                "rows": rows,
                "next_cursor": None,
                "has_more": False,
            }

        item_rows = [
            {
                "global_item_id": row["global_menu_item_id"],
                "canonical_name": row["canonical_name"],
                "canonical_type": row["canonical_type"],
                "is_verified": row["is_verified"],
                "lifecycle_state": row["lifecycle_state"],
            }
            for row in payload["items"]
        ]
        variant_rows = [
            {
                "global_variant_id": row["global_variant_id"],
                "canonical_name": row["canonical_name"],
                "unit": row["unit"],
                "value": "300.00",
                "is_verified": row["is_verified"],
                "lifecycle_state": row["lifecycle_state"],
            }
            for row in payload["variants"]
        ]
        rule_rows = [
            {
                "rule_scope": "group",
                "restaurant_id": "",
                "locator_type": (
                    "pos_item" if row["locator_kind"] == "pos-item" else "pos_addon"
                ),
                "locator_value": row["locator_value"],
                "global_item_id": row["target_global_menu_item_id"],
                "global_variant_id": row["target_global_variant_id"] or "",
                "price": row["price"],
                "provenance": row["provenance"],
                "menu_group_revision": 7,
            }
            for row in payload["mapping_rules"]
        ]
        event_tail = {
            "schema_version": 1,
            "menu_group_id": "group-desserts",
            "menu_group_revision": 7,
            "event_head_seq": 7,
            "events": [],
            "next_cursor": 7,
            "has_more": False,
        }
        pages = [
            snapshot_page("items", item_rows),
            snapshot_page("variants", variant_rows),
            snapshot_page("redirects", []),
            snapshot_page("rules", rule_rows),
            event_tail,
        ]

        def current_capability(*_args: Any, **_kwargs: Any) -> GlobalMenuCapabilityStatus:
            row = self.conn.execute(
                """
                SELECT catalog_revision, mutation_revision, snapshot_cursor,
                       event_cursor, bootstrap_status, coverage_linked,
                       coverage_total
                FROM global_menu_state WHERE singleton_id=1
                """
            ).fetchone()
            return replace(
                shared_capability(revision=int(row[0] or 0)),
                mutation_revision=int(row[1] or 0),
                snapshot_cursor=row[2],
                event_cursor=row[3],
                bootstrap_status=str(row[4]),
                coverage_linked=int(row[5] or 0),
                coverage_total=int(row[6] or 0),
            )

        with patch(
            "src.core.global_menu_sync.require_global_menu_capability",
            side_effect=current_capability,
        ), patch(
            "src.core.global_menu_sync.resolve_global_menu_capability",
            side_effect=current_capability,
        ), patch(
            "src.core.global_menu_sync.pull_global_menu_status",
            return_value={"status": "applied"},
        ), patch(
            "src.core.global_menu_sync.get_global_menu_snapshot_endpoint",
            return_value="https://cloud/snapshot",
        ), patch(
            "src.core.global_menu_sync.get_global_menu_events_endpoint",
            return_value="https://cloud/events",
        ), patch(
            "src.core.global_menu_sync._fetch_page",
            side_effect=[{"error": None, **page} for page in pages],
        ) as transport:
            result = pull_global_menu_state(self.conn, auth="sync-key")

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["stream"], "events")
        self.assertEqual(transport.call_count, 5)
        state = self.conn.execute(
            """
            SELECT bootstrap_status, catalog_revision, event_cursor
            FROM global_menu_state WHERE singleton_id=1
            """
        ).fetchone()
        self.assertEqual(tuple(state), ("complete", 7, "7"))
        self.assertEqual(
            [
                tuple(row)
                for row in self.conn.execute(
                    """
                    SELECT order_item_id, printf('%.2f', price)
                    FROM menu_item_variants ORDER BY order_item_id
                    """
                ).fetchall()
            ],
            [("1001", "290.00"), ("2001", "40.00")],
        )

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

    def test_two_restaurant_pos_ids_and_alias_resolve_to_one_global_item(self) -> None:
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
                alias = resolve_global_identity_for_ingest(
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
            self.assertEqual(alias.global_menu_item_id, "global-vanilla")
            self.assertEqual(alias.provenance, "global-alias")
            self.assertFalse(unknown.resolved)
            if conn is not self.conn:
                conn.close()

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

    def test_shadow_item_create_preview_is_committable_with_incomplete_coverage(self) -> None:
        shadow = replace(
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
            "menu_group_id": shadow.menu_group_id,
            "menu_group_revision": shadow.mutation_revision,
            "mutation_type": "global_item.create",
            "payload": payload,
            "preview_digest": "digest-create",
            "revision_current": True,
            "conflicts": [],
            "commit_allowed": True,
        }
        with patch(
            "src.core.global_menu_mutation.require_global_menu_capability",
            return_value=shadow,
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
        ) as assignments:
            _apply_accepted_projection(Mock(), {})
        catalog.assert_called_once()
        assignments.assert_called_once()

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
                petpooja_itemid, name_raw, quantity, unit_price, total_price
            ) VALUES (1, 1, 'local-kulfi', 'local-regular', 7777,
                      'Kulfi', 1, 100, 100)
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
                ("alias", "Kulfi"),
            },
        )
        scopes = {row["locator_type"]: row["rule_scope"] for row in context["locators"]}
        self.assertEqual(scopes["pos_item"], "restaurant")
        self.assertEqual(scopes["pos_addon"], "restaurant")
        self.assertEqual(scopes["alias"], "group")

    def test_verified_unlinked_pair_enters_shadow_resolution_queue_only(self) -> None:
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
            "employee": commit_fixture["request"]["uploaded_by"],
            "device": commit_fixture["request"]["uploaded_from"],
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
        }
        self.assertEqual(sent, expected)
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
