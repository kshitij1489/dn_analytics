"""Order sources for customer metrics — one store, or All Stores combined.

Customer analytics are computed from a list of `CustomerMetricOrder` atoms by
pure builders. All Stores therefore combines the *atoms*, not the finished
percentages, so every rate is recomputed from the combined numerator and
denominator (plan §7.4).

Customers are profile-qualified: a local `customer_id` is offset by its store's
index so two stores' customer 41 stay two different people. Nothing is merged
across stores by name, phone, or address — that needs a separate privacy and
identity contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.profiles import RestaurantProfile
from src.core.queries.customer_metric_fetchers import (
    fetch_customer_metric_orders,
    fetch_total_verified_customers,
)
from src.core.queries.customer_metric_types import CustomerMetricOrder

# Local integer IDs are far below this, so `index * STRIDE + local_id` is a
# reversible qualification rather than a hash.
CUSTOMER_ID_STRIDE = 10 ** 12


class LocalIdTooLarge(RuntimeError):
    """A local ID cannot be profile-qualified without colliding."""


@dataclass(frozen=True)
class ConnectionOrdersSource:
    """One physical restaurant: the ordinary single-store behavior."""

    conn: Any

    def fetch(self, **kwargs) -> List[CustomerMetricOrder]:
        return fetch_customer_metric_orders(self.conn, **kwargs)

    def count_verified_customers(self) -> int:
        return fetch_total_verified_customers(self.conn)

    def qualify_rows(self, rows: Sequence[Dict[str, Any]], **_kwargs) -> List[Dict[str, Any]]:
        return list(rows)


@dataclass(frozen=True)
class FederatedOrdersSource:
    """All Stores: profile-qualified atoms from every member database."""

    members: Tuple[Tuple[RestaurantProfile, Any], ...]

    def _qualify(self, index: int, value: int) -> int:
        if value >= CUSTOMER_ID_STRIDE:
            raise LocalIdTooLarge(
                f"Local id {value} is too large to profile-qualify for All Stores"
            )
        return index * CUSTOMER_ID_STRIDE + value

    def fetch(self, **kwargs) -> List[CustomerMetricOrder]:
        combined: List[CustomerMetricOrder] = []
        for index, (_profile, conn) in enumerate(self.members):
            for order in fetch_customer_metric_orders(conn, **kwargs):
                combined.append(
                    replace(
                        order,
                        customer_id=self._qualify(index, int(order.customer_id)),
                        order_id=self._qualify(index, int(order.order_id)),
                    )
                )
        # Builders group by customer and walk orders in time order.
        combined.sort(key=lambda order: (order.customer_id, order.created_on, order.order_id))
        return combined

    def count_verified_customers(self) -> int:
        return sum(fetch_total_verified_customers(conn) for _profile, conn in self.members)

    def decode(self, qualified_id: int) -> Tuple[Optional[RestaurantProfile], int]:
        index, local_id = divmod(int(qualified_id), CUSTOMER_ID_STRIDE)
        if 0 <= index < len(self.members):
            return self.members[index][0], local_id
        return None, local_id

    def qualify_rows(
        self, rows: Sequence[Dict[str, Any]], *, id_field: str = "customer_id"
    ) -> List[Dict[str, Any]]:
        """Restore each row's real local ID and name the store that owns it."""
        qualified: List[Dict[str, Any]] = []
        for row in rows:
            enriched = dict(row)
            raw_id = enriched.get(id_field)
            if isinstance(raw_id, int) and not isinstance(raw_id, bool):
                profile, local_id = self.decode(raw_id)
                enriched[id_field] = local_id
                if profile is not None:
                    enriched["restaurant_id"] = profile.restaurant_id
                    enriched["restaurant_name"] = profile.display_name
                    enriched["row_key"] = f"{profile.restaurant_id}:{local_id}"
            qualified.append(enriched)
        return qualified


def orders_source_for(conn) -> ConnectionOrdersSource:
    return ConnectionOrdersSource(conn=conn)


def resolve_orders_source(conn, orders_source=None):
    """Use the injected source when present, else this connection's own orders."""
    if orders_source is not None:
        return orders_source
    return ConnectionOrdersSource(conn=conn)


def federated_orders_source(members: Sequence[Tuple[RestaurantProfile, Any]]) -> FederatedOrdersSource:
    return FederatedOrdersSource(members=tuple(members))
