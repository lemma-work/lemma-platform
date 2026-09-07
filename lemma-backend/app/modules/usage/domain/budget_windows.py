"""The dispatch-time windows an allocation participates in."""

from datetime import datetime, timedelta

from app.modules.usage.domain.accounting import BudgetWindow, MeteringIdentity, money
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
        if user is not None and identity.organization_id in excluded:
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
    return windows
