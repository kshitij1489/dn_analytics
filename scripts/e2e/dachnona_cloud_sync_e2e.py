#!/usr/bin/env python3
"""
End-to-end verifier for the Dachnona cloud sync contract.

This script validates the current revision-1.2 scoped pull surface and simulates
two local analytics clients exchanging a menu bootstrap snapshot. Strict menu
and customer mutations are covered by the dedicated mutation-commit suites;
the retired batched merge-ingest routes are intentionally not called here.

The script is intentionally isolated:
- it uses temp SQLite databases
- it does not touch the real analytics.db
- it patches backup-path helpers so menu bootstrap tests do not overwrite data/*.json

Run against your Dachnona stack (paths under /desktop-analytics-sync/...):

    python scripts/e2e/dachnona_cloud_sync_e2e.py --base-url http://127.0.0.1:<PORT> \
        --api-key <sync_api_key> --restaurant-id <restaurant_id>

With db.dachnona "docker compose -f docker-compose.dev.yml up", traffic usually goes through nginx to
Gunicorn. Use the host:port your compose file publishes for HTTP (often localhost and a mapped port), not a
different process bound to the same port. Gunicorn listens on 0.0.0.0:8000 inside the web container; nginx
proxies the public port.

If web logs show "Sync API auth failed: missing or invalid Authorization header", pass the same Bearer token
as the desktop app (system_config cloud_sync_api_key / dev sync secret): --api-key or env DACHNONA_E2E_API_KEY.

Defaults: env DACHNONA_E2E_BASE_URL (else http://127.0.0.1:8000),
DACHNONA_E2E_API_KEY, and DACHNONA_E2E_RESTAURANT_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.menu_bootstrap_shipper import upload_pending as upload_menu_bootstrap
from src.core.menu_bootstrap_sync import (
    fetch_and_apply_menu_bootstrap_snapshot,
    fetch_latest_menu_bootstrap_snapshot,
)


class E2EFailure(RuntimeError):
    pass


CUSTOMER_SCHEMA = """
CREATE TABLE system_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app_users (
    employee_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

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


MENU_SCHEMA = """
CREATE TABLE system_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app_users (
    employee_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    order_status TEXT NOT NULL
);

CREATE TABLE menu_items (
    menu_item_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    is_verified BOOLEAN DEFAULT 0,
    total_sold INTEGER DEFAULT 0,
    total_revenue REAL DEFAULT 0,
    sold_as_item INTEGER DEFAULT 0,
    sold_as_addon INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE variants (
    variant_id TEXT PRIMARY KEY,
    variant_name TEXT NOT NULL,
    unit TEXT,
    value REAL,
    is_verified BOOLEAN DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE menu_item_variants (
    order_item_id INTEGER PRIMARY KEY,
    menu_item_id TEXT NOT NULL,
    variant_id TEXT,
    is_verified BOOLEAN DEFAULT 1,
    updated_at TEXT
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    menu_item_id TEXT,
    variant_id TEXT,
    quantity INTEGER DEFAULT 1,
    total_price REAL DEFAULT 0,
    name_raw TEXT,
    updated_at TEXT
);

CREATE TABLE order_item_addons (
    order_item_addon_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_item_id INTEGER,
    menu_item_id TEXT,
    variant_id TEXT,
    quantity INTEGER DEFAULT 0,
    price REAL DEFAULT 0
);

CREATE TABLE merge_history (
    merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    affected_order_items TEXT NOT NULL,
    merged_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


BOOTSTRAP_TARGET_SCHEMA = """
CREATE TABLE system_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app_users (
    employee_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

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
    updated_at TEXT
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    menu_item_id TEXT,
    variant_id TEXT,
    updated_at TEXT
);
"""


def info(message: str) -> None:
    print(f"[INFO] {message}")


def ok(message: str) -> None:
    print(f"[PASS] {message}")


def fail(message: str) -> None:
    raise E2EFailure(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def make_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_identity(
    conn: sqlite3.Connection,
    employee_id: str,
    employee_name: str,
    device_id: str,
    install_id: str,
    restaurant_id: str,
) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS restaurant_profile_identity (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            restaurant_id TEXT NOT NULL UNIQUE,
            bound_at TEXT NOT NULL,
            profile_schema_version INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO restaurant_profile_identity
            (singleton_id, restaurant_id, bound_at, profile_schema_version)
        VALUES (1, ?, CURRENT_TIMESTAMP, 1)
        """,
        (restaurant_id,),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO app_users (employee_id, name, is_active)
        VALUES (?, ?, 1)
        """,
        (employee_id, employee_name),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO system_config (key, value, updated_at)
        VALUES ('sync_device_id', ?, CURRENT_TIMESTAMP)
        """,
        (device_id,),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO system_config (key, value, updated_at)
        VALUES ('sync_install_id', ?, CURRENT_TIMESTAMP)
        """,
        (install_id,),
    )
    conn.commit()


def seed_customer_fixture(conn: sqlite3.Connection, tag: str) -> Dict[str, Any]:
    phone = f"9{tag[:9]}".ljust(10, "0")[:10]
    source_identity = f"phone:source:{tag}"
    target_identity = f"addr:target:{tag}"
    source_name = f"Rahul Sharma {tag}"
    target_name = f"Rahul S. {tag}"
    address = f"HSR Layout Block {tag}"

    conn.executescript(CUSTOMER_SCHEMA)
    conn.executemany(
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
                source_identity,
                source_name,
                source_name.lower(),
                phone,
                address,
                None,
                1,
                80.0,
                "2024-02-03 10:00:00",
                "2024-02-03 10:00:00",
                0,
            ),
            (
                2,
                target_identity,
                target_name,
                target_name.lower(),
                None,
                address,
                None,
                1,
                120.0,
                "2024-02-04 10:00:00",
                "2024-02-04 10:00:00",
                0,
            ),
        ],
    )
    conn.executemany(
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
            (1, "Primary", address, "Bengaluru", "KA", "560102", "IN", 1),
            (2, "Primary", address, "Bengaluru", "KA", "560102", "IN", 1),
        ],
    )
    conn.executemany(
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
            (101, 1, f"PP-{tag}-101", 5001, f"evt-{tag}-101", f"agg-{tag}-101", 80.0, "2024-02-03 10:00:00"),
            (102, 2, f"PP-{tag}-102", 5002, f"evt-{tag}-102", f"agg-{tag}-102", 120.0, "2024-02-04 10:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO menu_items (menu_item_id, name) VALUES (?, ?)",
        [
            (f"m_burger_{tag}", "Burger"),
            (f"m_fries_{tag}", "Fries"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO order_items (order_id, menu_item_id, name_raw, quantity)
        VALUES (?, ?, ?, ?)
        """,
        [
            (101, f"m_burger_{tag}", "Burger", 1),
            (102, f"m_fries_{tag}", "Fries", 2),
        ],
    )
    conn.commit()
    return {
        "source_customer_id": "1",
        "target_customer_id": "2",
        "phone": phone,
        "address": address,
    }


