"""
Shared Dependencies for FastAPI Routers

This module contains common dependencies used across multiple routers to avoid duplication.
"""

from datetime import datetime, timezone as dt_timezone
from typing import Any, Callable, Dict, Optional

from fastapi import Depends, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from src.core.analytics_scope import AnalyticsScope, scope_from_token
from src.core.db.connection import get_profile_connection
from src.core.profiles import (
    ALL_STORES_TOKEN,
    ProfileError,
    get_profile,
    validate_restaurant_id,
)
from src.core.queries.multi_store import federate
from src.core.utils.business_date import (
    bind_business_date_context,
    business_date_context,
    reset_business_date_context,
)


def _single_restaurant_required() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "Select one physical restaurant for this action",
            "code": "single_restaurant_required",
        },
    )


def get_restaurant_profile(x_analytics_scope: str = Header(None, alias="X-Analytics-Scope")):
    if str(x_analytics_scope or "").strip() == ALL_STORES_TOKEN:
        # Every state-changing route and every endpoint without an explicit All
        # Stores reducer resolves through here, so All mode is refused before a
        # database is opened or a central request is built.
        raise _single_restaurant_required()
    try:
        restaurant_id = validate_restaurant_id(x_analytics_scope)
        profile = get_profile(restaurant_id)
        if not profile.is_bound:
            raise ProfileError(f"Restaurant profile is not initialized: {restaurant_id}")
        return profile
    except ProfileError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": str(exc), "code": getattr(exc, "code", "profile_error")},
        ) from exc


def get_authorized_restaurant_profile(
    profile=Depends(get_restaurant_profile),
):
    if profile.authorization_state != "authorized":
        raise HTTPException(
            status_code=403,
            detail={
                "error": f"Restaurant profile is not authorized: {profile.restaurant_id}",
                "code": "restaurant_forbidden",
            },
        )
    return profile


async def _open_profile_connection(profile):
    """Open one profile connection without blocking the event loop.

    Opening applies the canonical schema, so it is real file I/O and belongs in
    a worker thread. The connection itself is created with
    ``check_same_thread=False`` and is used later from the endpoint's own
    thread, exactly as before.
    """
    try:
        conn, _ = await run_in_threadpool(get_profile_connection, profile)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": str(exc), "code": "profile_open_failed"},
        ) from exc
    return conn


async def get_db(profile=Depends(get_restaurant_profile)):
    """
    Database connection dependency for FastAPI routes.

    Yields a database connection and ensures it's properly closed after use.

    This is deliberately an async generator. FastAPI runs a *sync* generator
    dependency's setup and teardown as two separate threadpool calls, and each
    one gets its own copied ``contextvars.Context``: the business-date binding
    would be discarded before the endpoint ran, and resetting its token during
    teardown would raise ``Token was created in a different Context``. On the
    event loop both halves share one context, and the endpoint — sync or async
    — inherits the binding.

    Usage:
        @router.get("/endpoint")
        def my_endpoint(conn = Depends(get_db)):
            # use conn here
    """
    conn = await _open_profile_connection(profile)
    _context, token = bind_business_date_context(timezone=profile.timezone)
    try:
        yield conn
    finally:
        reset_business_date_context(token)
        conn.close()


async def get_authorized_db(profile=Depends(get_authorized_restaurant_profile)):
    """Writable/scoped-network database dependency.

    Profiles removed from the latest server allow-list remain available through
    `get_db` for offline reads, but must not accept local mutations or start new
    scoped network work.
    """
    conn = await _open_profile_connection(profile)
    _context, token = bind_business_date_context(timezone=profile.timezone)
    try:
        yield conn
    finally:
        reset_business_date_context(token)
        conn.close()


