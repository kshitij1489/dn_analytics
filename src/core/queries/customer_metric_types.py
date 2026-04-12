"""
Shared types and pure utility functions for customer metric calculations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

BUSINESS_DAY_OFFSET_HOURS = 5


@dataclass(frozen=True)
class CustomerMetricFilters:
    evaluation_start_date: str | None = None
    evaluation_end_date: str | None = None
    lookback_start_date: str | None = None
    lookback_end_date: str | None = None
    lookback_days: int | None = None
    min_orders_per_customer: int = 2
    order_sources: tuple[str, ...] | None = None
    verified_only: bool = True
    order_status: str = "Success"

    def __post_init__(self) -> None:
        if bool(self.evaluation_start_date) != bool(self.evaluation_end_date):
            raise ValueError("evaluation_start_date and evaluation_end_date must be provided together.")
        if (
            self.evaluation_start_date and self.evaluation_end_date
            and self.evaluation_start_date > self.evaluation_end_date
        ):
            raise ValueError("evaluation_start_date cannot be after evaluation_end_date.")
        if (
            self.lookback_start_date and self.lookback_end_date
            and self.lookback_start_date > self.lookback_end_date
        ):
            raise ValueError("lookback_start_date cannot be after lookback_end_date.")
        if self.lookback_days is not None and self.lookback_days < 1:
            raise ValueError("lookback_days must be at least 1 when provided.")
        if self.min_orders_per_customer < 1:
            raise ValueError("min_orders_per_customer must be at least 1.")


@dataclass(frozen=True)
class CustomerMetricOrder:
    order_id: int
    customer_id: int
    customer_name: str | None
    created_on: str
    total: float
    order_from: str
    business_date: str
    business_month: str


def shift_month(month_start: DateType, months: int) -> DateType:
    total_months = (month_start.year * 12) + (month_start.month - 1) + months
    return DateType(total_months // 12, (total_months % 12) + 1, 1)


def month_bounds(month_start: DateType) -> tuple[str, str]:
    next_month_start = shift_month(month_start, 1)
    month_end = next_month_start - timedelta(days=1)
    return month_start.isoformat(), month_end.isoformat()


def normalize_order_sources(order_sources: Sequence[str] | None) -> tuple[str, ...] | None:
    if not order_sources:
        return None

    normalized = tuple(
        dict.fromkeys(
            source.strip() for source in order_sources
            if source and source.strip() and source.strip().lower() != "all"
        )
    )
    return normalized or None


def resolve_lookback_window(filters: CustomerMetricFilters) -> tuple[str | None, str | None]:
    if filters.lookback_start_date or filters.lookback_end_date:
        return filters.lookback_start_date, filters.lookback_end_date

    if not filters.lookback_days or not filters.evaluation_start_date:
        return None, None

    evaluation_start = DateType.fromisoformat(filters.evaluation_start_date)
    lookback_end = evaluation_start - timedelta(days=1)
    lookback_start = lookback_end - timedelta(days=filters.lookback_days - 1)
    return lookback_start.isoformat(), lookback_end.isoformat()


def percentage(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return float(round_half_up((100.0 * numerator) / denominator, 2))


def percentage_decimal(numerator: Decimal, denominator: Decimal) -> float:
    if not denominator:
        return 0.0
    return float(round_half_up((Decimal("100.0") * numerator) / denominator, 2))


def round_half_up(value: int | float | Decimal, digits: int = 2) -> int | float:
    exponent = Decimal("1") if digits == 0 else Decimal(f"1e-{digits}")
    rounded = Decimal(str(value)).quantize(exponent, rounding=ROUND_HALF_UP)
    if digits == 0:
        return int(rounded)
    return float(rounded)


def cast_customer_ids(value: object) -> set[int]:
    return value if isinstance(value, set) else set()


def cast_decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal("0")
