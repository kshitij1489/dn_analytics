"""
Phase C4: fresh-install fast path (snapshot → cursor at watermark → tail) and
bootstrap demotion (seed-only default, snapshot_role + hash-skip on the shipper).
"""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from tests.profile_test_helpers import bind_test_profile

from src.core.menu_assignment_bootstrap import (
    MENU_ASSIGNMENTS_BOOTSTRAPPED_KEY,
    bootstrap_menu_assignments_if_needed,
)
from src.core.menu_bootstrap_sync import (
    DEFAULT_MENU_BOOTSTRAP_APPLY_MODE,
    apply_menu_bootstrap_snapshot,
    get_menu_bootstrap_apply_mode,
)
from src.core.menu_mapping_verification_sync import (
    get_menu_mapping_verification_pull_cursor,
)
from src.core.menu_merge_sync import get_menu_merge_pull_cursor, set_menu_merge_pull_cursor
from tests.test_menu_assignment_apply import (
    FakeEventServer,
    make_install_db,
    pull_install,
    route_commits_through_server,
)
from utils.id_generator import generate_deterministic_id
from utils import menu_utils


def _assignment_digest(conn):
    """Ordered acknowledged assignments — the convergence fingerprint."""
    return conn.execute(
        """
        SELECT order_item_id, menu_item_id, variant_id, is_verified
        FROM menu_item_variants
        WHERE pending_local = 0 OR pending_local IS NULL
        ORDER BY order_item_id
        """
    ).fetchall()


def _snapshot_rows_from_install(conn):
    rows = conn.execute(
        """
        SELECT order_item_id, menu_item_id, variant_id, is_verified, assignment_seq
        FROM menu_item_variants
        ORDER BY order_item_id
        """
    ).fetchall()
    return [
        {
            "order_item_id": str(row["order_item_id"]),
            "menu_item_id": str(row["menu_item_id"]),
            "variant_id": row["variant_id"],
            "is_verified": int(row["is_verified"] or 0),
            "last_seq": row["assignment_seq"],
        }
        for row in rows
    ]


def _fake_snapshot_fetch(
    rows,
    watermark_seq,
    watermark_cursor,
    page_size=2,
    verification_watermark_cursor=None,
):
    ordered = sorted(rows, key=lambda row: row["order_item_id"])

    def _fetch(conn, endpoint, auth, after, limit):
        remaining = [row for row in ordered if not after or row["order_item_id"] > after]
        page = remaining[:page_size]
        return {
            "error": None,
            "assignments": page,
            "watermark_seq": watermark_seq,
            "watermark_cursor": watermark_cursor,
            "verification_watermark_cursor": verification_watermark_cursor,
            "next_page": page[-1]["order_item_id"] if len(page) == page_size else None,
        }

    return _fetch


class MenuAssignmentBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        for target in (
            patch("utils.menu_utils._clear_impacted_models", return_value=None),
        ):
            target.start()
            self.addCleanup(target.stop)

    def test_fresh_install_snapshot_digest_equals_replay_digest(self) -> None:
        replayed = make_install_db()
        self.addCleanup(replayed.close)
        server = FakeEventServer()
        router = route_commits_through_server(server)
        router.start()
        self.addCleanup(router.stop)

        result = menu_utils.merge_menu_items(replayed, "item_a", "item_b")
        self.assertEqual(result["status"], "success")
        result = menu_utils.resolve_menu_item_variant(
            replayed,
            source_menu_item_id="item_b",
            source_variant_id=menu_utils.NULL_VARIANT_SENTINEL,
            target_menu_item_id="item_b",
            target_variant_id="variant_x",
        )
        self.assertEqual(result["status"], "success", result.get("message"))
        # The strict commits above already fed `server` and self-applied; pulling
        # our own echo is a dedupe no-op that leaves the replay state settled.
        pull_install(replayed, server)

        snapshot_rows = _snapshot_rows_from_install(replayed)
        watermark_seq = len(server.rows)
        watermark_cursor = str(len(server.rows))

        verification_watermark_cursor = "verif-" + str(len(server.rows))

        fresh = make_install_db()
        self.addCleanup(fresh.close)
        with patch(
            "src.core.menu_assignment_bootstrap._fetch_snapshot_page",
            side_effect=_fake_snapshot_fetch(
                snapshot_rows,
                watermark_seq,
                watermark_cursor,
                verification_watermark_cursor=verification_watermark_cursor,
            ),
        ):
            outcome = bootstrap_menu_assignments_if_needed(fresh, "http://fake/snapshot")

        self.assertEqual(outcome["status"], "bootstrapped")
        self.assertTrue(outcome["cursor_set"])
        self.assertEqual(get_menu_merge_pull_cursor(fresh), watermark_cursor)
        # The verification cursor is seeded to the snapshot's verification
        # watermark, so the fresh install tails the flag stream instead of
        # replaying it from zero.
        self.assertTrue(outcome["verification_cursor_set"])
        self.assertEqual(
            get_menu_mapping_verification_pull_cursor(fresh),
            verification_watermark_cursor,
        )

        # Tail from the watermark fetches nothing new and changes nothing.
        stats = pull_install(fresh, server)
        self.assertEqual(stats["merge_events_applied"], 0)

        self.assertEqual(
            [tuple(row) for row in _assignment_digest(fresh)],
            [tuple(row) for row in _assignment_digest(replayed)],
        )

        # Idempotent: a second call is a no-op.
        second = bootstrap_menu_assignments_if_needed(fresh, "http://fake/snapshot")
        self.assertEqual(second["status"], "already_bootstrapped")

    def test_existing_install_with_cursor_is_not_reseeded(self) -> None:
        conn = make_install_db()
        self.addCleanup(conn.close)
        set_menu_merge_pull_cursor(conn, "42")
        conn.commit()

        fetch = MagicMock()
        with patch("src.core.menu_assignment_bootstrap._fetch_snapshot_page", fetch):
            outcome = bootstrap_menu_assignments_if_needed(conn, "http://fake/snapshot")

        self.assertEqual(outcome["status"], "existing_install")
        fetch.assert_not_called()
        flag = conn.execute(
            "SELECT value FROM system_config WHERE key = ?",
            (MENU_ASSIGNMENTS_BOOTSTRAPPED_KEY,),
        ).fetchone()
        self.assertEqual(flag[0], "existing-install")

    def test_fetch_error_leaves_bootstrap_unmarked(self) -> None:
        conn = make_install_db()
        self.addCleanup(conn.close)
        with patch(
            "src.core.menu_assignment_bootstrap._fetch_snapshot_page",
            return_value={"error": "HTTP 404"},
        ):
            outcome = bootstrap_menu_assignments_if_needed(conn, "http://fake/snapshot")
        self.assertEqual(outcome["status"], "error")
        flag = conn.execute(
            "SELECT value FROM system_config WHERE key = ?",
            (MENU_ASSIGNMENTS_BOOTSTRAPPED_KEY,),
        ).fetchone()
        self.assertIsNone(flag)

    def test_bootstrap_apply_mode_defaults_to_seed_only(self) -> None:
        self.assertEqual(DEFAULT_MENU_BOOTSTRAP_APPLY_MODE, "seed_only")
        conn = make_install_db()
        self.addCleanup(conn.close)
        self.assertEqual(get_menu_bootstrap_apply_mode(conn), "seed_only")

        # Restore/support flows can re-enable relinking explicitly.
        with patch.dict("os.environ", {"MENU_BOOTSTRAP_APPLY_MODE": "seed_and_relink_orders"}):
            self.assertEqual(get_menu_bootstrap_apply_mode(conn), "seed_and_relink_orders")
        conn.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('menu_bootstrap_apply_mode', 'seed_and_relink_orders')"
        )
        self.assertEqual(get_menu_bootstrap_apply_mode(conn), "seed_and_relink_orders")

    def test_bootstrap_pull_updates_existing_catalog_without_shipper(self) -> None:
        conn = make_install_db()
        self.addCleanup(conn.close)

        beverage_type_id = generate_deterministic_id("Beverage")
        id_maps = {
            "menu_id_to_str": {
                "item_b": "Server Cold Brew",
            },
            "variant_id_to_str": {
                "variant_x": "SERVER_SIZE",
            },
            "variant_id_to_meta": {
                "variant_x": {"unit": "ML", "value": 300},
            },
            "type_id_to_str": {
                beverage_type_id: "Beverage",
            },
        }
        cluster_state = {
            f"item_b:{beverage_type_id}": {
                "2": [["2", "variant_x"]],
            },
        }

        with patch("src.core.menu_bootstrap_shipper.upload_pending") as upload_pending:
            result = apply_menu_bootstrap_snapshot(conn, id_maps, cluster_state)

        self.assertIsNone(result["error"])
        upload_pending.assert_not_called()
        item = conn.execute(
            "SELECT name, type FROM menu_items WHERE menu_item_id = 'item_b'"
        ).fetchone()
        self.assertEqual(dict(item), {"name": "Server Cold Brew", "type": "Beverage"})
        variant = conn.execute(
            "SELECT variant_name, unit, value FROM variants WHERE variant_id = 'variant_x'"
        ).fetchone()
        self.assertEqual(variant["variant_name"], "SERVER_SIZE")
        self.assertEqual(variant["unit"], "ML")
        self.assertEqual(variant["value"], 300)


