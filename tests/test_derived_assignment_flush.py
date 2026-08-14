import sqlite3
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from src.core.db.connection import apply_analytics_schema
from src.core.derived_assignment_flush import flush_pending_derived_assignments
from src.core.menu_mutation_commit import (
    CommitResult,
    MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC,
)
from src.core.services.cloud_pull_orchestrator import run_best_effort_cloud_pulls
from tests.profile_test_helpers import bind_test_profile
from utils.menu_item_variant_enforcement import (
    addon_seeded_mapping_order_item_id,
    catalog_stub_order_item_id,
)

# Sentinel: distinguishes "patch the flush with this result" from "let the real
# flush run", which `None` cannot do — `None` is a result a flush could return.
_REAL_FLUSH = object()


class DerivedAssignmentFlushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
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
                variant_id TEXT,
                is_verified BOOLEAN DEFAULT 0,
                assignment_seq INTEGER,
                pending_local INTEGER DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                restaurant_id INTEGER
            );
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                order_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT,
                petpooja_itemid INTEGER,
                name_raw TEXT,
                updated_at TEXT
            );
            CREATE TABLE order_item_addons (
                order_item_addon_id INTEGER PRIMARY KEY,
                order_item_id INTEGER,
                menu_item_id TEXT,
                variant_id TEXT
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES (?, ?, ?, ?)",
            [
                ("item_a", "Orange Ice Cream", "Ice Cream", 0),
                ("item_b", "Mango Ice Cream", "Ice Cream", 0),
            ],
        )
        self.conn.execute(
            "INSERT INTO variants (variant_id, variant_name, unit, value, is_verified) VALUES ('variant_a', 'SCOOP', NULL, NULL, 0)"
        )
        self.conn.execute("INSERT INTO orders (order_id, restaurant_id) VALUES (1, 1)")
        self.conn.executemany(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id, petpooja_itemid, name_raw
            )
            VALUES (?, 1, ?, ?, ?, ?)
            """,
            [
                (1, "item_a", "variant_a", 101, "Orange Ice Cream Scoop"),
                (2, "item_b", "variant_a", 202, "Mango Ice Cream Scoop"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified, assignment_seq, pending_local
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("101", "item_a", "variant_a", 1, None, 0),
                ("202", "item_b", "variant_a", 0, 44, 0),
                ("303", "item_a", "variant_a", 0, None, 0),
                ("404", "item_a", "variant_a", 0, None, 1),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @patch("src.core.derived_assignment_flush.strict_mode_active", return_value=True)
    @patch("src.core.derived_assignment_flush.commit_mutation")
    def test_flush_sends_only_pos_backed_unacked_rows_as_unverified(self, commit, _strict) -> None:
        commit.return_value = CommitResult(status="ok")

        summary = flush_pending_derived_assignments(self.conn, max_batches=1)

        self.assertEqual(summary["sent"], 1)
        self.assertEqual(commit.call_count, 1)
        plan = commit.call_args.args[1]
        self.assertEqual(plan.mutation_type, MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC)
        assignments = plan.event["merge_payload"]["assignments"]
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["order_item_id"], "101")
        self.assertEqual(assignments[0]["is_verified"], 0)
        self.assertEqual(plan.catalog_delta["items"][0]["menu_item_id"], "item_a")
        self.assertEqual(plan.catalog_delta["variants"][0]["variant_id"], "variant_a")

    @patch("src.core.derived_assignment_flush.strict_mode_active", return_value=False)
    def test_flush_noops_when_strict_not_ready(self, _strict) -> None:
        summary = flush_pending_derived_assignments(self.conn)

        self.assertFalse(summary["attempted"])
        self.assertEqual(summary["reason"], "strict mode not ready")

    @patch("src.core.derived_assignment_flush.strict_mode_active", return_value=True)
    @patch("src.core.derived_assignment_flush.commit_mutation")
    def test_synthetic_rows_cannot_starve_backed_rows(self, commit, _strict) -> None:
        # Never-flushable synthetic rows (catalog stubs, addon backfills) sorted
        # ahead of a POS-backed row must not exhaust the candidate window.
        commit.return_value = CommitResult(status="ok")
        stub_items = [(f"item_stub_{i}", f"Stub {i}", "Ice Cream", 0) for i in range(5)]
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) VALUES (?, ?, ?, ?)",
            stub_items,
        )
        self.conn.executemany(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            )
            VALUES (?, ?, 'variant_a', 0, NULL, 0)
            """,
            [
                (catalog_stub_order_item_id(mid), mid)
                for mid, _, _, _ in stub_items
            ],
        )
        self.conn.execute(
            """
            INSERT INTO menu_item_variants (
                order_item_id, menu_item_id, variant_id, is_verified,
                assignment_seq, pending_local
            )
            VALUES (?, 'item_b', 'variant_a', 0, NULL, 0)
            """,
            (addon_seeded_mapping_order_item_id("item_b", "variant_a"),),
        )
        # NULL updated_at sorts first: all synthetics precede the backed row.
        self.conn.execute(
            "UPDATE menu_item_variants SET updated_at = '2026-01-01' WHERE order_item_id = '101'"
        )
        self.conn.commit()

        # batch_size=1 gives a window of 4; six synthetics sit ahead of '101'.
        summary = flush_pending_derived_assignments(self.conn, max_batches=1, batch_size=1)

        self.assertEqual(summary["sent"], 1)
        plan = commit.call_args.args[1]
        assignments = plan.event["merge_payload"]["assignments"]
        self.assertEqual([a["order_item_id"] for a in assignments], ["101"])

    @patch("src.core.derived_assignment_flush.strict_mode_active", return_value=True)
    @patch("src.core.derived_assignment_flush.commit_mutation")
    def test_skipped_existing_not_counted_as_accepted(self, commit, _strict) -> None:
        commit.return_value = CommitResult(status="ok", skipped_existing=["101"])

        summary = flush_pending_derived_assignments(self.conn, max_batches=1)

        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["accepted"], 0)
        self.assertEqual(summary["skipped_existing"], 1)


