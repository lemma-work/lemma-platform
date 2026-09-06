"""Actual-cost checks preserve receipts through overruns, retries and missing usage."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.usage.config import usage_settings
from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.services.run_usage_recorder import RunUsageRecorder
from app.modules.usage.config import UsageSettings
from app.modules.usage.domain.accounting import (
    AccountingConflictError,
    BudgetWindow,
    MeteringIdentity,
    RequestReceipt,
    TokenCounts,
)
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.infrastructure import request_accounting
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.infrastructure.price_catalog import Rate, RateCard
from app.modules.usage.services.request_accounting_gateway import (
    PostgresRequestAccountingGateway,
)

pytestmark = pytest.mark.e2e


def gateway_for(
    db_manager: DatabaseManager, user_id: UUID
) -> PostgresRequestAccountingGateway:
    return PostgresRequestAccountingGateway(
        SessionUnitOfWorkFactory(db_manager.session_factory),
        MeteringIdentity(
            execution_id=uuid4(),
            user_id=user_id,
            profile_id="system:actual",
            profile_scope="SYSTEM",
            model_name="actual",
            provider_model_name="actual",
        ),
        RateCard(
            model="actual",
            enforceable=True,
            rates={
                "input_mtok": Rate(base=Decimal(1)),
                "output_mtok": Rate(base=Decimal(1)),
            },
        ),
        UsageSettings(),
    )


async def records_for(db_manager: DatabaseManager, user_id: UUID) -> list[UsageRecord]:
    async with db_manager.session_factory() as session:
        return list(
            await session.scalars(
                select(UsageRecord).where(UsageRecord.user_id == user_id)
            )
        )


async def test_crossing_receipt_commits_before_denial_and_replays_exactly_once(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    user_id, request_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    gateway = gateway_for(db_manager, user_id)
    recorder = RunUsageRecorder(SessionUnitOfWorkFactory(db_manager.session_factory))
    profile: dict[str, object | None] = {
        "protocol": "OPENAI_COMPATIBLE",
        "scope": "SYSTEM",
    }
    assert (
        await recorder.reserve(
            organization_id=None, user_id=user_id, runtime_profile=profile
        )
        is None
    )
    await gateway.begin(request_id, now)
    receipt = RequestReceipt(
        request_id=request_id,
        counts=TokenCounts(input_tokens=3, output_tokens=5, request_count=1),
        cost=Decimal("1.100000001"),
        occurred_at=now,
    )
    assert await gateway.record(receipt)
    assert all(await asyncio.gather(*(gateway.record(receipt) for _ in range(4))))
    with pytest.raises(UsageLimitExceededError):
        await gateway.begin(uuid4(), now)
    with pytest.raises(UsageLimitExceededError):
        await recorder.reserve(
            organization_id=None, user_id=user_id, runtime_profile=profile
        )
    records = await records_for(db_manager, user_id)
    assert len(records) == 1
    assert records[0].cost_amount == Decimal("1.100000001")
    assert records[0].input_tokens == 3
    assert records[0].output_tokens == 5
    with pytest.raises(AccountingConflictError):
        await gateway.record(receipt.model_copy(update={"cost": Decimal(".2")}))
    assert (await records_for(db_manager, user_id))[0].cost_amount == Decimal(
        "1.100000001"
    )


async def test_all_inflight_actual_costs_persist_after_another_run_exhausts_budget(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    gateways = [gateway_for(db_manager, user_id) for _ in range(3)]
    requests = [uuid4() for _ in gateways]
    await asyncio.gather(
        *(
            gateway.begin(request, now)
            for gateway, request in zip(gateways, requests, strict=True)
        )
    )
    await asyncio.gather(
        *(
            gateway.record(
                RequestReceipt(
                    request_id=request,
                    counts=TokenCounts(request_count=1),
                    cost=Decimal(".7"),
                    occurred_at=now,
                )
            )
            for gateway, request in zip(gateways, requests, strict=True)
        )
    )
    for gateway in [*gateways, gateway_for(db_manager, user_id)]:
        with pytest.raises(UsageLimitExceededError):
            await gateway.begin(uuid4(), now)
    records = await records_for(db_manager, user_id)
    assert len(records) == 3
    assert sum((record.cost_amount or Decimal(0)) for record in records) == Decimal(
        "2.1"
    )


async def test_current_limit_rechecks_exact_historical_cost_and_legacy_fallback(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 0.300000002)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    async with db_manager.session_factory() as session, session.begin():
        session.add_all(
            [
                UsageRecord(
                    user_id=user_id,
                    source_type="agent_run",
                    profile_id="historical",
                    profile_scope="SYSTEM",
                    model_name="historical",
                    occurred_at=now,
                    cost_amount=exact,
                    cost_usd=legacy,
                )
                for exact, legacy in [(Decimal(".100000001"), 99.0), (None, 0.2)]
            ]
        )
    gateway = gateway_for(db_manager, user_id)
    await gateway.begin(uuid4(), now)
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 0.300000001)
    with pytest.raises(UsageLimitExceededError):
        await gateway.begin(uuid4(), now)
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 0.4)
    await gateway.begin(uuid4(), now)


async def test_missing_provider_usage_is_audited_without_inventing_cost(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    user_id, request_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    gateway = gateway_for(db_manager, user_id)
    await gateway.begin(request_id, now)
    pending = await records_for(db_manager, user_id)
    assert len(pending) == 1
    assert pending[0].cost_amount is None
    receipt = RequestReceipt(
        request_id=request_id,
        counts=TokenCounts(request_count=1, unconfirmed_requests=1),
        cost=None,
        occurred_at=now,
    )
    assert not await gateway.record(receipt)
    assert not await gateway.record(receipt)
    records = await records_for(db_manager, user_id)
    assert len(records) == 1
    assert records[0].cost_amount is None
    assert records[0].record_metadata is not None
    assert records[0].record_metadata["usage"] == receipt.counts.model_dump(mode="json")
    await gateway.begin(uuid4(), now)


async def test_late_receipt_charges_dispatch_week_without_blocking_new_week(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    user_id, request_id = uuid4(), uuid4()
    dispatch_time = datetime(2026, 9, 6, 23, 59, 59, tzinfo=timezone.utc)
    next_week = datetime(2026, 9, 7, 0, 0, 1, tzinfo=timezone.utc)
    gateway = gateway_for(db_manager, user_id)
    await gateway.begin(request_id, dispatch_time)
    receipt = RequestReceipt(
        request_id=request_id,
        counts=TokenCounts(request_count=1),
        cost=Decimal("1.1"),
        occurred_at=dispatch_time,
    )
    assert not await gateway.record(receipt, now=next_week)
    next_request_id = uuid4()
    await gateway.begin(next_request_id, next_week)
    with pytest.raises(UsageLimitExceededError):
        await gateway.begin(uuid4(), dispatch_time)
    records = await records_for(db_manager, user_id)
    assert len(records) == 2
    assert {row.request_id: (row.occurred_at, row.cost_amount) for row in records} == {
        request_id: (dispatch_time, Decimal("1.1")),
        next_request_id: (next_week, None),
    }


async def test_request_identity_cannot_be_replayed_into_another_scope(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    owner_id, other_id, request_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    owner = gateway_for(db_manager, owner_id)
    other = gateway_for(db_manager, other_id)
    await owner.begin(request_id, now)
    receipt = RequestReceipt(
        request_id=request_id,
        counts=TokenCounts(request_count=1),
        cost=Decimal(".2"),
        occurred_at=now,
    )
    with pytest.raises(AccountingConflictError):
        await other.begin(request_id, now)
    with pytest.raises(AccountingConflictError):
        await other.record(receipt)
    assert not await owner.record(receipt)
    assert not await records_for(db_manager, other_id)
    records = await records_for(db_manager, owner_id)
    assert len(records) == 1
    assert records[0].cost_amount == Decimal(".2")


async def test_changing_global_scope_exclusions_rechecks_historical_spend(
    db_manager: DatabaseManager,
) -> None:
    user_id, excluded_org = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    window = BudgetWindow(
        organization_id=None,
        user_id=user_id,
        kind="user_week",
        start=now - timedelta(days=1),
        end=now + timedelta(days=1),
        limit=Decimal("1"),
        excluded_organization_ids=(excluded_org,),
    )
    async with db_manager.session_factory() as session, session.begin():
        session.add_all(
            [
                UsageRecord(
                    user_id=user_id,
                    organization_id=organization_id,
                    source_type="agent_run",
                    profile_id="historical",
                    profile_scope="SYSTEM",
                    model_name="historical",
                    occurred_at=now,
                    cost_amount=cost,
                )
                for organization_id, cost in [
                    (None, Decimal(".2")),
                    (excluded_org, Decimal(".9")),
                ]
            ]
        )
    async with db_manager.session_factory() as session, session.begin():
        await request_accounting.check(session, [window])
    with pytest.raises(UsageLimitExceededError):
        async with db_manager.session_factory() as session, session.begin():
            await request_accounting.check(
                session, [window.model_copy(update={"excluded_organization_ids": ()})]
            )


async def test_existing_ledger_writer_cannot_bypass_a_previously_checked_budget(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 0.1)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    gateway = gateway_for(db_manager, user_id)
    await gateway.begin(uuid4(), now)
    # Compatibility writers still create ordinary ledger rows during rollout.
    async with db_manager.session_factory() as session, session.begin():
        session.add(
            UsageRecord(
                user_id=user_id,
                source_type="agent_run",
                profile_id="historical",
                profile_scope="SYSTEM",
                model_name="historical",
                occurred_at=now,
                cost_amount=Decimal(".2"),
            )
        )
    with pytest.raises(UsageLimitExceededError):
        await gateway.begin(uuid4(), now)
