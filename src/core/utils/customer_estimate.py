"""
Heuristic range for “how many people” implied by order volume.

Splits successful orders into verified-customer orders vs the rest, uses
orders-per-verified-customer as a baseline intensity, and assumes unverified /
anonymous guests repeat at a lower intensity (industry prior k in [k_low, k_high]).

This is not a deduplicated identity count; it avoids false precision from raw
customer_id cardinality.
"""

from __future__ import annotations

from typing import Any, Tuple

# Unverified pool: orders per implied person ≈ k × (verified_orders / verified_customers).
# Higher k ⇒ stronger repeat among guests ⇒ fewer implied people (lower total estimate).
UNVERIFIED_REPEAT_FACTOR_LOW: float = 0.8
UNVERIFIED_REPEAT_FACTOR_HIGH: float = 1.0


def estimate_customer_count_range_from_split(
    verified_orders: Any,
    unverified_orders: Any,
    verified_customers: Any,
    *,
    k_low: float = UNVERIFIED_REPEAT_FACTOR_LOW,
    k_high: float = UNVERIFIED_REPEAT_FACTOR_HIGH,
) -> Tuple[float, float, int, int]:
    """
    Total people (est.) ≈ verified_customers + unverified_orders / (k × avg_orders_verified).

    avg_orders_verified = verified_orders / verified_customers when verified_customers > 0.

    Bounds use k_high for the low total and k_low for the high total (weaker repeat ⇒ more people).

    Returns:
        (low_raw, high_raw, low_rounded, high_rounded)
    """
    try:
        v_o = float(verified_orders)
    except (TypeError, ValueError):
        v_o = 0.0
    try:
        u_o = float(unverified_orders)
    except (TypeError, ValueError):
        u_o = 0.0
    try:
        v = float(verified_customers)
    except (TypeError, ValueError):
        v = 0.0

    if k_low <= 0 or k_high <= 0 or k_low > k_high:
        k_low, k_high = (
            UNVERIFIED_REPEAT_FACTOR_LOW,
            UNVERIFIED_REPEAT_FACTOR_HIGH,
        )

    if v <= 0:
        if u_o <= 0:
            z = 0.0
            zi = 0
            return z, z, zi, zi
        # No verified anchor: assume ~1 order per implied person at baseline intensity 1.
        low_raw = u_o / (k_high * 1.0)
        high_raw = u_o / (k_low * 1.0)
    else:
        avg = v_o / v
        if avg <= 0:
            avg = 1.0
        low_raw = v + u_o / (k_high * avg)
        high_raw = v + u_o / (k_low * avg)

    low_i = int(round(low_raw))
    high_i = int(round(high_raw))
    if low_i > high_i:
        low_i, high_i = high_i, low_i
    return low_raw, high_raw, low_i, high_i
