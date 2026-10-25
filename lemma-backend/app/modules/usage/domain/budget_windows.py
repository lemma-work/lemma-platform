"""The limit windows spend counts in, for one request and for a run about to start."""

from datetime import datetime, timedelta
from uuid import UUID

from app.modules.usage.domain.accounting import (
    OUTSIDE_AUDIENCE_SOURCES,
    BudgetWindow,
    MeteringIdentity,
    money,
)
from app.modules.usage.domain.ports import UsageLimitValues


def budget_windows(
    identity: MeteringIdentity, limits: UsageLimitValues, now: datetime
) -> list[BudgetWindow]:
    """The windows one provider request's spend counts in."""
    if identity.profile_scope != "SYSTEM":
        return []
    return spend_windows(
        organization_id=identity.organization_id,
        user_id=identity.user_id,
        outside_audience=identity.source_type in OUTSIDE_AUDIENCE_SOURCES,
        limits=limits,
        now=now,
    )


def spend_windows(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    outside_audience: bool,
    limits: UsageLimitValues,
    now: datetime,
) -> list[BudgetWindow]:
    """The windows system-paid spend by ``user_id`` in ``organization_id`` counts in.

    Whose spend it is decides them -- the organization, the member, and whether
    the run answers people outside the organization -- never what a request
    was for. So a run can be held to the windows its requests will be before
    it has made one.
    """
    month, next_month = _month_of(now)
    week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    user_org = organization_id if limits.user_limit_scope == "organization" else None
    excluded = limits.excluded_organization_ids if user_org is None else ()
    windows = []
    for org, user, kind, start, end, limit in (
        (
            organization_id,
            None,
            "org_month",
            month,
            next_month,
            limits.org_monthly_limit_usd,
        ),
        (
            user_org,
            user_id,
            "user_week",
            week,
            week + timedelta(days=7),
            limits.user_weekly_limit_usd,
        ),
        (
            user_org,
            user_id,
            "user_month",
            month,
            next_month,
            limits.user_monthly_limit_usd,
        ),
    ):
        if user is None and org is None:
            continue
        # A run answering somebody outside the organization is the
        # organization's spend, not the member's who looks after it.
        if user is not None and (outside_audience or organization_id in excluded):
            continue
        windows.append(
            BudgetWindow(
                organization_id=org,
                user_id=user,
                kind=kind,
                start=start,
                end=end,
                limit=None if limit is None else money(limit),
                excluded_organization_ids=excluded if user is not None else (),
                # What answering people outside cost is the organization's,
                # so it never fills the allowance of the member who looked
                # after the conversation either.
                excluded_source_types=(
                    OUTSIDE_AUDIENCE_SOURCES if user is not None else ()
                ),
            )
        )
    if outside_audience and organization_id is not None:
        windows.append(contacts_window(organization_id, limits, now))
    return windows


def contacts_window(
    organization_id: UUID, limits: UsageLimitValues, now: datetime
) -> BudgetWindow:
    """The organization's contacts cap, counting only runs for people outside.

    Every request of such a run counts in it (``spend_windows``), and a surface
    asks it before starting one: one window, so both read the same total.
    """
    month, next_month = _month_of(now)
    cap = limits.contacts_monthly_limit_usd
    return BudgetWindow(
        organization_id=organization_id,
        user_id=None,
        kind="contacts_month",
        start=month,
        end=next_month,
        limit=None if cap is None else money(cap),
        source_types=OUTSIDE_AUDIENCE_SOURCES,
    )


def _month_of(now: datetime) -> tuple[datetime, datetime]:
    """The calendar month ``now`` falls in, as ``[start, end)``."""
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month, (month.replace(day=28) + timedelta(days=4)).replace(day=1)
