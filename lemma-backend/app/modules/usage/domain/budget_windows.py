"""The dispatch-time windows an allocation participates in."""

from datetime import datetime, timedelta

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
    if identity.profile_scope != "SYSTEM":
        return []
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    user_org = (
        identity.organization_id if limits.user_limit_scope == "organization" else None
    )
    excluded = limits.excluded_organization_ids if user_org is None else ()
    outside_audience = identity.source_type in OUTSIDE_AUDIENCE_SOURCES
    windows = []
    for org, user, kind, start, end, limit in (
        (
            identity.organization_id,
            None,
            "org_month",
            month,
            next_month,
            limits.org_monthly_limit_usd,
        ),
        (
            user_org,
            identity.user_id,
            "user_week",
            week,
            week + timedelta(days=7),
            limits.user_weekly_limit_usd,
        ),
        (
            user_org,
            identity.user_id,
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
        if user is not None and (
            outside_audience or identity.organization_id in excluded
        ):
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
            )
        )
    if outside_audience:
        windows.extend(_contacts_window(identity, limits, month, next_month))
    return windows


def _contacts_window(
    identity: MeteringIdentity,
    limits: UsageLimitValues,
    month: datetime,
    next_month: datetime,
) -> list[BudgetWindow]:
    """The organization's contacts cap, counting only runs for people outside."""
    if identity.organization_id is None:
        return []
    cap = limits.contacts_monthly_limit_usd
    return [
        BudgetWindow(
            organization_id=identity.organization_id,
            user_id=None,
            kind="contacts_month",
            start=month,
            end=next_month,
            limit=None if cap is None else money(cap),
            source_types=OUTSIDE_AUDIENCE_SOURCES,
        )
    ]