class DerivedAssignmentFlushOrchestrationTests(unittest.TestCase):
    """Which cloud-pull runs are allowed to flush local POS-backed assignments."""

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT)")
        bind_test_profile(conn)
        conn.commit()
        self.addCleanup(conn.close)
        return conn

    def _schema_conn(self):
        """A real analytics schema, so the flush itself can run unmocked."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_analytics_schema(conn)
        bind_test_profile(conn)
        conn.commit()
        self.addCleanup(conn.close)
        return conn

    def _run_pull(self, conn, *, global_active, assignment_pull, flush_result=_REAL_FLUSH):
        """Drive one cloud pull with every stream but the menu ones silenced.

        `flush_result` left at `_REAL_FLUSH` runs the true
        `flush_pending_derived_assignments`, so the commit path itself is under
        test rather than a stand-in for it.
        """
        capability = Mock(active=global_active)
        with ExitStack() as stack:
            for target, kwargs in (
                (
                    "src.core.config.cloud_sync_config.get_cloud_sync_config",
                    {"return_value": ("https://cloud.example", "k")},
                ),
                (
                    "src.core.global_menu_schema.resolve_global_menu_capability",
                    {"return_value": capability},
                ),
                (
                    "src.core.global_menu_sync.pull_global_menu_state",
                    {"return_value": {"status": "applied"}},
                ),
                (
                    "src.core.global_menu_sync.pull_global_assignment_snapshot",
                    {"new": assignment_pull},
                ),
                (
                    "src.core.global_menu_history.pull_global_menu_history",
                    {"return_value": {"status": "applied"}},
                ),
                (
                    "src.core.menu_bootstrap_sync.get_menu_bootstrap_pull_endpoint",
                    {"return_value": None},
                ),
                (
                    "src.core.menu_assignment_bootstrap.get_menu_assignments_snapshot_endpoint",
                    {"return_value": None},
                ),
                (
                    "src.core.menu_mapping_verification_sync."
                    "get_menu_mapping_verification_pull_endpoint",
                    {"return_value": None},
                ),
                (
                    "src.core.menu_merge_sync.get_menu_merge_pull_endpoint",
                    {"return_value": None},
                ),
                (
                    "src.core.customer_merge_sync.get_customer_merge_pull_endpoint",
                    {"return_value": None},
                ),
                (
                    "src.core.forecast_sync.get_forecast_delta_endpoint",
                    {"return_value": None},
                ),
            ):
                stack.enter_context(patch(target, **kwargs))
            flush = None
            if flush_result is not _REAL_FLUSH:
                flush = stack.enter_context(patch(
                    "src.core.derived_assignment_flush.flush_pending_derived_assignments",
                    return_value=flush_result,
                ))
            summary = run_best_effort_cloud_pulls(conn, blocking=False)
        return summary, flush

    def test_global_menu_install_still_flushes_its_pending_assignments(self) -> None:
        # A global-menu install skips the legacy merge/verification streams, so
        # gating the flush on those keys stopped it from ever running: the
        # server never learned the assignment, so a locator map had no row to
        # repoint and assignment verification failed on incomplete identity.
        assignment_pull = Mock(return_value={"status": "applied"})
        summary, flush = self._run_pull(
            self._conn(),
            global_active=True,
            flush_result={"attempted": True, "accepted": 1},
            assignment_pull=assignment_pull,
        )

        flush.assert_called_once()
        self.assertEqual(summary["derived_assignment_flush"], {"attempted": True, "accepted": 1})
        # Accepted rows are linked to global identity as they land, so the
        # snapshot is drained again to finish inside this one sync.
        self.assertEqual(assignment_pull.call_count, 2)

    def test_flush_that_accepts_nothing_does_not_repull_the_snapshot(self) -> None:
        assignment_pull = Mock(return_value={"status": "applied"})
        _summary, flush = self._run_pull(
            self._conn(),
            global_active=True,
            flush_result={"attempted": True, "accepted": 0},
            assignment_pull=assignment_pull,
        )

        flush.assert_called_once()
        self.assertEqual(assignment_pull.call_count, 1)

    def test_failed_global_pull_still_blocks_the_flush(self) -> None:
        assignment_pull = Mock(return_value={"status": "error", "error": "boom"})
        _summary, flush = self._run_pull(
            self._conn(),
            global_active=True,
            flush_result={"attempted": True, "accepted": 1},
            assignment_pull=assignment_pull,
        )

        flush.assert_not_called()

    def test_legacy_install_without_menu_pull_endpoints_does_not_flush(self) -> None:
        assignment_pull = Mock(return_value={"status": "applied"})
        _summary, flush = self._run_pull(
            self._conn(),
            global_active=False,
            flush_result={"attempted": True, "accepted": 1},
            assignment_pull=assignment_pull,
        )

        flush.assert_not_called()

    def test_a_failed_post_flush_repull_is_reported_as_an_assignment_pull_error(self) -> None:
        # Nothing reads `derived_assignment_flush`, so a re-pull failure recorded
        # there would leave `global_menu_assignments` holding the first pull's
        # success and Sync DB claiming a clean run while the snapshot never
        # drained — the exact silence the re-pull exists to prevent.
        assignment_pull = Mock(side_effect=[
            {"status": "applied"},
            RuntimeError("connection reset"),
        ])
        summary, _flush = self._run_pull(
            self._conn(),
            global_active=True,
            flush_result={"attempted": True, "accepted": 2},
            assignment_pull=assignment_pull,
        )

        self.assertEqual(assignment_pull.call_count, 2)
        self.assertEqual(
            summary["global_menu_assignments"],
            {"status": "error", "error": "connection reset"},
        )
        # The flush itself succeeded; its counts must survive the re-pull failure.
        self.assertEqual(
            summary["derived_assignment_flush"], {"attempted": True, "accepted": 2}
        )
        self.assertTrue(summary["menu_pull_failed"])
        self.assertIn(
            ("global_menu_assignments", "connection reset"),
            [(entry["stream"], entry["error"]) for entry in summary["menu_pull_errors"]],
        )

    def test_global_install_commits_a_real_derived_assignment_sync(self) -> None:
        # The gate tests above stand in for the flush, so none of them prove the
        # commit path this change unblocks actually runs under global mode.
        # Drive the true flush and assert on the plan it hands the transport.
        conn = self._schema_conn()
        conn.execute(
            "INSERT INTO menu_items (menu_item_id, name, type, is_verified) "
            "VALUES ('item_a', 'Orange Ice Cream', 'Ice Cream', 0)"
        )
        conn.execute(
            "INSERT INTO variants (variant_id, variant_name, is_verified) "
            "VALUES ('variant_a', 'SCOOP', 0)"
        )
        conn.execute(
            """
            INSERT INTO orders (
                order_id, petpooja_order_id, stream_id, event_id, occurred_at,
                created_on, order_type, order_from, order_status, core_total,
                tax_total, discount_total, delivery_charges, packaging_charge,
                service_charge, total
            ) VALUES (
                1, 1, 1, 'event-1', '2026-08-14T00:00:00', '2026-08-14T00:00:00',
                'Pickup', 'POS', 'Success', 100, 0, 0, 0, 0, 0, 100
            )
            """
        )
        conn.execute(
            """
            INSERT INTO order_items (
                order_item_id, order_id, menu_item_id, variant_id,
                petpooja_itemid, name_raw, quantity, unit_price, total_price
            ) VALUES (
                1, 1, 'item_a', 'variant_a', 101, 'Orange Ice Cream Scoop',
                1, 100, 100
            )
            """
        )
        conn.execute(
            "INSERT INTO menu_item_variants "
            "(order_item_id, menu_item_id, variant_id, is_verified) "
            "VALUES ('101', 'item_a', 'variant_a', 1)"
        )
        conn.commit()

        def ack(_conn, plan):
            # What the real applier does on accept: stamping assignment_seq is
            # what retires the row as a flush candidate. Without it the batch
            # loop re-sends the same row until max_batches, which is the shape a
            # commit that silently fails to apply would have.
            conn.executemany(
                "UPDATE menu_item_variants SET assignment_seq = 1 "
                "WHERE order_item_id = ?",
                [(order_item_id,) for order_item_id in plan.order_item_ids],
            )
            return CommitResult(status="ok")

        assignment_pull = Mock(return_value={"status": "applied"})
        with patch(
            "src.core.derived_assignment_flush.strict_mode_active", return_value=True
        ), patch(
            "src.core.derived_assignment_flush.commit_mutation", side_effect=ack
        ) as commit:
            summary, flush = self._run_pull(
                conn, global_active=True, assignment_pull=assignment_pull
            )

        self.assertIsNone(flush, "the real flush must run, not a stand-in")
        commit.assert_called_once()
        plan = commit.call_args.args[1]
        self.assertEqual(plan.mutation_type, MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC)
        self.assertEqual(
            [row["order_item_id"] for row in plan.event["merge_payload"]["assignments"]],
            ["101"],
        )
        self.assertEqual(summary["derived_assignment_flush"]["accepted"], 1)
        # One accepted row, so the snapshot drains a second time in this sync.
        self.assertEqual(assignment_pull.call_count, 2)


if __name__ == "__main__":
    unittest.main()
