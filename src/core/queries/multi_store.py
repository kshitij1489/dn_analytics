"""Read-only All Stores query federation.

One request resolves an immutable profile list, opens each profile database
read-only, runs the ordinary single-store query (or its metric-atom variant),
attributes the result to its restaurant, and combines the results with an
endpoint-specific reducer. Nothing is copied into a mixed database and no
profile is written through this path.

A profile that cannot be read is reported in `incomplete_profiles`; it is never
treated as zero (plan invariant 11).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.core.analytics_scope import AnalyticsScope
from src.core.profiles import RestaurantProfile
from src.core.utils.business_date import business_date_context

logger = logging.getLogger(__name__)

ProfileQuery = Callable[..., Any]


class FederationAbort(Exception):
    """Stop the whole federated request instead of dropping one store.

    A store that simply cannot be read is recorded in `incomplete_profiles`. A
    deliberate refusal — a bound the caller has to respect — must reach the
    client as its own error rather than degrading into a partial result that
    silently omits a store.
    """

    def __init__(self, error: BaseException):
        super().__init__(str(error))
        self.error = error


@dataclass
class ProfileResult:
    profile: RestaurantProfile
    value: Any


@dataclass
class FederationOutcome:
    results: List[ProfileResult] = field(default_factory=list)
    incomplete: List[Dict[str, str]] = field(default_factory=list)
    requested: int = 0

    @property
    def values(self) -> List[Any]:
        return [result.value for result in self.results]

    @property
    def pairs(self) -> List[Tuple[RestaurantProfile, Any]]:
        return [(result.profile, result.value) for result in self.results]


def read_only_connection(profile: RestaurantProfile) -> sqlite3.Connection:
    """Open one bound profile database read-only and prove its identity."""
    from src.core.profiles import ProfileMismatch, validate_restaurant_id

    restaurant_id = validate_restaurant_id(profile.restaurant_id)
    path = Path(profile.database_path).expanduser().resolve()
    if not path.exists():
        raise ProfileMismatch(f"Profile database is missing: {path}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            raise ProfileMismatch(f"Profile database is not bound: {restaurant_id}")
        if str(row[0]) != restaurant_id:
            raise ProfileMismatch(
                f"Profile database is bound to {row[0]}, not {restaurant_id}"
            )
        return conn
    except Exception:
        conn.close()
        raise


def run_for_profiles(
    profiles: Sequence[RestaurantProfile],
    query: ProfileQuery,
    *,
    as_of: Optional[datetime] = None,
) -> FederationOutcome:
    """Run one read-only query per profile, in the frozen order it was given."""
    captured_as_of = as_of or datetime.now(dt_timezone.utc)
    outcome = FederationOutcome(requested=len(profiles))
    for profile in profiles:
        conn = None
        try:
            conn = read_only_connection(profile)
            with business_date_context(timezone=profile.timezone, as_of=captured_as_of):
                value = query(conn, profile)
            outcome.results.append(ProfileResult(profile=profile, value=value))
        except FederationAbort as abort:
            raise abort.error from None
        except Exception as exc:  # noqa: BLE001 - reported, never silently zeroed
            logger.warning(
                "All Stores read failed for %s: %s", profile.restaurant_id, exc
            )
            outcome.incomplete.append(
                {
                    "restaurant_id": profile.restaurant_id,
                    "restaurant_name": profile.display_name,
                    "error": str(exc),
                    "code": getattr(exc, "code", "profile_read_failed"),
                }
            )
        finally:
            if conn is not None:
                conn.close()
    return outcome


def latest_business_date_timezone(
    profiles: Sequence[RestaurantProfile], as_of: datetime
) -> str:
    """Timezone whose local business date is furthest ahead at one instant.

    A combined-atoms query (customer metrics) needs one calendar frame for
    "current month" style windows. Taking the latest local business date keeps
    every store's own today inside the window instead of cutting one store off.
    """
    from src.core.utils.business_date import DEFAULT_TIMEZONE, get_current_business_date

    best_timezone = DEFAULT_TIMEZONE
    best_date = ""
    for profile in profiles:
        with business_date_context(timezone=profile.timezone, as_of=as_of):
            business_date = get_current_business_date()
        if business_date > best_date:
            best_date = business_date
            best_timezone = profile.timezone
    return best_timezone


def build_envelope(scope: AnalyticsScope, outcome: FederationOutcome, data: Any) -> Dict[str, Any]:
    """Wrap combined data with completeness metadata."""
    identity_coverage = None
    if isinstance(data, dict) and data.get("__identity_aware_federated_data__") is True:
        identity_coverage = data.get("identity_coverage")
        data = data.get("data")
    envelope = {
        "scope": "all",
        "profiles_requested": outcome.requested,
        "profiles_included": len(outcome.results),
        "incomplete_profiles": outcome.incomplete,
        # Bound profiles deliberately outside the federation (revoked grants).
        # Reported so a shrinking All Stores total is never silent.
        "excluded_profiles": [
            {
                "restaurant_id": profile.restaurant_id,
                "restaurant_name": profile.display_name,
                "code": "restaurant_unauthorized",
            }
            for profile in scope.excluded
        ],
        "stores": [
            {
                "restaurant_id": result.profile.restaurant_id,
                "restaurant_name": result.profile.display_name,
                "timezone": result.profile.timezone,
            }
            for result in outcome.results
        ],
        "data": data,
    }
    if identity_coverage is not None:
        envelope["identity_coverage"] = identity_coverage
    return envelope


def federate(
    scope: AnalyticsScope,
    query: ProfileQuery,
    reducer: Callable[[List[Tuple[RestaurantProfile, Any]]], Any],
    *,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run `query` across the scope's profiles and reduce with explicit rules."""
    outcome = run_for_profiles(scope.profiles, query, as_of=as_of)
    return build_envelope(scope, outcome, reducer(outcome.pairs))