class MenuBootstrapShipperTests(unittest.TestCase):
    def _make_catalog_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                is_verified BOOLEAN DEFAULT 0
            );

            CREATE TABLE variants (
                variant_id TEXT PRIMARY KEY,
                variant_name TEXT NOT NULL,
                unit TEXT,
                value REAL,
                is_verified BOOLEAN DEFAULT 0
            );

            CREATE TABLE menu_item_variants (
                order_item_id TEXT PRIMARY KEY,
                menu_item_id TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                price DECIMAL(10,2) DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                is_verified BOOLEAN DEFAULT 0
            );

            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                created_on TEXT NOT NULL
            );

            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                petpooja_itemid TEXT,
                unit_price DECIMAL(10,2)
            );

            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER NOT NULL,
                petpooja_addonid TEXT,
                price DECIMAL(10,2)
            );

            CREATE TABLE system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        bind_test_profile(conn)
        conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES ('item_a', 'a', 'Dessert', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES ('variant_a', '1_PIECE', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES (?, 'UNKNOWN', 1)
            """,
            (generate_deterministic_id("UNKNOWN"),),
        )
        conn.execute(
            """
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES ('101', 'item_a', 'variant_a', 1)
            """
        )
        conn.execute("INSERT INTO orders VALUES (1, '2026-08-11 10:00:00')")
        conn.execute(
            "INSERT INTO order_items VALUES (1, 1, '101', 120.00)"
        )
        conn.commit()
        return conn

    def test_builds_deterministic_item_addon_and_no_variant_observation(self) -> None:
        from src.core.menu_catalog_seed import build_shared_pos_catalog

        conn = self._make_catalog_db()
        self.addCleanup(conn.close)
        conn.execute(
            "UPDATE variants SET unit = 'piece', value = 1 WHERE variant_id = 'variant_a'"
        )
        conn.execute(
            "INSERT INTO menu_items VALUES ('item_b', '  Kulfi   Addon ', 'Addon', 1)"
        )
        conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_active, is_verified
            ) VALUES (?, 'item_b', ?, ?, 1)
            """,
            [
                ("addon-2", generate_deterministic_id("UNKNOWN"), 1),
                ("102", generate_deterministic_id("UNKNOWN"), 1),
                ("103", "variant_a", 0),
            ],
        )
        conn.executemany(
            "INSERT INTO order_items VALUES (?, 1, ?, ?)",
            [(2, "102", "90"), (3, "103", "999")],
        )
        conn.execute(
            "INSERT INTO order_item_addons VALUES (1, 1, 'addon-2', '35.5')"
        )

        self.assertEqual(
            build_shared_pos_catalog(conn),
            [
                {
                    "locator_type": "pos_addon",
                    "locator_value": "addon-2",
                    "menu_item_id": "item_b",
                    "variant_id": None,
                    "item_name": "Kulfi Addon",
                    "item_type": "Addon",
                    "variant_name": None,
                    "variant_unit": None,
                    "variant_value": None,
                    "price": "35.50",
                },
                {
                    "locator_type": "pos_item",
                    "locator_value": "101",
                    "menu_item_id": "item_a",
                    "variant_id": "variant_a",
                    "item_name": "a",
                    "item_type": "Dessert",
                    "variant_name": "1_PIECE",
                    "variant_unit": "piece",
                    "variant_value": "1.00",
                    "price": "120.00",
                },
                {
                    "locator_type": "pos_item",
                    "locator_value": "102",
                    "menu_item_id": "item_b",
                    "variant_id": None,
                    "item_name": "Kulfi Addon",
                    "item_type": "Addon",
                    "variant_name": None,
                    "variant_unit": None,
                    "variant_value": None,
                    "price": "90.00",
                },
            ],
        )

    def test_uses_most_recent_raw_price_for_a_locator(self) -> None:
        from src.core.menu_catalog_seed import build_shared_pos_catalog

        conn = self._make_catalog_db()
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO orders VALUES (2, '2026-08-11 11:00:00')")
        conn.execute("INSERT INTO order_items VALUES (2, 2, '101', '135.75')")

        catalog = build_shared_pos_catalog(conn)

        self.assertEqual(catalog[0]["price"], "135.75")

    def test_rejects_locator_kind_collision(self) -> None:
        from src.core.menu_catalog_seed import build_shared_pos_catalog

        conn = self._make_catalog_db()
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO order_item_addons VALUES (1, 1, '101', 25)")

        with self.assertRaisesRegex(ValueError, "both"):
            build_shared_pos_catalog(conn)

    def test_rejects_invalid_price_and_variant_decimals(self) -> None:
        from src.core.menu_catalog_seed import build_shared_pos_catalog

        for invalid_price in ("-1", "NaN", "Infinity", "12.345", "bad"):
            with self.subTest(price=invalid_price):
                conn = self._make_catalog_db()
                conn.execute(
                    "UPDATE order_items SET unit_price = ? WHERE petpooja_itemid = '101'",
                    (invalid_price,),
                )
                with self.assertRaisesRegex(ValueError, "Invalid price"):
                    build_shared_pos_catalog(conn)
                conn.close()

        conn = self._make_catalog_db()
        self.addCleanup(conn.close)
        conn.execute("UPDATE variants SET value = '-0.01' WHERE variant_id = 'variant_a'")
        with self.assertRaisesRegex(ValueError, "Invalid variant_value"):
            build_shared_pos_catalog(conn)

    def test_bootstrap_observation_hash_is_canonical(self) -> None:
        from src.core.menu_bootstrap_shipper import _hash_bootstrap_observation

        row = {
            "locator_type": "pos_item",
            "locator_value": "101",
            "price": "120.00",
        }
        reverse_row = dict(reversed(list(row.items())))
        self.assertEqual(
            _hash_bootstrap_observation({"b": 2, "a": 1}, [row]),
            _hash_bootstrap_observation({"a": 1, "b": 2}, [reverse_row]),
        )

    def test_unconfirmed_observation_is_retried_without_persisting_hash(self) -> None:
        from src.core import menu_bootstrap_shipper

        conn = self._make_catalog_db()
        self.addCleanup(conn.close)
        refused = MagicMock(status_code=200)
        refused.json.return_value = {"shared_pos_catalog_updated": False}
        accepted = MagicMock(status_code=200)
        accepted.json.return_value = {"shared_pos_catalog_updated": True}

        with patch("requests.post", side_effect=[refused, accepted]) as post:
            first = menu_bootstrap_shipper.upload_pending(
                conn, endpoint="http://fake/ingest"
            )
            self.assertFalse(first["sent"])
            self.assertIsNone(
                conn.execute(
                    "SELECT value FROM system_config WHERE key=?",
                    (menu_bootstrap_shipper.LAST_PUSH_HASH_KEY,),
                ).fetchone()
            )

            second = menu_bootstrap_shipper.upload_pending(
                conn, endpoint="http://fake/ingest"
            )
            self.assertTrue(second["sent"])
            self.assertEqual(post.call_count, 2)
            self.assertIsNotNone(
                conn.execute(
                    "SELECT value FROM system_config WHERE key=?",
                    (menu_bootstrap_shipper.LAST_PUSH_HASH_KEY,),
                ).fetchone()
            )

    def test_sends_seed_only_role_and_skips_unchanged_id_maps(self) -> None:
        from src.core import menu_bootstrap_shipper

        conn = self._make_catalog_db()
        self.addCleanup(conn.close)

        response = MagicMock(status_code=200)
        response.json.return_value = {"shared_pos_catalog_updated": True}
        with patch("requests.post", return_value=response) as post:
                first = menu_bootstrap_shipper.upload_pending(conn, endpoint="http://fake/ingest")
                self.assertTrue(first["sent"])
                payload = post.call_args.kwargs["json"]
                self.assertEqual(payload["snapshot_role"], "seed_only")
                self.assertEqual(payload["id_maps"]["menu_id_to_str"], {"item_a": "a"})
                self.assertEqual(
                    payload["shared_pos_catalog"],
                    [
                        {
                            "locator_type": "pos_item",
                            "locator_value": "101",
                            "menu_item_id": "item_a",
                            "variant_id": "variant_a",
                            "item_name": "a",
                            "item_type": "Dessert",
                            "variant_name": "1_PIECE",
                            "variant_unit": None,
                            "variant_value": None,
                            "price": "120.00",
                        }
                    ],
                )
                self.assertEqual(
                    payload["cluster_state"][next(iter(payload["cluster_state"]))],
                    {"101": [["101", "variant_a"]]},
                )
                stored_hash = conn.execute(
                    "SELECT value FROM system_config WHERE key=?",
                    (menu_bootstrap_shipper.LAST_PUSH_HASH_KEY,),
                ).fetchone()
                self.assertIsNotNone(stored_hash)

                # Same id_maps and observation → skipped without an HTTP call.
                second = menu_bootstrap_shipper.upload_pending(conn, endpoint="http://fake/ingest")
                self.assertFalse(second["sent"])
                self.assertEqual(
                    second.get("skipped"),
                    "id_maps + shared_pos_catalog unchanged",
                )
                self.assertEqual(post.call_count, 1)

                # force=True pushes anyway.
                forced = menu_bootstrap_shipper.upload_pending(
                    conn, endpoint="http://fake/ingest", force=True
                )
                self.assertTrue(forced["sent"])
                self.assertEqual(post.call_count, 2)

                # A price-only observation change must not be hidden by the
                # unchanged legacy id_maps hash.
                conn.execute(
                    "UPDATE order_items SET unit_price = 125.50 WHERE petpooja_itemid = '101'"
                )
                conn.commit()
                price_changed = menu_bootstrap_shipper.upload_pending(
                    conn, endpoint="http://fake/ingest"
                )
                self.assertTrue(price_changed["sent"])
                self.assertEqual(post.call_count, 3)
                self.assertEqual(
                    post.call_args.kwargs["json"]["shared_pos_catalog"][0]["price"],
                    "125.50",
                )

                # Catalog change → pushed again.
                conn.execute(
                    """
                    INSERT INTO menu_items (menu_item_id, name, type, is_verified)
                    VALUES ('item_b', 'b', 'Dessert', 1)
                    """
                )
                conn.commit()
                third = menu_bootstrap_shipper.upload_pending(conn, endpoint="http://fake/ingest")
                self.assertTrue(third["sent"])
                self.assertEqual(post.call_count, 4)


if __name__ == "__main__":
    unittest.main()
