"""Exact ledger amounts remain authoritative through database reporting."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.usage.domain.accounting import BudgetWindow, MeteringIdentity
from app.modules.usage.infrastructure.allocation_repository import open_allocation
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord
from app.modules.usage.infrastructure.price_catalog import RateCard
from app.modules.usage.infrastructure.repositories import UsageRepository

pytestmark = pytest.mark.e2e


def _record(
    user_id: UUID,
    organization_id: UUID,
    now: datetime,
    model: str,
    exact: Decimal | None,
    legacy: float | None,
) -> UsageRecord:
    return UsageRecord(
        user_id=user_id,
        organization_id=organization_id,
        occurred_at=now,
        source_type="agent_run",
        profile_id="precision",
        profile_scope="SYSTEM",
        model_name=model,
        cost_amount=exact,
        cost_usd=legacy,
    )


@pytest.mark.parametrize("authoritative", [True, False])
async def test_summary_stats_and_windows_sum_exact_cost_before_float_conversion(
    db_manager: DatabaseManager, authoritative: bool
) -> None:
    user_id, organization_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    values = [Decimal("0.1"), Decimal("0.2"), Decimal("0.000000001")] * 100
    expected = float(sum(values, Decimal(0)))
    async with SessionUnitOfWorkFactory(db_manager.session_factory)() as uow:
        uow.session.add_all(
            [
                _record(
                    user_id,
                    organization_id,
                    now,
                    str(index),
                    value if authoritative else None,
                    (None if index % 2 else 999.0) if authoritative else float(value),
                )
                for index, value in enumerate(values)
            ]
        )
        await uow.session.flush()
        repository = UsageRepository(uow)
        summary = await repository.get_usage_summary(
            organization_id=organization_id, start=start, end=end
        )
        assert summary.system_cost_usd == expected
        assert summary.total_by_profile["precision"]["system_cost_usd"] == expected
        stats = await repository.get_usage_stats(
            organization_id=organization_id, start=start, end=end
        )
        assert len(stats) == 1
        assert stats[0]["system_cost_usd"] == expected
        assert (
            await repository.get_system_cost(
                organization_id=organization_id, user_id=user_id, start=start, end=end
            )
            == expected
        )
        windows = await repository.get_system_cost_by_window(
            organization_id=organization_id,
            user_id=user_id,
            window_starts={"week": start, "month": start - timedelta(days=30)},
            end=end,
        )
        assert windows == {"week": expected, "month": expected}


async def test_counter_bootstrap_preserves_exact_ledger_and_legacy_amounts(
    db_manager: DatabaseManager,
) -> None:
    user_id, organization_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    async with SessionUnitOfWorkFactory(db_manager.session_factory)() as uow:
        uow.session.add_all(
            [
                _record(
                    user_id, organization_id, now, "exact", Decimal("0.100000001"), 99.0
                ),
                _record(user_id, organization_id, now, "legacy", None, 0.2),
            ]
        )
        await uow.session.flush()
        await open_allocation(
            uow.session,
            allocation_id=uuid4(),
            identity=MeteringIdentity(
                execution_id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                profile_id="precision",
                profile_scope="SYSTEM",
                model_name="test",
                provider_model_name="test",
            ),
            pricing=RateCard(model="test"),
            windows=[
                BudgetWindow(
                    organization_id=organization_id,
                    user_id=user_id,
                    kind="user_week",
                    start=start,
                    end=end,
                    limit=Decimal("2"),
                )
            ],
            required=Decimal("1"),
            target=Decimal("1"),
            now=now,
            timeout_seconds=120,
        )
        counter = (
            await uow.session.scalars(
                select(UsageLimitCounter).where(UsageLimitCounter.user_id == user_id)
            )
        ).one()
        assert counter.used_usd == Decimal("0.300000001")
        assert counter.reserved_usd == Decimal("1.000000000")


async def test_repository_round_trip_preserves_domain_exact_amount(
    db_manager: DatabaseManager,
) -> None:
    from app.modules.usage.domain.entities import UsageRecord as UsageRecordEntity

    exact = Decimal("123456789012345.123456789")
    entity = UsageRecordEntity(
        user_id=uuid4(),
        profile_id="precision",
        profile_scope="SYSTEM",
        model_name="test",
        cost_amount=exact,
        cost_usd=float(exact),
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        saved = await UsageRepository(uow).create(entity)
        assert saved.cost_amount == exact
    async with factory() as uow:
        stored = await uow.session.get(UsageRecord, saved.id)
        assert stored is not None
        assert stored.cost_amount == exact
        assert stored.to_entity().cost_amount == exact


async def test_exact_last_nanodollar_is_admitted_once_after_historical_spend(
    db_manager: DatabaseManager,
) -> None:
    from app.modules.usage.domain.errors import UsageLimitExceededError

    user_id, organization_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    identity = MeteringIdentity(
        execution_id=uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        profile_id="precision",
        profile_scope="SYSTEM",
        model_name="test",
        provider_model_name="test",
    )
    window = BudgetWindow(
        organization_id=organization_id,
        user_id=user_id,
        kind="user_week",
        start=start,
        end=end,
        limit=Decimal("0.300000002"),
    )
    async with factory() as uow:
        uow.session.add_all(
            [
                _record(user_id, organization_id, now, "first", Decimal("0.1"), 0.1),
                _record(
                    user_id, organization_id, now, "second", Decimal("0.200000001"), 0.2
                ),
            ]
        )
    async with factory() as uow:
        allocation = await open_allocation(
            uow.session,
            allocation_id=uuid4(),
            identity=identity,
            pricing=RateCard(model="test"),
            windows=[window],
            required=Decimal("0.000000001"),
            target=Decimal("0.000000001"),
            now=now,
            timeout_seconds=120,
        )
        assert allocation.amount == Decimal("0.000000001")
    with pytest.raises(UsageLimitExceededError):
        async with factory() as uow:
            await open_allocation(
                uow.session,
                allocation_id=uuid4(),
                identity=identity,
                pricing=RateCard(model="test"),
                windows=[window],
                required=Decimal("0.000000001"),
                target=Decimal("0.000000001"),
                now=now,
                timeout_seconds=120,
            )
    async with factory() as uow:
        counter = (
            await uow.session.scalars(
                select(UsageLimitCounter).where(UsageLimitCounter.user_id == user_id)
            )
        ).one()
        assert counter.used_usd == Decimal("0.300000001")
        assert counter.reserved_usd == Decimal("0.000000001")
