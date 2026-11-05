"""The limits a member's work is held to, and whether a run may start under them.

Every check that admits work resolves its limits here: each provider request
(``PostgresRequestAccountingGateway``), a run's start (``check_run_budget``),
the message that starts it (the agent's preflight), and the surface that asks
whether a contact may be answered at all (``contacts_cap_spent``). So a run is
never refused at the door by a limit its requests would not be held to, nor
let in past one they would.
"""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.usage.config import UsageSettings
from app.modules.usage.domain.accounting import BudgetWindow
from app.modules.usage.domain.budget_windows import contacts_window, spend_windows
from app.modules.usage.domain.ports import (
    UsageLimitPort,
    UsageLimitValues,
    normalize_limit_values,
)
from app.modules.usage.infrastructure import spend_counters
from app.modules.usage.services.contacts_cap import applicable_contacts_cap


async def run_limits(
    uow: SqlAlchemyUnitOfWork,
    port: UsageLimitPort | None,
    *,
    organization_id: UUID | None,
    user_id: UUID,
    outside_audience: bool,
    settings: UsageSettings | None = None,
) -> UsageLimitValues:
    """What ``port`` limits, plus the contacts cap for a run answering outsiders.

    The cap is the organization's own where an owner set one, the deployment's
    default where nobody did. It is read only for a run that answers people
    outside the organization: no other run is held to it, so no other run
    pays for the read.
    """
    limits = (
        normalize_limit_values(
            await port.resolve_limits(organization_id=organization_id, user_id=user_id)
        )
        if port is not None
        else UsageLimitValues()
    )
    if organization_id is None or not outside_audience:
        return limits
    return await _with_contacts_cap(uow, limits, organization_id, settings=settings)


async def _with_contacts_cap(
    uow: SqlAlchemyUnitOfWork,
    limits: UsageLimitValues,
    organization_id: UUID,
    *,
    settings: UsageSettings | None = None,
) -> UsageLimitValues:
    """``limits`` with the organization's contacts cap in them."""
    cap = await applicable_contacts_cap(uow, organization_id, settings=settings)
    return replace(
        limits,
        contacts_monthly_limit_usd=(
            None if cap.limit_usd is None else float(cap.limit_usd)
        ),
    )


async def spent_windows(
    uow: SqlAlchemyUnitOfWork,
    port: UsageLimitPort | None,
    *,
    organization_id: UUID | None,
    user_id: UUID,
    outside_audience: bool,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """The kinds of limit window a run starting now would be refused under.

    Empty when it may start. These are the windows each of the run's provider
    requests is admitted under (``spend_windows``), compared on their running
    totals (``spend_counters``) rather than summed from the ledger: asking
    costs one read of the counters, however long the member's month has been.
    A counter that cannot be trusted is recounted first, in ``uow``, so what
    the recount learned commits with it -- and is lost with it, if the caller
    refuses by rolling back.
    """
    limits = await run_limits(
        uow,
        port,
        organization_id=organization_id,
        user_id=user_id,
        outside_audience=outside_audience,
    )
    return await _spent(
        uow,
        spend_windows(
            organization_id=organization_id,
            user_id=user_id,
            outside_audience=outside_audience,
            limits=limits,
            now=now or datetime.now(timezone.utc),
        ),
    )


async def contacts_cap_spent(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Whether the organization has spent its contacts cap this month.

    Decided as each request of a run answering people outside it is: on the
    ``contacts_month`` window, with the cap ``run_limits`` gives that run, and
    on the window's running total. A cap nobody set is the deployment's
    default; a cap an owner removed limits nothing, and no counter is read
    for it.

    A counter that cannot be trusted is recounted first, in ``uow``: the
    recount holds the counter's row and its recount lock until ``uow`` ends,
    and is lost if ``uow`` rolls back. So ask it in a unit of work that does
    nothing else and commits.
    """
    limits = await _with_contacts_cap(uow, UsageLimitValues(), organization_id)
    window = contacts_window(organization_id, limits, now or datetime.now(timezone.utc))
    return bool(await _spent(uow, [window]))


async def _spent(
    uow: SqlAlchemyUnitOfWork, windows: list[BudgetWindow]
) -> tuple[str, ...]:
    """The kinds of the limited ``windows`` whose running total has reached
    the limit. A window with no limit keeps no counter, and none is made for
    it here."""
    limited = [window for window in windows if window.limit is not None]
    found = await spend_counters.counters(uow.session, limited)
    return tuple(
        window.kind for window in limited if spend_counters.exhausted(found, [window])
    )
