"""Two-profile fixtures for All Stores tests.

Every store gets its own SQLite profile with deliberately colliding local IDs
(same Petpooja order ID, same customer ID, same menu item ID) so leakage between
profiles is visible instead of plausible.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.core.db.connection import get_profile_connection
from src.core.profiles import bind_and_select_profile, get_profile, upsert_allowed_restaurants


def allowed(*restaurants):
    """`(restaurant_id, display_name, timezone)` triples for the allowed list."""
    return [
        {"restaurant_id": rid, "display_name": name, "timezone": timezone}
        for rid, name, timezone in restaurants
    ]


class TwoStoreFixture:
    """Temporary app-data root holding two bound, authorized profiles."""

    def __init__(self, timezones=("Asia/Kolkata", "Asia/Kolkata")):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.analytics_path = self.root / "analytics.db"
        self.timezones = timezones
        self.env_patch = patch.dict(
            os.environ,
            {
                "ANALYTICS_APP_DATA_ROOT": str(self.root),
                "ANALYTICS_DB_PATH": str(self.analytics_path),
                "ANALYTICS_CONTROL_DB_PATH": str(self.root / "analytics-control.db"),
                "DB_URL": str(self.analytics_path),
            },
            clear=False,
        )

    def start(self):
        self.env_patch.start()
        upsert_allowed_restaurants(
            allowed(
                ("rest-A", "Dach & Nona", self.timezones[0]),
                ("rest-B", "Dach & Nona Two", self.timezones[1]),
            )
        )
        bind_and_select_profile("rest-A")
        bind_and_select_profile("rest-B")
        return self

    def stop(self):
        self.env_patch.stop()
        self.temp_dir.cleanup()

    @property
    def profiles(self):
        return [get_profile("rest-A"), get_profile("rest-B")]

    def connection(self, restaurant_id: str):
        conn, _ = get_profile_connection(get_profile(restaurant_id))
        return conn

    def seed_store(
        self,
        restaurant_id: str,
        *,
        order_total: float,
        customer_name: str,
        item_name: str,
        item_type: str = "Dessert",
        business_datetime: str = "2026-08-08 12:00:00",
        petpooja_order_id: int = 19470,
        quantity: int = 2,
        customer_id: int = 41,
    ):
        """Insert one verified customer with one successful order of one item.

        The identifiers are intentionally identical across stores. Repeat calls
        reuse the store's restaurant, customer, and menu rows.
        """
        conn = self.connection(restaurant_id)
        try:
            existing = conn.execute(
                "SELECT restaurant_id FROM restaurants WHERE petpooja_restid=?",
                (restaurant_id,),
            ).fetchone()
            restaurant_pk = existing[0] if existing else conn.execute(
                "INSERT INTO restaurants (petpooja_restid, name) VALUES (?, ?) RETURNING restaurant_id",
                (restaurant_id, restaurant_id),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO customers (
                    customer_id, name, name_normalized, phone, first_order_date,
                    last_order_date, total_orders, total_spent, is_verified
                ) VALUES (?, ?, ?, '9990000000', ?, ?, 1, ?, 1)
                ON CONFLICT(customer_id) DO UPDATE SET
                    last_order_date=excluded.last_order_date,
                    total_orders=customers.total_orders + 1,
                    total_spent=customers.total_spent + excluded.total_spent
                """,
                (
                    customer_id,
                    customer_name,
                    customer_name.lower(),
                    business_datetime[:10],
                    business_datetime[:10],
                    order_total,
                ),
            )
            conn.execute(
                """
                INSERT INTO menu_items (menu_item_id, name, type, is_active)
                VALUES ('mi-1', ?, ?, 1)
                ON CONFLICT(menu_item_id) DO NOTHING
                """,
                (item_name, item_type),
            )
            order_pk = conn.execute(
                """
                INSERT INTO orders (
                    petpooja_order_id, stream_id, event_id, aggregate_id, customer_id,
                    restaurant_id, occurred_at, created_on, order_type, order_from,
                    order_status, tax_total, total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Delivery', 'POS', 'Success', 0, ?)
                RETURNING order_id
                """,
                (
                    petpooja_order_id,
                    petpooja_order_id,
                    f"event-{petpooja_order_id}",
                    str(petpooja_order_id),
                    customer_id,
                    restaurant_pk,
                    business_datetime,
                    business_datetime,
                    order_total,
                ),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO order_items (
                    order_id, menu_item_id, name_raw, category_name, quantity,
                    unit_price, total_price
                ) VALUES (?, 'mi-1', ?, ?, ?, ?, ?)
                """,
                (
                    order_pk,
                    item_name,
                    item_type,
                    quantity,
                    order_total / quantity,
                    order_total,
                ),
            )
            conn.commit()
        finally:
            conn.close()
