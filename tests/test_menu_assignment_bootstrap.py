"""
Phase C4: fresh-install fast path (snapshot → cursor at watermark → tail) and
bootstrap demotion (seed-only default, snapshot_role + hash-skip on the shipper).
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from src.core.menu_assignment_bootstrap import (
    MENU_ASSIGNMENTS_BOOTSTRAPPED_KEY,
    bootstrap_menu_assignments_if_needed,
)
from src.core.menu_bootstrap_sync import (
    DEFAULT_MENU_BOOTSTRAP_APPLY_MODE,
    get_menu_bootstrap_apply_mode,
)
from src.core.menu_merge_sync import get_menu_merge_pull_cursor, set_menu_merge_pull_cursor
from tests.test_menu_assignment_apply import (
    FakeEventServer,
    make_install_db,
    pull_install,
)
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


def _fake_snapshot_fetch(rows, watermark_seq, watermark_cursor, page_size=2):
    ordered = sorted(rows, key=lambda row: row["order_item_id"])

    def _fetch(endpoint, auth, after, limit):
        remaining = [row for row in ordered if not after or row["order_item_id"] > after]
        page = remaining[:page_size]
        return {
            "error": None,
            "assignments": page,
            "watermark_seq": watermark_seq,
            "watermark_cursor": watermark_cursor,
            "next_page": page[-1]["order_item_id"] if len(page) == page_size else None,
        }

    return _fetch


class MenuAssignmentBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        for target in (
            patch("utils.menu_utils.export_to_backups", return_value=True),
            patch("utils.menu_utils._clear_impacted_models", return_value=None),
        ):
            target.start()
            self.addCleanup(target.stop)

    def test_fresh_install_snapshot_digest_equals_replay_digest(self) -> None:
        replayed = make_install_db()
        self.addCleanup(replayed.close)
        server = FakeEventServer()

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
        server.ingest_outbox(replayed)
        pull_install(replayed, server)

        snapshot_rows = _snapshot_rows_from_install(replayed)
        watermark_seq = len(server.rows)
        watermark_cursor = str(len(server.rows))

        fresh = make_install_db()
        self.addCleanup(fresh.close)
        with patch(
            "src.core.menu_assignment_bootstrap._fetch_snapshot_page",
            side_effect=_fake_snapshot_fetch(snapshot_rows, watermark_seq, watermark_cursor),
        ):
            outcome = bootstrap_menu_assignments_if_needed(fresh, "http://fake/snapshot")

        self.assertEqual(outcome["status"], "bootstrapped")
        self.assertTrue(outcome["cursor_set"])
        self.assertEqual(get_menu_merge_pull_cursor(fresh), watermark_cursor)

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


class MenuBootstrapShipperTests(unittest.TestCase):
    def _write_backups(self, data_dir: Path, id_maps: dict) -> None:
        (data_dir / "id_maps_backup.json").write_text(json.dumps(id_maps))
        (data_dir / "cluster_state_backup.json").write_text(json.dumps({"a:1": {}}))

    def test_sends_seed_only_role_and_skips_unchanged_id_maps(self) -> None:
        from src.core import menu_bootstrap_shipper

        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._write_backups(data_dir, {"menu_id_to_str": {"1": "a"}})

            response = MagicMock(status_code=200)
            with patch(
                "src.core.menu_bootstrap_shipper.get_resource_path", return_value=str(data_dir)
            ), patch("requests.post", return_value=response) as post:
                first = menu_bootstrap_shipper.upload_pending(endpoint="http://fake/ingest")
                self.assertTrue(first["sent"])
                payload = post.call_args.kwargs["json"]
                self.assertEqual(payload["snapshot_role"], "seed_only")

                # Same id_maps → skipped without an HTTP call.
                second = menu_bootstrap_shipper.upload_pending(endpoint="http://fake/ingest")
                self.assertFalse(second["sent"])
                self.assertEqual(second.get("skipped"), "id_maps unchanged")
                self.assertEqual(post.call_count, 1)

                # force=True pushes anyway.
                forced = menu_bootstrap_shipper.upload_pending(
                    endpoint="http://fake/ingest", force=True
                )
                self.assertTrue(forced["sent"])
                self.assertEqual(post.call_count, 2)

                # Catalog change → pushed again.
                self._write_backups(data_dir, {"menu_id_to_str": {"1": "a", "2": "b"}})
                third = menu_bootstrap_shipper.upload_pending(endpoint="http://fake/ingest")
                self.assertTrue(third["sent"])
                self.assertEqual(post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
