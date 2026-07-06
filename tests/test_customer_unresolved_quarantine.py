import hashlib
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.customer_merge_sync import (
    count_unresolved_customer_merge_events,
    list_unresolved_customer_merge_events,
    pull_and_apply_customer_merge_events,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CustomerUnresolvedQuarantineTests(unittest.TestCase):
    """Unresolvable remote events quarantine and retry instead of halting the pull."""

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
            INSERT INTO customers (customer_id, customer_identity_key, name, name_normalized, phone, address)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "phone:source", "Rahul Sharma", "rahul sharma", "9999999999", "HSR Layout"),
                (2, "addr:target", "Rahul S.", "rahul s.", None, "HSR Layout"),
                # Two same-name customers with no phone/address: a name-only locator
                # can never resolve uniquely between them.
                (3, None, "Nirupam Das", "nirupam das", None, None),
                (4, None, "Nirupam Das", "nirupam das", None, None),
            ],
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _locators(self, *, identity_key=None, phone=None, name=None, address=None) -> dict:
        name_norm = name.lower() if name else None
        address_norm = address.lower() if address else None
        return {
            "customer_identity_key": identity_key,
            "phone_hash": _sha(phone) if phone else None,
            "name_address_hash": _sha(f"{name_norm}|{address_norm}") if name_norm and address_norm else None,
            "name_normalized": name_norm,
            "address_normalized": address_norm,
            "address_book_hashes": [],
        }

    def _merge_event(self, remote_event_id: str, source_locators: dict, target_locators: dict, occurred_at: str) -> dict:
        return {
            "remote_event_id": remote_event_id,
            "schema_version": 1,
            "event_type": "customer_merge.applied",
            "occurred_at": occurred_at,
            "source_customer": {"snapshot": {}, "portable_locators": source_locators},
            "target_customer": {"snapshot": {}, "portable_locators": target_locators},
            "merge_metadata": {"similarity_score": 0.9, "model_name": "test", "reasons": []},
            "moved_orders": {"count": 0, "portable_refs": []},
            "local_refs": {},
        }

    def _undo_event(self, remote_event_id: str, reverts: str, applied_event: dict, occurred_at: str) -> dict:
        return {
            "remote_event_id": remote_event_id,
            "schema_version": 1,
            "event_type": "customer_merge.undone",
            "occurred_at": occurred_at,
            "reverts_remote_event_id": reverts,
            "source_customer": applied_event["source_customer"],
            "target_customer": applied_event["target_customer"],
            "merge_metadata": applied_event["merge_metadata"],
            "undo_metadata": {"restored_order_count": 0},
            "moved_orders": applied_event["moved_orders"],
            "local_refs": {},
        }

    def _ambiguous_event(self, remote_event_id: str = "remote-ambiguous-1") -> dict:
        name_only = self._locators(name="Nirupam Das")
        return self._merge_event(remote_event_id, name_only, dict(name_only), "2024-02-05 12:00:00")

    def _missing_source_event(self, remote_event_id: str = "remote-ghost-1") -> dict:
        source = self._locators(phone="8888888888", name="Ghost Person")
        target = self._locators(identity_key="addr:target", name="Rahul S.", address="HSR Layout")
        return self._merge_event(remote_event_id, source, target, "2024-02-05 12:00:00")

    def _resolvable_event(self, remote_event_id: str = "remote-ok-1") -> dict:
        source = self._locators(identity_key="phone:source", phone="9999999999", name="Rahul Sharma", address="HSR Layout")
        target = self._locators(identity_key="addr:target", name="Rahul S.", address="HSR Layout")
        return self._merge_event(remote_event_id, source, target, "2024-02-05 12:30:00")

    def _pull(self, events, *, next_cursor="cursor-1", scope_state=True) -> dict:
        body = {"events": events, "next_cursor": next_cursor}
        if scope_state:
            body["customer_revision"] = 58
            body["strict_mode_enabled"] = True
        with patch("requests.get", return_value=Mock(status_code=200, json=Mock(return_value=body))):
            return pull_and_apply_customer_merge_events(
                self.conn,
                endpoint="https://cloud.example.com/desktop-analytics-sync/customer-merges",
                auth="secret-token",
            )

    def _config_value(self, key: str):
        row = self.conn.execute("SELECT value FROM system_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def test_unresolvable_event_is_quarantined_and_stream_continues(self) -> None:
        result = self._pull([self._ambiguous_event(), self._resolvable_event()])

        self.assertIsNone(result["error"])
        self.assertEqual(result["events_unresolved"], 1)
        self.assertEqual(result["merge_events_applied"], 1)
        self.assertEqual(result["unresolved_pending"], 1)
        self.assertEqual(result["cursor_after"], "cursor-1")

        # Scope state mirrors despite the quarantined event — strict mode must not
        # stay blocked behind an unresolvable historical event.
        self.assertEqual(self._config_value("customer_state_revision"), "58")
        self.assertEqual(self._config_value("customer_strict_mode_enabled"), "1")

        quarantined = list_unresolved_customer_merge_events(self.conn)
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0]["remote_event_id"], "remote-ambiguous-1")
        self.assertEqual(quarantined[0]["attempts"], 1)
        self.assertIn("Could not resolve", quarantined[0]["last_error"])

    def test_quarantined_event_retries_and_applies_when_resolvable(self) -> None:
        first = self._pull([self._missing_source_event()])
        self.assertEqual(first["events_unresolved"], 1)
        self.assertEqual(count_unresolved_customer_merge_events(self.conn), 1)

        # The missing customer arrives (e.g. via order sync); the next pull heals.
        self.conn.execute(
            "INSERT INTO customers (customer_id, name, name_normalized, phone) VALUES (5, 'Ghost Person', 'ghost person', '8888888888')"
        )
        self.conn.commit()

        second = self._pull([], next_cursor="cursor-2")
        self.assertIsNone(second["error"])
        self.assertEqual(second["merge_events_applied"], 1)
        self.assertEqual(second["unresolved_pending"], 0)
        self.assertEqual(count_unresolved_customer_merge_events(self.conn), 0)

        remote_row = self.conn.execute(
            "SELECT local_merge_id FROM customer_merge_remote_events WHERE remote_event_id = 'remote-ghost-1'"
        ).fetchone()
        self.assertIsNotNone(remote_row)
        merge_row = self.conn.execute(
            "SELECT source_customer_id, target_customer_id FROM customer_merge_history"
        ).fetchone()
        self.assertEqual(int(merge_row["source_customer_id"]), 5)
        self.assertEqual(int(merge_row["target_customer_id"]), 2)

    def test_retry_attempts_increment_while_still_unresolvable(self) -> None:
        self._pull([self._ambiguous_event()])
        second = self._pull([], next_cursor="cursor-2")

        self.assertIsNone(second["error"])
        self.assertEqual(second["unresolved_pending"], 1)
        quarantined = list_unresolved_customer_merge_events(self.conn)
        self.assertEqual(quarantined[0]["attempts"], 2)

    def test_undo_referencing_quarantined_merge_heals_in_order(self) -> None:
        applied = self._missing_source_event("remote-ghost-2")
        undo = self._undo_event("remote-undo-2", "remote-ghost-2", applied, "2024-02-05 13:00:00")

        first = self._pull([applied, undo])
        self.assertIsNone(first["error"])
        self.assertEqual(first["events_unresolved"], 2)
        self.assertEqual(count_unresolved_customer_merge_events(self.conn), 2)

        self.conn.execute(
            "INSERT INTO customers (customer_id, name, name_normalized, phone) VALUES (5, 'Ghost Person', 'ghost person', '8888888888')"
        )
        self.conn.commit()

        second = self._pull([], next_cursor="cursor-2")
        self.assertIsNone(second["error"])
        self.assertEqual(second["merge_events_applied"], 1)
        self.assertEqual(second["undo_events_applied"], 1)
        self.assertEqual(count_unresolved_customer_merge_events(self.conn), 0)

        merge_row = self.conn.execute("SELECT undone_at FROM customer_merge_history").fetchone()
        self.assertIsNotNone(merge_row["undone_at"])

    def test_non_resolution_failures_still_fail_closed(self) -> None:
        bogus = self._resolvable_event()
        bogus["event_type"] = "customer_merge.bogus"

        result = self._pull([bogus])

        self.assertIsNotNone(result["error"])
        self.assertIn("Unsupported remote event_type", result["error"])
        self.assertEqual(count_unresolved_customer_merge_events(self.conn), 0)
        # Scope state must not apply on a failed pull.
        self.assertIsNone(self._config_value("customer_state_revision"))


class CustomerPullWarningSurfacingTests(unittest.TestCase):
    def test_collect_customer_pull_warnings_reports_unresolved_pending(self) -> None:
        from src.core.services.cloud_pull_orchestrator import collect_customer_pull_warnings

        summary = {"customer_merges": {"error": None, "unresolved_pending": 2}}
        warnings = collect_customer_pull_warnings(summary)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0][0], "customer_merges")
        self.assertIn("2 customer merge event(s)", warnings[0][1])

    def test_collect_customer_pull_warnings_empty_when_clean(self) -> None:
        from src.core.services.cloud_pull_orchestrator import collect_customer_pull_warnings

        self.assertEqual(collect_customer_pull_warnings({"customer_merges": {"error": None, "unresolved_pending": 0}}), [])
        self.assertEqual(collect_customer_pull_warnings({"customer_merges": None}), [])


if __name__ == "__main__":
    unittest.main()
