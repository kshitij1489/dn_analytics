import hashlib
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.customer_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    EVENT_TYPE_UNDONE,
    SCHEMA_VERSION,
    build_merge_applied_event_payload,
    build_merge_undone_event_payload,
)


def _insert_customer_merge(
    conn,
    *,
    source_id: int = 1,
    target_id: int = 2,
    similarity_score: float = 0.98,
    model_name: str = "duplicate_matcher_v1",
    reasons=None,
    moved_order_ids=None,
    target_is_verified_after_merge: bool = False,
    remote_event_id=None,
) -> int:
    """Insert a customer_merge_history row the way merge_customers used to, without
    the strict-mode commit gate. Strict commits self-apply via apply_accepted (see
    src/core/customer_mutation_commit.py) and never write customer_merge_sync_events
    (legacy pre-cutover rows only) — these tests exercise the build_* payload
    builders that strict capture uses directly."""
    context = {
        "reasons": reasons or [],
        "target_before_fields": {},
        "inserted_target_address_ids": [],
        "target_is_verified_after_merge": target_is_verified_after_merge,
    }
    if remote_event_id:
        context["remote_event_id"] = remote_event_id
    suggestion_context = json.dumps(context)
    merge_id = conn.execute(
        """
        INSERT INTO customer_merge_history (
            source_customer_id, target_customer_id, similarity_score, model_name,
            suggestion_context, source_snapshot, target_snapshot, moved_order_ids, copied_address_count
        )
        VALUES (?, ?, ?, ?, ?, '{}', '{}', ?, 0)
        RETURNING merge_id
        """,
        (
            source_id,
            target_id,
            similarity_score,
            model_name,
            suggestion_context,
            json.dumps(moved_order_ids if moved_order_ids is not None else [101]),
        ),
    ).fetchone()[0]
    conn.commit()
    return int(merge_id)


def _mark_customer_merge_undone(conn, merge_id: int, *, restored_order_count: int = 1) -> None:
    conn.execute(
        """
        UPDATE customer_merge_history
        SET undone_at = CURRENT_TIMESTAMP, undo_context = ?
        WHERE merge_id = ?
        """,
        (
            json.dumps({
                "restored_order_count": restored_order_count,
                "removed_target_address_ids": [],
                "restored_target_fields": [],
            }),
            merge_id,
        ),
    )
    conn.commit()


class CustomerMergeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                customer_identity_key TEXT,
                name TEXT,
                name_normalized TEXT,
                phone TEXT,
                address TEXT,
                gstin TEXT,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                first_order_date TEXT,
                last_order_date TEXT,
                is_verified BOOLEAN NOT NULL DEFAULT 0,
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
                customer_id INTEGER NOT NULL,
                petpooja_order_id TEXT,
                stream_id INTEGER,
                event_id TEXT,
                aggregate_id TEXT,
                total REAL NOT NULL DEFAULT 0,
                created_on TEXT,
                updated_at TEXT
            );

            CREATE TABLE menu_items (
                menu_item_id TEXT PRIMARY KEY,
                name TEXT
            );

            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                menu_item_id TEXT,
                name_raw TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1
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
        self.conn.executemany(
            """
            INSERT INTO customers (
                customer_id,
                customer_identity_key,
                name,
                name_normalized,
                phone,
                address,
                gstin,
                total_orders,
                total_spent,
                first_order_date,
                last_order_date,
                is_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "phone:source",
                    "Rahul Sharma",
                    "rahul sharma",
                    "9999999999",
                    "HSR Layout",
                    None,
                    1,
                    80.0,
                    "2024-02-03 10:00:00",
                    "2024-02-03 10:00:00",
                    0,
                ),
                (
                    2,
                    "addr:target",
                    "Rahul S.",
                    "rahul s.",
                    None,
                    "HSR Layout",
                    None,
                    1,
                    120.0,
                    "2024-02-04 10:00:00",
                    "2024-02-04 10:00:00",
                    0,
                ),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO customer_addresses (
                customer_id,
                label,
                address_line_1,
                city,
                state,
                postal_code,
                country,
                is_default
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Primary", "HSR Layout", "Bengaluru", "KA", "560102", "IN", 1),
                (2, "Primary", "HSR Layout", "Bengaluru", "KA", "560102", "IN", 1),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO orders (
                order_id,
                customer_id,
                petpooja_order_id,
                stream_id,
                event_id,
                aggregate_id,
                total,
                created_on
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (101, 1, "PP-101", 5001, "evt-101", "agg-101", 80.0, "2024-02-03 10:00:00"),
                (102, 2, "PP-102", 5002, "evt-102", "agg-102", 120.0, "2024-02-04 10:00:00"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO menu_items (menu_item_id, name) VALUES (?, ?)",
            [
                ("m_burger", "Burger"),
                ("m_fries", "Fries"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO order_items (order_id, menu_item_id, name_raw, quantity)
            VALUES (?, ?, ?, ?)
            """,
            [
                (101, "m_burger", "Burger", 1),
                (102, "m_fries", "Fries", 2),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _insert_legacy_applied_event_row(self, merge_id: int, event_id: str) -> None:
        """Simulate a pre-strict-cutover outbox row (only the legacy shipper wrote these)."""
        self.conn.execute(
            """
            INSERT INTO customer_merge_sync_events (event_id, merge_id, event_type, payload, occurred_at)
            VALUES (?, ?, ?, '{}', CURRENT_TIMESTAMP)
            """,
            (event_id, merge_id, EVENT_TYPE_APPLIED),
        )
        self.conn.commit()

    def test_build_merge_applied_payload_carries_metadata_and_locators(self) -> None:
        merge_id = _insert_customer_merge(
            self.conn,
            similarity_score=0.98,
            reasons=["phone exact match"],
            target_is_verified_after_merge=True,
        )
        payload = build_merge_applied_event_payload(self.conn, merge_id)
        self.assertIsNotNone(payload)

        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["event_type"], EVENT_TYPE_APPLIED)
        self.assertTrue(payload["remote_event_id"])
        self.assertTrue(payload["merge_metadata"]["mark_target_verified"])
        self.assertEqual(payload["merge_metadata"]["reasons"], ["phone exact match"])
        self.assertEqual(payload["moved_orders"]["count"], 1)
        self.assertEqual(payload["moved_orders"]["portable_refs"][0]["petpooja_order_id"], "PP-101")
        self.assertTrue(payload["attribution"]["device"]["device_id"].startswith("device-"))
        self.assertTrue(payload["attribution"]["device"]["install_id"].startswith("install-"))
        expected_phone_hash = hashlib.sha256("9999999999".encode("utf-8")).hexdigest()
        self.assertEqual(
            payload["source_customer"]["portable_locators"]["phone_hash"],
            expected_phone_hash,
        )
        # Builders never write the legacy outbox.
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_merge_sync_events").fetchone()[0],
            0,
        )

    def test_build_merge_applied_payload_carries_pre_merge_order_refs(self) -> None:
        # Capture runs after orders moved to the target: simulate that state,
        # then assert descriptors reconstruct pre-merge ownership (source =
        # moved orders, target = its own orders minus moved).
        self.conn.execute("UPDATE orders SET customer_id = 2 WHERE order_id = 101")
        self.conn.commit()
        merge_id = _insert_customer_merge(self.conn, moved_order_ids=[101])

        payload = build_merge_applied_event_payload(self.conn, merge_id)
        self.assertIsNotNone(payload)

        source_refs = payload["source_customer"]["portable_locators"]["order_refs"]
        target_refs = payload["target_customer"]["portable_locators"]["order_refs"]
        self.assertEqual([ref["petpooja_order_id"] for ref in source_refs], ["PP-101"])
        self.assertEqual([ref["petpooja_order_id"] for ref in target_refs], ["PP-102"])

    def test_build_merge_undone_payload_links_via_legacy_outbox_row(self) -> None:
        merge_id = _insert_customer_merge(self.conn, similarity_score=0.9, reasons=["same address"])
        self._insert_legacy_applied_event_row(merge_id, "legacy-applied-1")
        _mark_customer_merge_undone(self.conn, merge_id)

        payload = build_merge_undone_event_payload(self.conn, merge_id)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["event_type"], EVENT_TYPE_UNDONE)
        self.assertEqual(payload["reverts_remote_event_id"], "legacy-applied-1")
        self.assertEqual(payload["moved_orders"]["count"], 1)

    def test_build_merge_undone_payload_links_via_suggestion_context_remote_event_id(self) -> None:
        merge_id = _insert_customer_merge(
            self.conn,
            similarity_score=0.9,
            reasons=["same address"],
            remote_event_id="remote-applied-7",
        )
        _mark_customer_merge_undone(self.conn, merge_id)

        payload = build_merge_undone_event_payload(self.conn, merge_id)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["reverts_remote_event_id"], "remote-applied-7")

    def test_build_merge_undone_payload_returns_none_without_reverts_target(self) -> None:
        merge_id = _insert_customer_merge(self.conn, similarity_score=0.9, reasons=["same address"])
        _mark_customer_merge_undone(self.conn, merge_id)

        payload = build_merge_undone_event_payload(self.conn, merge_id)
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