def get_analytics_scope(
    x_analytics_scope: str = Header(None, alias="X-Analytics-Scope")
) -> AnalyticsScope:
    """Resolve the local scope header to one restaurant or a frozen All snapshot."""
    try:
        return scope_from_token(x_analytics_scope)
    except ProfileError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": str(exc), "code": getattr(exc, "code", "profile_error")},
        ) from exc


class ScopedReader:
    """Runs a read-only query under the request's scope.

    Single restaurant: the ordinary profile connection, unchanged.
    All Stores: the same query per profile, combined by an explicit reducer and
    returned inside a completeness envelope.

    An endpoint without a reducer is single-restaurant-only; asking for All mode
    fails closed rather than guessing an aggregation rule.
    """

    def __init__(self, scope: AnalyticsScope):
        self.scope = scope
        # One instant for the whole request so stores cannot disagree about
        # which moment "today" was computed from (plan §7.6).
        self.as_of = datetime.now(dt_timezone.utc)

    @property
    def is_all(self) -> bool:
        return self.scope.is_all

    @property
    def profile(self):
        return self.scope.profile

    def read(
        self,
        query: Callable[..., Any],
        reducer: Optional[Callable[[Any], Any]] = None,
    ) -> Any:
        if self.scope.is_all:
            if reducer is None:
                raise _single_restaurant_required()
            return federate(self.scope, query, reducer, as_of=self.as_of)
        profile = self.scope.profile
        try:
            conn, _ = get_profile_connection(profile)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": str(exc), "code": "profile_open_failed"},
            ) from exc
        try:
            with business_date_context(timezone=profile.timezone, as_of=self.as_of):
                return query(conn, profile)
        finally:
            conn.close()

    def read_together(self, build: Callable[..., Any]) -> Any:
        """Run one query that needs every profile's rows at the same time.

        Customer metrics recompute rates from combined order atoms, so they
        cannot be reduced from finished per-store percentages. `build` receives
        `[(profile, connection)]` and, in All Stores mode, a
        `FederatedOrdersSource` that profile-qualifies customers.

        Every connection is opened read-only and closed in `finally`.
        """
        from src.core.queries.customer_metric_sources import (
            federated_orders_source,
            orders_source_for,
        )
        from src.core.queries.multi_store import (
            FederationOutcome,
            ProfileResult,
            build_envelope,
            latest_business_date_timezone,
            read_only_connection,
        )

        if not self.scope.is_all:
            profile = self.scope.profile
            try:
                conn, _ = get_profile_connection(profile)
            except Exception as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": str(exc), "code": "profile_open_failed"},
                ) from exc
            try:
                with business_date_context(timezone=profile.timezone, as_of=self.as_of):
                    return build([(profile, conn)], orders_source_for(conn))
            finally:
                conn.close()

        outcome = FederationOutcome(requested=len(self.scope.profiles))
        members = []
        try:
            for profile in self.scope.profiles:
                try:
                    members.append((profile, read_only_connection(profile)))
                    outcome.results.append(ProfileResult(profile=profile, value=None))
                except Exception as exc:  # noqa: BLE001 - reported, never zeroed
                    outcome.incomplete.append(
                        {
                            "restaurant_id": profile.restaurant_id,
                            "restaurant_name": profile.display_name,
                            "error": str(exc),
                            "code": getattr(exc, "code", "profile_read_failed"),
                        }
                    )
            timezone_name = latest_business_date_timezone(
                [profile for profile, _ in members], self.as_of
            )
            with business_date_context(timezone=timezone_name, as_of=self.as_of):
                data = build(members, federated_orders_source(members))
            return build_envelope(self.scope, outcome, data)
        finally:
            for _profile, conn in members:
                conn.close()

    def scope_metadata(self) -> Dict[str, Any]:
        return {
            "scope": "all" if self.scope.is_all else "restaurant",
            "profiles": [profile.restaurant_id for profile in self.scope.profiles],
        }


def get_reader(scope: AnalyticsScope = Depends(get_analytics_scope)) -> ScopedReader:
    return ScopedReader(scope)
