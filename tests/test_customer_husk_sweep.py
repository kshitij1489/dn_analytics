"""Customer husk lifecycle tests (MENU_SYNC_ARCHITECTURE.md §2.4.1).

Covers both layers of the customer husk fix:
  1. Source: order replay reuses the order's current owner for anon: identity
     keys instead of minting a duplicate customer per replay.
  2. Sweep: state-scoped GC deletes zero-reference customers past the grace
     window while preserving merge lineage and fresh rows.
"""

import sqlite3
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from services.load_orders import (
    _ensure_customer_no_stats,
    compute_customer_identity_key,
    sweep_orphan_customers,
)
from services.load_orders import main as load_orders_main
from src.core.customer_merge_sync import pull_and_apply_customer_merge_events
from src.core.services.sync_service import sync_database
from tests.profile_test_helpers import bind_test_profile


def _make_conn(with_created_at: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    created_at_col = "created_at TEXT DEFAULT CURRENT_TIMESTAMP," if with_created_at else ""
    conn.executescript(
        f"""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_identity_key TEXT UNIQUE,
            name TEXT,
            name_normalized TEXT,
            phone TEXT,
            address TEXT,
            gstin TEXT,
            first_order_date TEXT,
            last_order_date TEXT,
            total_orders INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0,
            is_verified BOOLEAN DEFAULT 0,
            {created_at_col}
            updated_at TEXT
        );

        CREATE TABLE customer_addresses (
            address_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            label TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            country TEXT,
            is_default BOOLEAN DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            petpooja_order_id TEXT,
            stream_id INTEGER,
            event_id TEXT,
            aggregate_id TEXT,
            total REAL NOT NULL DEFAULT 0,
            created_on TEXT,
            updated_at TEXT
        );

        CREATE TABLE customer_merge_history (
            merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_customer_id INTEGER NOT NULL,
            target_customer_id INTEGER NOT NULL,
            similarity_score REAL,
            model_name TEXT,
            suggestion_context TEXT,
            source_snapshot TEXT,
            target_snapshot TEXT,
            moved_order_ids TEXT,
            copied_address_count INTEGER DEFAULT 0,
            merged_at TEXT DEFAULT CURRENT_TIMESTAMP,
            undone_at TEXT,
            undo_context TEXT
        );

        CREATE TABLE customer_merge_sync_events (
            event_id TEXT PRIMARY KEY,
            merge_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            upload_attempted_at TEXT,
            uploaded_at TEXT,
            last_error TEXT,
            UNIQUE (merge_id, event_type)
        );
        """
    )
    bind_test_profile(conn)
    return conn


def _insert_customer(
    conn,
    identity_key: str,
    name: str = "Anonymous",
    created_at: str = "2020-01-01 00:00:00",
) -> int:
    row = conn.execute(
        """
        INSERT INTO customers (
            customer_identity_key, name, name_normalized, total_orders, total_spent, created_at
        )
        VALUES (?, ?, ?, 0, 0, ?)
        RETURNING customer_id
        """,
        (identity_key, name, name.lower(), created_at),
    ).fetchone()
    return int(row[0])


class SweepOrphanCustomersTests(unittest.TestCase):
    def test_sweep_deletes_zero_ref_customer_past_grace(self) -> None:
        conn = _make_conn()
        husk_id = _insert_customer(conn, "anon:husk-1")
        conn.execute(
            "INSERT INTO customer_addresses (customer_id, label, address_line_1) VALUES (?, 'Primary', 'Old Lane')",
            (husk_id,),
        )
        live_id = _insert_customer(conn, "phone:live")
        conn.execute(
            "INSERT INTO orders (order_id, customer_id, petpooja_order_id) VALUES (1, ?, 'PP-1')",
            (live_id,),
        )
        conn.commit()

        swept = sweep_orphan_customers(conn.cursor())

        self.assertEqual(swept, [husk_id])
        remaining = [r[0] for r in conn.execute("SELECT customer_id FROM customers").fetchall()]
        self.assertEqual(remaining, [live_id])
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM customer_addresses WHERE customer_id = ?", (husk_id,)
            ).fetchone()[0],
            0,
        )

    def test_fresh_customer_survives_grace(self) -> None:
        conn = _make_conn()
        fresh_id = _insert_customer(
            conn, "anon:fresh", created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        conn.commit()

        swept = sweep_orphan_customers(conn.cursor())

        self.assertEqual(swept, [])
        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM customers WHERE customer_id = ?", (fresh_id,)
            ).fetchone()
        )

    def test_merge_referenced_zero_order_customer_survives(self) -> None:
        conn = _make_conn()
        source_id = _insert_customer(conn, "phone:merged-source", name="Rahul Sharma")
        target_id = _insert_customer(conn, "phone:merged-target", name="Rahul S.")
        # Undone merges count too: the history row is lineage either way.
        conn.execute(
            """
            INSERT INTO customer_merge_history (source_customer_id, target_customer_id, undone_at)
            VALUES (?, ?, '2024-02-05 13:00:00')
            """,
            (source_id, target_id),
        )
        conn.commit()

        swept = sweep_orphan_customers(conn.cursor())

        self.assertEqual(swept, [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 2)

    def test_schema_without_created_at_skips_sweep(self) -> None:
        conn = _make_conn(with_created_at=False)
        conn.execute(
            """
            INSERT INTO customers (customer_identity_key, name, name_normalized, total_orders, total_spent)
            VALUES ('anon:no-col', 'Anonymous', 'anonymous', 0, 0)
            """
        )
        conn.commit()

        swept = sweep_orphan_customers(conn.cursor())

        self.assertEqual(swept, [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)


class ReplayAnonReuseTests(unittest.TestCase):
    def test_replay_reuses_previous_anon_owner(self) -> None:
        conn = _make_conn()
        owner_id = _insert_customer(conn, "anon:original-owner")
        conn.commit()

        resolved = _ensure_customer_no_stats(
            conn,
            {"name": "Anonymous"},
            datetime(2024, 2, 3, 10, 0, 0),
            fallback_customer_id=owner_id,
        )

        self.assertEqual(resolved, owner_id)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)

    def test_replay_anon_follows_merge_redirect(self) -> None:
        conn = _make_conn()
        old_owner = _insert_customer(conn, "anon:old-owner")
        surviving = _insert_customer(conn, "phone:surviving", name="Rahul Sharma")
        conn.execute(
            "INSERT INTO customer_merge_history (source_customer_id, target_customer_id) VALUES (?, ?)",
            (old_owner, surviving),
        )
        conn.commit()

        resolved = _ensure_customer_no_stats(
            conn,
            {"name": "Anonymous"},
            datetime(2024, 2, 3, 10, 0, 0),
            fallback_customer_id=old_owner,
        )

        self.assertEqual(resolved, surviving)

    def test_replay_without_fallback_still_creates_anon_customer(self) -> None:
        conn = _make_conn()

        created = _ensure_customer_no_stats(
            conn,
            {"name": "Anonymous"},
            datetime(2024, 2, 3, 10, 0, 0),
        )

        self.assertIsNotNone(created)
        row = conn.execute(
            "SELECT customer_identity_key FROM customers WHERE customer_id = ?", (created,)
        ).fetchone()
        self.assertTrue(str(row[0]).startswith("anon:"))

    def test_replay_deterministic_key_still_resolves_normally(self) -> None:
        conn = _make_conn()
        anon_owner = _insert_customer(conn, "anon:owner")
        phone_key = compute_customer_identity_key({"name": "Rahul Sharma", "phone": "9999999999"})
        phone_row = conn.execute(
            """
            INSERT INTO customers (customer_identity_key, name, name_normalized, phone, total_orders, total_spent)
            VALUES (?, 'Rahul Sharma', 'rahul sharma', '9999999999', 1, 80.0)
            RETURNING customer_id
            """,
            (phone_key,),
        ).fetchone()
        conn.commit()

        # Order gained a phone number on replay: must re-key to the phone
        # customer, NOT stick to the anon fallback.
        resolved = _ensure_customer_no_stats(
            conn,
            {"name": "Rahul Sharma", "phone": "9999999999"},
            datetime(2024, 2, 3, 10, 0, 0),
            fallback_customer_id=anon_owner,
        )

        self.assertEqual(resolved, int(phone_row[0]))


class PullEndSweepTests(unittest.TestCase):
    @patch("requests.get")
    def test_eventless_pull_sweeps_customer_husk(self, mock_get: Mock) -> None:
        conn = _make_conn()
        husk_id = _insert_customer(conn, "anon:pull-husk")
        live_id = _insert_customer(conn, "phone:pull-live")
        conn.execute(
            "INSERT INTO orders (order_id, customer_id, petpooja_order_id) VALUES (1, ?, 'PP-1')",
            (live_id,),
        )
        conn.commit()

        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"events": [], "next_cursor": None}),
        )

        result = pull_and_apply_customer_merge_events(
            conn,
            endpoint="https://cloud.example.com/desktop-analytics-sync/customer-merges",
            auth="secret-token",
        )

        self.assertIsNone(result["error"])
        self.assertEqual(result["customer_husks_swept"], 1)
        self.assertIsNone(
            conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (husk_id,)).fetchone()
        )
        self.assertIsNotNone(
            conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (live_id,)).fetchone()
        )