def seed_menu_fixture(conn: sqlite3.Connection, tag: str) -> Dict[str, str]:
    source_item = f"item_source_{tag}"
    target_item = f"item_target_{tag}"
    source_name = f"Iced Coffee {tag}"
    target_name = f"Cold Coffee {tag}"
    conn.executescript(MENU_SCHEMA)
    conn.execute(
        """
        INSERT INTO orders (order_id, order_status)
        VALUES (1, 'Success')
        """
    )
    conn.executemany(
        """
        INSERT INTO menu_items (
            menu_item_id, name, type, is_verified, total_sold, total_revenue, sold_as_item, sold_as_addon
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (source_item, source_name, "Beverage", 1, 4, 480.0, 4, 0),
            (target_item, target_name, "Beverage", 1, 7, 910.0, 7, 0),
        ],
    )
    conn.execute(
        """
        INSERT INTO order_items (order_id, menu_item_id, quantity, total_price, name_raw)
        VALUES (1, ?, 2, 240.0, ?)
        """,
        (source_item, source_name),
    )
    conn.execute(
        """
        INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
        VALUES (1, ?, NULL, 1)
        """,
        (source_item,),
    )
    conn.commit()
    return {
        "source_item": source_item,
        "target_item": target_item,
    }


def seed_bootstrap_source_fixture(conn: sqlite3.Connection, tag: str) -> Dict[str, str]:
    conn.executescript(MENU_SCHEMA)
    item_id = f"item_bootstrap_{tag}"
    variant_id = f"variant_bootstrap_{tag}"
    type_name = "Beverage"
    conn.execute(
        """
        INSERT INTO menu_items (menu_item_id, name, type, is_verified)
        VALUES (?, ?, ?, 1)
        """,
        (item_id, f"Cloud Latte {tag}", type_name),
    )
    conn.execute(
        """
        INSERT INTO variants (variant_id, variant_name, unit, value, is_verified)
        VALUES (?, ?, 'ML', 350, 1)
        """,
        (variant_id, f"Large {tag}"),
    )
    conn.execute(
        """
        INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
        VALUES (101, ?, ?, 1)
        """,
        (item_id, variant_id),
    )
    conn.commit()
    return {
        "item_id": item_id,
        "variant_id": variant_id,
    }


def seed_bootstrap_target_fixture(conn: sqlite3.Connection) -> None:
    conn.executescript(BOOTSTRAP_TARGET_SCHEMA)
    conn.execute(
        """
        INSERT INTO order_items (order_item_id, menu_item_id, variant_id)
        VALUES (101, 'item_old', 'variant_old')
        """
    )
    conn.commit()


def read_single_value(conn: sqlite3.Connection, query: str, params: Tuple[Any, ...] = ()) -> Any:
    row = conn.execute(query, params).fetchone()
    return row[0] if row else None


def auth_headers(api_key: Optional[str], restaurant_id: str) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers["X-Restaurant-ID"] = restaurant_id
    return headers


def preflight_sync_api(base_url: str, api_key: Optional[str], restaurant_id: str) -> None:
    """
    Fail fast with actionable errors: Dachnona returns 401 without a valid Bearer token;
    404 usually means base URL does not reach the Django app that mounts desktop-analytics-sync.
    """
    url = f"{base_url}/desktop-analytics-sync/customer-merges"
    info(f"Preflight: GET {url}?limit=1")
    try:
        response = requests.get(
            url,
            headers=auth_headers(api_key, restaurant_id),
            params={"limit": 1},
            timeout=20,
        )
    except requests.RequestException as exc:
        fail(f"Preflight: cannot reach sync API ({url}): {exc}")

    if response.status_code in (401, 403):
        body = (response.text or "")[:400]
        hint = ""
        low = body.lower()
        if "invalid api key" in low:
            hint = (
                " The server read your Authorization header but the token value is wrong. "
                "In db.dachnona, find the env/settings variable that defines the desktop sync API secret "
                "(often in docker-compose.dev.yml or Django settings next to desktop-analytics-sync routes) "
                "and use that exact string for DACHNONA_E2E_API_KEY. It must match analytics "
                "system_config.cloud_sync_api_key when you point the desktop app at this stack."
            )
        fail(
            "Preflight: HTTP {} — Dachnona rejected sync API auth. "
            "Use Authorization: Bearer <key> (this script matches the analytics client). "
            "Set --api-key or env DACHNONA_E2E_API_KEY to the same secret the backend expects.{}"
            " Response: {!r}".format(response.status_code, hint, body)
        )
    if response.status_code == 404:
        fail(
            "Preflight: HTTP 404 on {} — wrong --base-url. Use the URL nginx/your compose file publishes "
            "for the Dachnona web app (where /analytics/... routes work), not a different server on the same port. "
            "Body: {!r}".format(url, (response.text or "")[:200])
        )
    if response.status_code >= 400:
        fail(f"Preflight: unexpected HTTP {response.status_code} from {url}: {(response.text or '')[:400]}")

    try:
        payload = response.json()
    except json.JSONDecodeError:
        fail(f"Preflight: {url} returned non-JSON (status {response.status_code})")
    require(isinstance(payload, dict), "Preflight: customer-merges pull did not return a JSON object")
    ok(f"Preflight: customer merge pull reachable (HTTP {response.status_code})")


def assert_merge_attribution(
    remote_event: Dict[str, Any],
    *,
    install_id: str,
    device_id: str,
    employee_id: Optional[str] = None,
) -> None:
    """Contract §12: attribution.device and attribution.employee survive pull."""
    attr = remote_event.get("attribution")
    require(isinstance(attr, dict), "Remote event is missing attribution object")
    dev = attr.get("device")
    require(isinstance(dev, dict), "Remote event is missing attribution.device")
    require(dev.get("install_id") == install_id, f"Unexpected attribution.device.install_id: {dev.get('install_id')}")
    require(dev.get("device_id") == device_id, f"Unexpected attribution.device.device_id: {dev.get('device_id')}")
    require(
        dev.get("device_label") is not None and str(dev.get("device_label")),
        "Remote event is missing attribution.device.device_label",
    )
    require(dev.get("platform") is not None, "Remote event is missing attribution.device.platform")
    require(dev.get("platform_release") is not None, "Remote event is missing attribution.device.platform_release")
    require(dev.get("machine") is not None, "Remote event is missing attribution.device.machine")
    if employee_id is not None:
        emp = attr.get("employee")
        require(isinstance(emp, dict), "Remote event is missing attribution.employee")
        require(
            emp.get("employee_id") == employee_id,
            f"Unexpected attribution.employee.employee_id: {emp.get('employee_id')}",
        )
        require(emp.get("name") is not None, "Remote event is missing attribution.employee.name")


def assert_customer_payload_parity(local_event: Dict[str, Any], remote_event: Dict[str, Any]) -> None:
    """Contract §4.5 / §8.3: pulled customer merge payload matches what the client ingested."""
    require(
        local_event.get("remote_event_id") == remote_event.get("remote_event_id"),
        "remote_event_id mismatch between local payload and cloud pull",
    )
    require(local_event.get("event_type") == remote_event.get("event_type"), "event_type mismatch between local and cloud")
    loc_sc = (local_event.get("source_customer") or {}).get("portable_locators") or {}
    rem_sc = (remote_event.get("source_customer") or {}).get("portable_locators") or {}
    require(loc_sc == rem_sc, "source_customer.portable_locators differs between local payload and cloud pull")
    loc_tc = (local_event.get("target_customer") or {}).get("portable_locators") or {}
    rem_tc = (remote_event.get("target_customer") or {}).get("portable_locators") or {}
    require(loc_tc == rem_tc, "target_customer.portable_locators differs between local payload and cloud pull")
    loc_lr = local_event.get("local_refs")
    rem_lr = remote_event.get("local_refs")
    require(loc_lr == rem_lr, "local_refs differs between local payload and cloud pull")


def assert_menu_payload_parity(local_event: Dict[str, Any], remote_event: Dict[str, Any]) -> None:
    require(
        local_event.get("remote_event_id") == remote_event.get("remote_event_id"),
        "remote_event_id mismatch between local menu payload and cloud pull",
    )
    require(local_event.get("event_type") == remote_event.get("event_type"), "menu event_type mismatch")
    require(
        (local_event.get("merge_payload") or {}) == (remote_event.get("merge_payload") or {}),
        "merge_payload differs between local payload and cloud pull",
    )
    require(
        (local_event.get("source_item") or {}).get("menu_item_id")
        == (remote_event.get("source_item") or {}).get("menu_item_id"),
        "source_item.menu_item_id mismatch",
    )
    require(
        (local_event.get("target_item") or {}).get("menu_item_id")
        == (remote_event.get("target_item") or {}).get("menu_item_id"),
        "target_item.menu_item_id mismatch",
    )


def verify_merge_ingest_idempotency(
    label: str,
    endpoint: str,
    api_key: Optional[str],
    restaurant_id: str,
    head_cursor: Optional[str],
    remote_event_id: str,
    page_events: List[Dict[str, Any]],
) -> None:
    """
    Contract §4.3: duplicate POST of the same remote_event_id must not add a second pullable row
    for the same cursor window (re-fetch from head_cursor yields one row with that id).
    """
    require(bool(remote_event_id), f"{label}: missing remote_event_id for idempotency check")
    require(
        sum(1 for e in page_events if e.get("remote_event_id") == remote_event_id) == 1,
        f"{label}: expected exactly one event for remote_event_id={remote_event_id} in pull page, got {page_events!r}",
    )
    again, _ = fetch_events_page(endpoint, api_key, restaurant_id, head_cursor)
    require(
        sum(1 for e in again if e.get("remote_event_id") == remote_event_id) == 1,
        f"{label}: idempotency check failed — duplicate pull rows for remote_event_id={remote_event_id}",
    )


def normalize_events_response(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    events = data.get("events")
    if not isinstance(events, list):
        events = data.get("items")
    if not isinstance(events, list):
        events = []
    next_cursor = data.get("next_cursor")
    if next_cursor is None:
        next_cursor = data.get("cursor_after")
    if next_cursor is None and events:
        next_cursor = data.get("cursor")
    return events, None if next_cursor is None else str(next_cursor)


def fetch_events_page(
    endpoint: str,
    api_key: Optional[str],
    restaurant_id: str,
    cursor: Optional[str],
    limit: int = 500,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    params: Dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    response = requests.get(
        endpoint,
        headers=auth_headers(api_key, restaurant_id),
        params=params,
        timeout=30,
    )
    if response.status_code >= 400:
        fail(f"GET {endpoint} failed with HTTP {response.status_code}: {response.text[:400]}")
    payload = response.json()
    require(isinstance(payload, dict), f"{endpoint} did not return a JSON object")
    return normalize_events_response(payload)


def discover_head_cursor(
    endpoint: str, api_key: Optional[str], restaurant_id: str
) -> Optional[str]:
    cursor: Optional[str] = None
    while True:
        events, next_cursor = fetch_events_page(endpoint, api_key, restaurant_id, cursor)
        if not events:
            return cursor if next_cursor is None else next_cursor
        if next_cursor is None or next_cursor == cursor:
            return next_cursor or cursor
        cursor = next_cursor


def fetch_events_since(
    endpoint: str,
    api_key: Optional[str],
    restaurant_id: str,
    cursor: Optional[str],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return fetch_events_page(endpoint, api_key, restaurant_id, cursor)


def get_customer_event_payload(conn: sqlite3.Connection, merge_id: int, event_type: str) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT payload
        FROM customer_merge_sync_events
        WHERE merge_id = ? AND event_type = ?
        LIMIT 1
        """,
        (merge_id, event_type),
    ).fetchone()
    require(row is not None, f"Missing local customer sync event for merge_id={merge_id} type={event_type}")
    return json.loads(row["payload"])


