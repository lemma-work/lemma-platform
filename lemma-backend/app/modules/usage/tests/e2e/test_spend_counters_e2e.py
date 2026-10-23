"""A limit window's counter is its running total, and stays the ledger's total.

Admission reads the counter instead of summing the ledger, so these hold the
counter to the ledger from every direction that could move one without the
other: settlements racing each other, a recount racing a settlement, a ledger
write nothing announced, and policy that moves between admission and
settlement. Real Postgres throughout -- what changed is which statements run
and which locks they take, and a fake cannot disagree with either.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.query_counting import counted_queries, format_statements
from app.modules.usage.config import UsageSettings, usage_settings
from app.modules.usage.domain.accounting import (
    BudgetWindow,
    MeteringIdentity,
    RequestReceipt,
    TokenCounts,
)
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.domain.events import UsageLimitWarningEvent
from app.modules.usage.infrastructure import request_accounting, spend_counters
from app.modules.usage.infrastructure.models import (
    COUNTER_LOCK_ORDER,
    UsageLimitCounter,
    UsageRecord,
)
from app.modules.usage.infrastructure.price_catalog import Rate, RateCard
from app.modules.usage.services.request_accounting_gateway import (
    PostgresRequestAccountingGateway,
)

pytestmark = pytest.mark.e2e

CARD = RateCard(
    model="counted",
    enforceable=True,
    rates={
        "input_mtok": Rate(base=Decimal(1)),
        "output_mtok": Rate(base=Decimal(1)),
    },
)


def _identity(user_id: UUID) -> MeteringIdentity:
    return MeteringIdentity(
        execution_id=uuid4(),
        user_id=user_id,
        profile_id="system:counted",
        profile_scope="SYSTEM",
        model_name="counted",
        provider_model_name="counted",
    )


def _gateway(
    db_manager: DatabaseManager, identity: MeteringIdentity
) -> PostgresRequestAccountingGateway:
    return PostgresRequestAccountingGateway(
        SessionUnitOfWorkFactory(db_manager.session_factory),
        identity,
        CARD,
        UsageSettings(),
    )


def _receipt(request_id: UUID, at: datetime, cost: str) -> RequestReceipt:
    return RequestReceipt(
        request_id=request_id,
        counts=TokenCounts(input_tokens=1, request_count=1),
        cost=Decimal(cost),
        occurred_at=at,
    )


def _week(user_id: UUID, now: datetime, limit: str = "100") -> BudgetWindow:
    return BudgetWindow(
        organization_id=None,
        user_id=user_id,
        kind="user_week",
        start=now - timedelta(days=1),
        end=now + timedelta(days=1),
        limit=Decimal(limit),
    )


async def _weekly_counter(
    db_manager: DatabaseManager, user_id: UUID
) -> UsageLimitCounter:
    async with db_manager.session_factory() as session:
        return (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == user_id,
                    UsageLimitCounter.window_kind == "user_week",
                )
            )
        ).one()


async def _ledger_total(db_manager: DatabaseManager, user_id: UUID) -> Decimal:
    async with db_manager.session_factory() as session:
        rows = await session.scalars(
            select(UsageRecord.cost_amount).where(UsageRecord.user_id == user_id)
        )
        return sum((cost or Decimal(0) for cost in rows), Decimal(0))


async def _write_behind_the_counters(
    db_manager: DatabaseManager, user_id: UUID, at: datetime, cost: str
) -> None:
    """A priced SYSTEM row written in plain SQL, which no counter hears of."""
    async with db_manager.session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO usage_records (id, created_at, updated_at, user_id,"
                " source_type, profile_id, profile_scope, model_name, usage_kind,"
                " input_tokens, output_tokens, units, cost_amount, occurred_at)"
                " VALUES (gen_random_uuid(), now(), now(), :user, 'agent_run',"
                " 'behind', 'SYSTEM', 'behind', 'llm', 0, 0, 0, :cost, :at)"
            ),
            {"user": user_id, "cost": Decimal(cost), "at": at},
        )


async def test_steady_requests_neither_sum_the_ledger_nor_lock_a_counter_to_admit(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 100.0)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    gateway = _gateway(db_manager, _identity(user_id))
    # The first request counts the window from the ledger: nothing has yet.
    first = uuid4()
    await gateway.begin(first, now)
    await gateway.record(_receipt(first, now, "0.5"))

    second = uuid4()
    with counted_queries() as admission:
        await gateway.begin(second, now)
    with counted_queries() as settlement:
        await gateway.record(_receipt(second, now, "0.25"))

    statements = admission + settlement
    assert not [s for s in statements if "sum(" in s.lower()], format_statements(
        statements
    )
    assert not [
        s for s in admission if "usage_limit_counters" in s and "FOR UPDATE" in s
    ], format_statements(admission)
    counter = await _weekly_counter(db_manager, user_id)
    assert counter.used_usd == Decimal("0.75")
    assert counter.used_usd == await _ledger_total(db_manager, user_id)


async def test_concurrent_settlements_keep_the_exact_total(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1000.0)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    gateways = [_gateway(db_manager, _identity(user_id)) for _ in range(8)]
    requests = [uuid4() for _ in gateways]
    costs = [f"0.{index + 1}23456789" for index in range(len(gateways))]
    await asyncio.gather(
        *(
            gateway.begin(request, now)
            for gateway, request in zip(gateways, requests, strict=True)
        )
    )
    await asyncio.gather(
        *(
            gateway.record(_receipt(request, now, cost))
            for gateway, request, cost in zip(gateways, requests, costs, strict=True)
        )
    )

    counter = await _weekly_counter(db_manager, user_id)
    assert counter.used_usd == sum((Decimal(cost) for cost in costs), Decimal(0))
    assert counter.used_usd == await _ledger_total(db_manager, user_id)


async def test_a_recount_keeps_a_charge_settled_while_it_summed(
    db_manager: DatabaseManager,
) -> None:
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    counted = _week(user_id, now)
    # The same counter row asked for under other rules -- an excluded source --
    # so a check under it recounts while a settlement under the first charges.
    recounting = counted.model_copy(update={"excluded_source_types": ("elsewhere",)})
    identity = _identity(user_id)
    async with factory() as uow:
        await request_accounting.check(uow.session, [counted])
    request_id = uuid4()
    async with factory() as uow:
        await request_accounting.journal(uow.session, request_id, identity, CARD, now)
    await _write_behind_the_counters(db_manager, user_id, now, "0.5")

    async with factory() as settling:
        # Charged and holding the counter's row lock, not yet committed.
        await request_accounting.record(
            settling.session,
            _receipt(request_id, now, "0.25"),
            identity,
            [counted],
            [counted],
            [],
            Decimal("0.8"),
        )

        async def recount() -> None:
            async with factory() as uow:
                await request_accounting.check(uow.session, [recounting])

        recount_task = asyncio.create_task(recount())

        async def waiting_on_the_counter() -> int:
            async with db_manager.session_factory() as session:
                return int(
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity"
                            " WHERE wait_event_type = 'Lock'"
                            " AND query LIKE 'UPDATE usage_limit_counters%'"
                        )
                    )
                    or 0
                )

        # Its sum is taken, without the settlement; its write waits for it.
        await eventually(
            label="the recount waiting on the settlement's lock",
            probe=waiting_on_the_counter,
            done=lambda waiting: waiting == 1,
        )
    await recount_task

    counter = await _weekly_counter(db_manager, user_id)
    assert counter.used_usd == Decimal("0.75")
    assert counter.used_usd == await _ledger_total(db_manager, user_id)
    assert counter.basis == recounting.basis


async def test_a_ledger_write_nothing_announced_is_counted_once_the_total_is_due(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    monkeypatch.setattr(usage_settings, "usage_counter_refresh_seconds", 60)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    gateway = _gateway(db_manager, _identity(user_id))
    request_id = uuid4()
    await gateway.begin(request_id, now)
    await gateway.record(_receipt(request_id, now, "0.2"))
    await _write_behind_the_counters(db_manager, user_id, now, "0.9")
    async with db_manager.session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE usage_limit_counters SET reconciled_at = now() - interval '2 minutes'"
                " WHERE user_id = :user"
            ),
            {"user": user_id},
        )

    with pytest.raises(UsageLimitExceededError):
        await gateway.begin(uuid4(), now)
    counter = await _weekly_counter(db_manager, user_id)
    assert counter.used_usd == Decimal("1.1")


async def test_a_settlement_under_other_rules_leaves_the_total_for_a_recount(
    db_manager: DatabaseManager,
) -> None:
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    admitted_under = _week(user_id, now)
    moved_to = admitted_under.model_copy(
        update={"excluded_source_types": ("elsewhere",)}
    )
    identity = _identity(user_id)
    request_id = uuid4()
    async with factory() as uow:
        await request_accounting.begin(
            uow.session, request_id, identity, CARD, [admitted_under], now
        )
    # Policy moves while the request is out: the next check counts the window
    # under the new rules before this receipt comes back.
    async with factory() as uow:
        await request_accounting.check(uow.session, [moved_to])
    async with factory() as uow:
        await request_accounting.record(
            uow.session,
            _receipt(request_id, now, "0.3"),
            identity,
            [admitted_under],
            [admitted_under],
            [],
            Decimal("0.8"),
        )

    assert (await _weekly_counter(db_manager, user_id)).basis is None
    async with factory() as uow:
        await request_accounting.check(uow.session, [moved_to])
    counter = await _weekly_counter(db_manager, user_id)
    assert counter.basis == moved_to.basis
    assert counter.used_usd == Decimal("0.3")


async def test_a_refused_admission_keeps_the_recount_it_made(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 0.1)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    gateway = _gateway(db_manager, _identity(user_id))
    await gateway.begin(uuid4(), now)
    # An ordinary ORM insert, as the aggregate run recorder makes, has the
    # counter recounted before anything trusts it again.
    async with db_manager.session_factory() as session, session.begin():
        session.add(
            UsageRecord(
                user_id=user_id,
                source_type="agent_run",
                profile_id="aggregate",
                profile_scope="SYSTEM",
                model_name="aggregate",
                occurred_at=now,
                cost_amount=Decimal("0.2"),
            )
        )
    assert (await _weekly_counter(db_manager, user_id)).basis is None

    with pytest.raises(UsageLimitExceededError):
        await gateway.begin(uuid4(), now)
    with counted_queries() as statements, pytest.raises(UsageLimitExceededError):
        await gateway.begin(uuid4(), now)

    assert not [s for s in statements if "sum(" in s.lower()], format_statements(
        statements
    )
    counter = await _weekly_counter(db_manager, user_id)
    assert counter.used_usd == Decimal("0.2")
    assert counter.basis is not None


async def test_a_limit_lifted_and_restored_counts_what_was_spent_meanwhile(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Long enough that only the counter, never a refresh, can know the spend.
    monkeypatch.setattr(usage_settings, "usage_counter_refresh_seconds", 3600)
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    limited = _gateway(db_manager, _identity(user_id))
    request_id = uuid4()
    await limited.begin(request_id, now)
    await limited.record(_receipt(request_id, now, "0.2"))

    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", None)
    unlimited = _gateway(db_manager, _identity(user_id))
    request_id = uuid4()
    assert await unlimited.begin(request_id, now) is False
    await unlimited.record(_receipt(request_id, now, "0.9"))

    # The same limit back: the counter is trusted as it stands, so it must
    # have counted the request made while there was none.
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    with pytest.raises(UsageLimitExceededError):
        await _gateway(db_manager, _identity(user_id)).begin(uuid4(), now)
    counter = await _weekly_counter(db_manager, user_id)
    assert counter.used_usd == Decimal("1.1")
    assert counter.used_usd == await _ledger_total(db_manager, user_id)


async def test_an_insert_marking_a_counter_never_lands_inside_a_recount(
    db_manager: DatabaseManager,
) -> None:
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    counted = _week(user_id, now)
    recounting = counted.model_copy(update={"excluded_source_types": ("elsewhere",)})
    async with factory() as uow:
        await request_accounting.check(uow.session, [counted])

    async with db_manager.session_factory() as inserting, inserting.begin():
        # The aggregate recorder's row, inserted and its counter marked, not
        # yet committed.
        inserting.add(
            UsageRecord(
                user_id=user_id,
                source_type="agent_run",
                profile_id="aggregate",
                profile_scope="SYSTEM",
                model_name="aggregate",
                occurred_at=now,
                cost_amount=Decimal("0.4"),
            )
        )
        await inserting.flush()

        async def recount() -> None:
            async with factory() as uow:
                await request_accounting.check(uow.session, [recounting])

        recount_task = asyncio.create_task(recount())

        async def recount_waiting() -> int:
            async with db_manager.session_factory() as session:
                return int(
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity"
                            " WHERE wait_event_type = 'Lock'"
                            " AND (query LIKE 'UPDATE usage_limit_counters%'"
                            " OR query LIKE 'SELECT pg_advisory_xact_lock%')"
                        )
                    )
                    or 0
                )

        # Without the insert's recount lock, the recount sums now -- without
        # the row -- and its write, waiting on the mark's row lock, would
        # set the counter back to trusted over it once the insert commits.
        await eventually(
            label="the recount waiting on the insert",
            probe=recount_waiting,
            done=lambda waiting: waiting == 1,
        )
    await recount_task

    async with factory() as uow:
        await request_accounting.check(uow.session, [recounting])
    counter = await _weekly_counter(db_manager, user_id)
    assert counter.used_usd == Decimal("0.4")
    assert counter.used_usd == await _ledger_total(db_manager, user_id)


async def test_warning_is_sent_once_per_limit_and_again_after_the_limit_changes(
    db_manager: DatabaseManager,
) -> None:
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    identity = _identity(user_id)

    async def spend(window: BudgetWindow, cost: str) -> list[UsageLimitWarningEvent]:
        events: list[DomainEvent] = []
        request_id = uuid4()
        async with factory() as uow:
            await request_accounting.begin(
                uow.session, request_id, identity, CARD, [window], now
            )
        async with factory() as uow:
            await request_accounting.record(
                uow.session,
                _receipt(request_id, now, cost),
                identity,
                [window],
                [window],
                events,
                Decimal("0.8"),
            )
        return [event for event in events if isinstance(event, UsageLimitWarningEvent)]

    ten = _week(user_id, now, "10")
    assert await spend(ten, "7") == []
    [crossed] = await spend(ten, "1.5")
    assert (crossed.used_usd, crossed.limit_usd) == (8.5, 10.0)
    assert await spend(ten, "0.5") == []
    # A new limit is a new threshold to warn about.
    [again] = await spend(ten.model_copy(update={"limit": Decimal("11")}), "0.1")
    assert (again.used_usd, again.limit_usd) == (9.1, 11.0)


async def test_counter_rows_lock_in_the_same_order_from_python_and_sql(
    db_manager: DatabaseManager,
) -> None:
    now = datetime.now(timezone.utc)
    organizations, members = [uuid4(), uuid4()], [uuid4(), uuid4()]
    windows = [
        BudgetWindow(
            organization_id=organization,
            user_id=member,
            kind=kind,
            start=now - timedelta(days=days),
            end=now + timedelta(days=1),
            limit=Decimal(1),
        )
        for kind in ("user_week", "org_month", "user_month", "contacts_month")
        for days in (1, 3)
        for organization, member in (
            (organizations[0], None),
            (organizations[1], None),
            (None, members[0]),
            (organizations[0], members[1]),
            (organizations[1], members[1]),
        )
    ]
    async with SessionUnitOfWorkFactory(db_manager.session_factory)() as uow:
        found = await spend_counters.counters(uow.session, windows)
        ids = [counter.id for counter in found.values()]
        in_sql = list(
            await uow.session.scalars(
                select(UsageLimitCounter.id)
                .where(UsageLimitCounter.id.in_(ids))
                .order_by(*COUNTER_LOCK_ORDER)
            )
        )

    by_window = {(w.kind, w.start, w.organization_id, w.user_id): w for w in windows}
    in_python = [
        counter.id
        for counter in sorted(
            found.values(),
            key=lambda counter: spend_counters.lock_order(
                by_window[
                    (
                        counter.window_kind,
                        counter.window_start,
                        counter.organization_id,
                        counter.user_id,
                    )
                ]
            ),
        )
    ]
    assert in_sql == in_python
