"""Local analytics scope: exactly one restaurant, or the All Stores federation.

`AnalyticsScope` is the immutable answer to "which databases does this request
read?". An All Stores scope is a snapshot of physical profiles taken once, so a
selection change mid-request cannot alter the set a job or query is working on.
The `__all__` token is local-only: it never becomes a profile identity and never
reaches the central server (contract §4.2 has no such selector).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from src.core.profiles import (
    ALL_STORES_TOKEN,
    AllStoresUnavailable,
    ProfileSelectionRequired,
    RestaurantProfile,
    excluded_federation_profiles,
    federation_profiles,
    get_profile,
    selected_selection,
    validate_restaurant_id,
)


SCOPE_HEADER = "X-Analytics-Scope"


@dataclass(frozen=True)
class AnalyticsScope:
    kind: str  # 'restaurant' | 'all'
    profiles: Tuple[RestaurantProfile, ...]
    excluded: Tuple[RestaurantProfile, ...] = ()

    @property
    def is_all(self) -> bool:
        return self.kind == "all"

    @property
    def profile(self) -> RestaurantProfile:
        """The one physical restaurant. Never guesses inside an All scope."""
        if self.is_all or len(self.profiles) != 1:
            raise ProfileSelectionRequired("Select one physical restaurant")
        return self.profiles[0]

    def describe(self) -> str:
        if not self.is_all:
            return self.profiles[0].display_name if self.profiles else "unselected"
        return f"All Stores · {len(self.profiles)} store(s)"


def restaurant_scope(profile: RestaurantProfile) -> AnalyticsScope:
    return AnalyticsScope(kind="restaurant", profiles=(profile,))


def all_stores_scope(profiles: Optional[Sequence[RestaurantProfile]] = None) -> AnalyticsScope:
    """Freeze the current All Stores membership.

    Selecting All Stores needs two authorized profiles, but a scope that already
    exists must keep working when one is revoked: the survivor is still read and
    the revoked profile is reported as excluded rather than silently dropped.
    """
    members = tuple(profiles if profiles is not None else federation_profiles())
    if not members:
        raise AllStoresUnavailable(
            "No authorized restaurant has an initialized database"
        )
    return AnalyticsScope(
        kind="all",
        profiles=members,
        excluded=tuple(excluded_federation_profiles()),
    )


def scope_from_token(token: Optional[str]) -> AnalyticsScope:
    """Resolve the local scope header. Requires an explicit, initialized target."""
    raw = str(token or "").strip()
    if raw == ALL_STORES_TOKEN:
        return all_stores_scope()
    restaurant_id = validate_restaurant_id(raw)
    profile = get_profile(restaurant_id)
    if not profile.is_bound:
        from src.core.profiles import ProfileError

        raise ProfileError(f"Restaurant profile is not initialized: {restaurant_id}")
    return restaurant_scope(profile)


def selected_scope() -> AnalyticsScope:
    """The persisted selection as a scope (used by background work, not requests)."""
    selection = selected_selection()
    if selection["selection_mode"] == "all":
        return all_stores_scope()
    restaurant_id = selection["restaurant_id"]
    if not restaurant_id:
        raise ProfileSelectionRequired("Select one physical restaurant")
    return restaurant_scope(get_profile(str(restaurant_id)))