def get_menu_event_payload(conn: sqlite3.Connection, merge_id: int, event_type: str) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT payload
        FROM menu_merge_sync_events
        WHERE merge_id = ? AND event_type = ?
        LIMIT 1
        """,
        (merge_id, event_type),
    ).fetchone()
    require(row is not None, f"Missing local menu sync event for merge_id={merge_id} type={event_type}")
    return json.loads(row["payload"])


def run_bootstrap_flow(
    base_url: str,
    api_key: Optional[str],
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    expected_item_id: str,
    expected_variant_id: str,
) -> None:
    info("Running menu bootstrap latest pull/apply flow")
    ingest_endpoint = f"{base_url}/desktop-analytics-sync/menu-bootstrap/ingest"
    latest_endpoint = f"{base_url}/desktop-analytics-sync/menu-bootstrap/latest"

    push_result = upload_menu_bootstrap(
        source_conn,
        endpoint=ingest_endpoint,
        auth=api_key,
        uploaded_by={"employee_id": "0001", "name": "Owner"},
        uploaded_from={
            "device_id": "device-source-bootstrap",
            "install_id": "install-source-bootstrap",
            "device_label": "source-bootstrap",
            "platform": "Darwin",
            "platform_release": "test",
            "machine": "arm64",
        },
    )
    require(push_result.get("error") is None, f"Menu bootstrap upload failed: {push_result}")
    require(push_result.get("sent") is True, f"Menu bootstrap was not uploaded: {push_result}")

    fetch_result = fetch_latest_menu_bootstrap_snapshot(source_conn, latest_endpoint, auth=api_key)
    require(fetch_result.get("error") is None, f"Fetching latest menu bootstrap failed: {fetch_result}")
    require(
        fetch_result.get("id_maps", {}).get("menu_id_to_str", {}).get(expected_item_id) is not None,
        "Latest menu bootstrap snapshot did not return the expected menu item",
    )
    require(
        fetch_result.get("id_maps", {}).get("variant_id_to_str", {}).get(expected_variant_id) is not None,
        "Latest menu bootstrap snapshot did not return the expected variant",
    )

    apply_result = fetch_and_apply_menu_bootstrap_snapshot(
        target_conn,
        latest_endpoint,
        auth=api_key,
        apply_mode="seed_and_relink_orders",
    )
    require(apply_result.get("error") is None, f"Applying latest menu bootstrap failed: {apply_result}")
    require(
        apply_result.get("order_items_relinked") == 1,
        f"Expected 1 order_item relinked during bootstrap apply, got: {apply_result}",
    )
    require(
        read_single_value(target_conn, "SELECT menu_item_id FROM order_items WHERE order_item_id = 101") == expected_item_id,
        "Bootstrap apply did not relink the target order_item to the expected menu item",
    )
    require(
        read_single_value(target_conn, "SELECT variant_id FROM order_items WHERE order_item_id = 101") == expected_variant_id,
        "Bootstrap apply did not relink the target order_item to the expected variant",
    )
    ok("Menu bootstrap latest pull/apply passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Dachnona cloud sync end-to-end against a live server")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DACHNONA_E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        help="Cloud server base URL (default: env DACHNONA_E2E_BASE_URL or http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Bearer token for Dachnona desktop sync (default: env DACHNONA_E2E_API_KEY). Required when sync middleware enforces auth.",
    )
    parser.add_argument(
        "--restaurant-id",
        default=os.environ.get("DACHNONA_E2E_RESTAURANT_ID"),
        help="Physical restaurant ID for every scoped request (required; or DACHNONA_E2E_RESTAURANT_ID)",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temp SQLite databases and backup directories after the run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    api_key = (args.api_key or os.environ.get("DACHNONA_E2E_API_KEY") or "").strip() or None
    restaurant_id = str(args.restaurant_id or "").strip()
    if not restaurant_id or restaurant_id == "__all__":
        print("[FAIL] --restaurant-id must name one physical restaurant")
        return 2
    tag = uuid.uuid4().hex[:8]

    try:
        preflight_sync_api(base_url, api_key, restaurant_id)
    except E2EFailure as exc:
        print(f"[FAIL] {exc}")
        return 1

    temp_root_obj = tempfile.TemporaryDirectory(prefix="dachnona-cloud-sync-e2e-")
    temp_root = Path(temp_root_obj.name)
    if args.keep_temp:
        temp_root_obj.cleanup = lambda: None  # type: ignore[attr-defined]

    info(f"Using temp workspace: {temp_root}")
    info(f"Using unique test tag: {tag}")

    source_bootstrap_conn = make_conn(temp_root / "source_bootstrap.db")
    target_bootstrap_conn = make_conn(temp_root / "target_bootstrap.db")

    connections = [
        source_bootstrap_conn,
        target_bootstrap_conn,
    ]

    try:
        bootstrap_ids = seed_bootstrap_source_fixture(source_bootstrap_conn, tag)
        seed_bootstrap_target_fixture(target_bootstrap_conn)

        init_identity(source_bootstrap_conn, "0001", "Owner", "device-source-bootstrap", "install-source-bootstrap", restaurant_id)
        init_identity(target_bootstrap_conn, "0002", "Target", "device-target-bootstrap", "install-target-bootstrap", restaurant_id)

        run_bootstrap_flow(
            base_url,
            api_key,
            source_bootstrap_conn,
            target_bootstrap_conn,
            expected_item_id=bootstrap_ids["item_id"],
            expected_variant_id=bootstrap_ids["variant_id"],
        )
        ok("All Dachnona cloud sync E2E checks passed")
        if args.keep_temp:
            info(f"Temp artifacts kept at: {temp_root}")
        return 0
    except E2EFailure as exc:
        print(f"[FAIL] {exc}")
        if args.keep_temp:
            info(f"Temp artifacts kept at: {temp_root}")
        return 1
    finally:
        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass
        if not args.keep_temp:
            try:
                temp_root_obj.cleanup()
            except Exception:
                shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