class SyncDatabaseSweepTests(unittest.TestCase):
    @patch("src.core.services.sync_service.fetch_stream_raw", return_value=([], 0))
    def test_orderless_sync_sweeps_customer_husk(self, mock_fetch: Mock) -> None:
        conn = _make_conn()
        husk_id = _insert_customer(conn, "anon:sync-husk")
        live_id = _insert_customer(conn, "phone:sync-live")
        conn.execute(
            "INSERT INTO orders (order_id, customer_id, petpooja_order_id, stream_id) VALUES (1, ?, 'PP-1', 5)",
            (live_id,),
        )
        conn.commit()

        statuses = list(sync_database(conn))

        self.assertEqual(statuses[-1].type, "done")
        self.assertNotIn("error", [s.type for s in statuses])
        self.assertIsNone(
            conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (husk_id,)).fetchone()
        )
        self.assertIsNotNone(
            conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (live_id,)).fetchone()
        )


class _CloseShieldedConn:
    """Delegate to a real sqlite3 connection but keep it open across close()
    so tests can assert on state after main() finishes."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


class CliSweepTests(unittest.TestCase):
    _ORDER_STATS = {
        "orders": 1,
        "order_items": 0,
        "order_item_addons": 0,
        "order_taxes": 0,
        "order_discounts": 0,
        "errors": [],
    }

    def _run_cli(self, conn, orders):
        proxy = _CloseShieldedConn(conn)
        with patch("services.load_orders.get_db_connection", return_value=(proxy, "connected")), \
             patch("services.load_orders.create_schema_if_needed"), \
             patch("services.load_orders.OrderItemCluster"), \
             patch("services.load_orders.fetch_stream_raw", return_value=(orders, len(orders))), \
             patch("services.load_orders.process_order", return_value=dict(self._ORDER_STATS)), \
             patch("sys.argv", ["load_orders.py", "--incremental"]):
            load_orders_main()

    def test_cli_no_orders_exit_sweeps_husk(self) -> None:
        conn = _make_conn()
        husk_id = _insert_customer(conn, "anon:cli-husk")
        conn.commit()

        self._run_cli(conn, [])

        self.assertIsNone(
            conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (husk_id,)).fetchone()
        )

    def test_cli_processed_orders_exit_sweeps_husk(self) -> None:
        conn = _make_conn()
        husk_id = _insert_customer(conn, "anon:cli-husk-2")
        conn.commit()

        self._run_cli(conn, [{"raw_event": {}}])

        self.assertIsNone(
            conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (husk_id,)).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
