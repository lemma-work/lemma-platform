"""Real transactions prove authority cannot be duplicated or refunded on expiry."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.manager import DatabaseManager
from app.modules.usage.domain.accounting import (
    AccountingConflictError,
    Allocation,
    AllocationState,
    BudgetWindow,
    MeteringIdentity,
    TokenCounts,
    UsageBatch,
)
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.infrastructure.allocation_repository import (
    checkpoint,
    mark_expired_uncertain,
    open_allocation,
)
from app.modules.usage.infrastructure.allocation_models import UsageAllocation
from app.modules.usage.infrastructure.models import (
    UsageLimitCounter,
    UsageRecord,
)
from app.modules.usage.infrastructure.price_catalog import RateCard

pytestmark = pytest.mark.e2e


def context() -> tuple[datetime, MeteringIdentity, BudgetWindow]:
    now = datetime.now(timezone.utc)
    identity = MeteringIdentity(
        execution_id=uuid4(),
        user_id=uuid4(),
        profile_id="system:test",
        profile_scope="SYSTEM",
        model_name="test",
        provider_model_name="test",
    )
    window = BudgetWindow(
        organization_id=None,
        user_id=identity.user_id,
        kind="user_weekly",
        start=now - timedelta(days=1),
        end=now + timedelta(days=1),
        limit=Decimal("1"),
    )
    return now, identity, window


async def test_concurrent_allocations_cannot_share_last_budget(
    db_manager: DatabaseManager,
) -> None:
    now, identity, window = context()

    async def admit() -> Allocation | None:
        try:
            async with db_manager.session_factory() as session, session.begin():
                return await open_allocation(
                    session,
                    allocation_id=uuid4(),
                    identity=identity,
                    pricing=RateCard(model="test"),
                    windows=[window],
                    required=Decimal(".2"),
                    target=Decimal(".2"),
                    now=now,
                    timeout_seconds=120,
                )
        except UsageLimitExceededError:
            return None

    results = await asyncio.gather(*(admit() for _ in range(20)))
    assert sum(result is not None for result in results) == 5
    async with db_manager.session_factory() as session:
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == identity.user_id
                )
            )
        ).one()
        assert counter.reserved_usd == Decimal("1")
        assert counter.used_usd == 0


async def test_expiry_preserves_authority_and_late_receipt_is_idempotent(
    db_manager: DatabaseManager,
) -> None:
    now, identity, window = context()
    async with db_manager.session_factory() as session, session.begin():
        allocation = await open_allocation(
            session,
            allocation_id=uuid4(),
            identity=identity,
            pricing=RateCard(model="test"),
            windows=[window],
            required=Decimal(".2"),
            target=Decimal("1"),
            now=now,
            timeout_seconds=120,
        )
    async with db_manager.session_factory() as session, session.begin():
        assert await mark_expired_uncertain(session, now + timedelta(minutes=3)) == 1
    async with db_manager.session_factory() as session:
        row = await session.get(UsageAllocation, allocation.id)
        assert row is not None
        assert row.state == AllocationState.UNCERTAIN
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == identity.user_id
                )
            )
        ).one()
        assert counter.reserved_usd == Decimal("1")
    batch = UsageBatch(
        allocation_id=allocation.id,
        sequence=1,
        counts=TokenCounts(input_tokens=100, request_count=1),
        cost=Decimal(".1"),
        uncertain=Decimal(".2"),
        occurred_at=now,
        close=True,
    )

    async def settle() -> None:
        async with db_manager.session_factory() as session, session.begin():
            await checkpoint(
                session, batch, now=now + timedelta(minutes=4), timeout_seconds=120
            )

    await asyncio.gather(*(settle() for _ in range(5)))
    async with db_manager.session_factory() as session:
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == identity.user_id
                )
            )
        ).one()
        assert counter.used_usd == Decimal(".1")
        assert counter.reserved_usd == Decimal(".2")
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.allocation_id == allocation.id)
            )
            == 1
        )
    with pytest.raises(AccountingConflictError):
        async with db_manager.session_factory() as session, session.begin():
            await checkpoint(
                session,
                batch.model_copy(update={"cost": Decimal(".2")}),
                now=now,
                timeout_seconds=120,
            )


async def test_real_model_wrapper_batches_receipts_and_closes_on_failure(
    db_manager: DatabaseManager,
) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.usage.infrastructure.metered_model import MeteredModel
    from app.modules.usage.services.metering_scope import metering_execution
    from app.modules.usage.services.usage_context import UsageExecutionContext
    from app.modules.usage.services.usage_service import ModelPricing, UsageService

    user_id = uuid4()
    calls = 0

    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 13:
            raise ConnectionError("response lost")
        return ModelResponse(
            parts=[TextPart("ok")],
            usage=RequestUsage(input_tokens=100, output_tokens=10),
        )

    profile = {
        "profile_id": "system:test",
        "scope": "SYSTEM",
        "model_name": "allocation-e2e",
        "provider_model_name": "allocation-e2e",
        "model_metadata": {"context_window": 1000},
    }
    UsageService.register_model_pricing({"allocation-e2e": ModelPricing(1, 2)})
    try:
        model = MeteredModel(FunctionModel(provider), profile)
        with pytest.raises(ConnectionError):
            async with metering_execution(
                UsageExecutionContext(
                    user_id=user_id, organization_id=None, pod_id=None
                ),
                factory=SessionUnitOfWorkFactory(db_manager.session_factory),
            ):
                for _ in range(13):
                    await model.request([], None, ModelRequestParameters())
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop("allocation-e2e", None)
    async with db_manager.session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(UsageRecord)
                    .where(UsageRecord.user_id == user_id)
                    .order_by(UsageRecord.batch_sequence)
                )
            ).all()
        )
        assert len(rows) == 2
        assert [row.input_tokens for row in rows] == [1000, 200]
        total = Decimal(0)
        for row in rows:
            assert row.cost_amount is not None
            total += row.cost_amount
        assert total == Decimal(".00144")
        assert all(row.cost_source == "REGISTERED" for row in rows)


async def test_accounting_migration_round_trip_preserves_legacy_usage(
    db_manager: DatabaseManager,
) -> None:
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import Numeric, inspect

    migration = import_module("migrations.versions.2026-09-06_usage_allocations_0030")
    now, identity, window = context()
    async with db_manager.session_factory() as session, session.begin():
        session.add(
            UsageRecord(
                user_id=identity.user_id,
                profile_id="legacy",
                profile_scope="SYSTEM",
                model_name="legacy",
                source_type="agent_run",
                cost_usd=0.125,
                occurred_at=now,
            )
        )
    async with db_manager.engine.begin() as connection:

        def round_trip(sync_connection: Connection) -> None:
            with Operations.context(MigrationContext.configure(sync_connection)):
                migration.downgrade()
                migration.upgrade()
            columns = {
                column["name"]: column
                for column in inspect(sync_connection).get_columns(
                    "usage_limit_counters"
                )
            }
            assert isinstance(columns["used_usd"]["type"], Numeric)
            assert "usage_allocations" in inspect(sync_connection).get_table_names()

        await connection.run_sync(round_trip)
    async with db_manager.session_factory() as session:
        record = (
            await session.scalars(
                select(UsageRecord).where(UsageRecord.user_id == identity.user_id)
            )
        ).one()
        assert record.cost_usd == 0.125
        assert record.cost_source == "LEGACY"
        assert record.cached_input_tokens is None
        assert record.allocation_id is None


async def test_warning_and_model_events_are_emitted_once_with_the_receipt(
    db_manager: DatabaseManager,
) -> None:
    from app.modules.usage.domain.events import ModelUsageEvent, UsageLimitWarningEvent

    now, identity, window = context()
    async with db_manager.session_factory() as session, session.begin():
        allocation = await open_allocation(
            session,
            allocation_id=uuid4(),
            identity=identity,
            pricing=RateCard(model="test"),
            windows=[window],
            required=Decimal("1"),
            target=Decimal("1"),
            now=now,
            timeout_seconds=120,
        )
    batch = UsageBatch(
        allocation_id=allocation.id,
        sequence=1,
        counts=TokenCounts(request_count=1),
        cost=Decimal(".8"),
        occurred_at=now,
    )
    events: list[DomainEvent] = []
    async with db_manager.session_factory() as session, session.begin():
        await checkpoint(session, batch, now=now, timeout_seconds=120, events=events)
    assert len([event for event in events if isinstance(event, ModelUsageEvent)]) == 1
    assert (
        len([event for event in events if isinstance(event, UsageLimitWarningEvent)])
        == 1
    )
    replay_events: list[DomainEvent] = []
    async with db_manager.session_factory() as session, session.begin():
        await checkpoint(
            session, batch, now=now, timeout_seconds=120, events=replay_events
        )
    assert replay_events == []


@pytest.mark.parametrize(
    "priced,activate_after_start", [(True, False), (False, False), (True, True)]
)
async def test_limit_is_enforced_before_an_ongoing_request_dispatch(
    db_manager: DatabaseManager, priced: bool, activate_after_start: bool
) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.usage.config import UsageSettings
    from app.modules.usage.domain.ports import UsageLimitValues
    from app.modules.usage.infrastructure.metered_model import MeteredModel
    from app.modules.usage.services.metering_scope import metering_execution
    from app.modules.usage.services.usage_context import UsageExecutionContext
    from app.modules.usage.services.usage_limit_provider import (
        configure_usage_limit_provider,
    )
    from app.modules.usage.services.usage_service import ModelPricing, UsageService

    class Limits:
        async def resolve_limits(
            self, *, organization_id: UUID | None, user_id: UUID
        ) -> UsageLimitValues:
            return UsageLimitValues(
                user_weekly_limit_usd=1.1 if not activate_after_start or calls else None
            )

    calls = 0

    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[TextPart("ok")], usage=RequestUsage(input_tokens=900)
        )

    model_name = "ongoing-allocation-e2e"
    if priced:
        UsageService.register_model_pricing({model_name: ModelPricing(1000, 0)})
    configure_usage_limit_provider(lambda _: Limits())
    try:
        model = MeteredModel(
            FunctionModel(provider),
            {
                "profile_id": "system:test",
                "scope": "SYSTEM",
                "model_name": model_name,
                "model_metadata": {"context_window": 1000},
            },
        )
        with pytest.raises(UsageLimitExceededError):
            async with metering_execution(
                UsageExecutionContext(
                    user_id=uuid4(), organization_id=None, pod_id=None
                ),
                factory=SessionUnitOfWorkFactory(db_manager.session_factory),
                settings=UsageSettings(usage_batch_requests=1),
            ):
                await model.request([], None, ModelRequestParameters())
                await model.request([], None, ModelRequestParameters())
        assert calls == (1 if priced else 0)
    finally:
        configure_usage_limit_provider(None)
        UsageService._SYSTEM_MODEL_PRICING.pop(model_name, None)


async def test_cancelled_stream_does_not_refund_unconfirmed_provider_usage(
    db_manager: DatabaseManager,
) -> None:
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.function import FunctionModel

    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.usage.domain.ports import UsageLimitValues
    from app.modules.usage.infrastructure.metered_model import MeteredModel
    from app.modules.usage.services.metering_scope import metering_execution
    from app.modules.usage.services.usage_context import UsageExecutionContext
    from app.modules.usage.services.usage_limit_provider import (
        configure_usage_limit_provider,
    )
    from app.modules.usage.services.usage_service import ModelPricing, UsageService

    class Limits:
        async def resolve_limits(
            self, *, organization_id: UUID | None, user_id: UUID
        ) -> UsageLimitValues:
            return UsageLimitValues(user_weekly_limit_usd=1)

    async def provider(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "partial answer"
        yield "not consumed"

    user_id = uuid4()
    name = "cancelled-allocation-e2e"
    UsageService.register_model_pricing({name: ModelPricing(1000, 0)})
    configure_usage_limit_provider(lambda _: Limits())
    try:
        model = MeteredModel(
            FunctionModel(stream_function=provider),
            {
                "profile_id": "system:test",
                "scope": "SYSTEM",
                "model_name": name,
                "model_metadata": {"context_window": 1000},
            },
        )
        with pytest.raises(asyncio.CancelledError):
            async with metering_execution(
                UsageExecutionContext(
                    user_id=user_id, organization_id=None, pod_id=None
                ),
                factory=SessionUnitOfWorkFactory(db_manager.session_factory),
            ):
                async with model.request_stream(
                    [], None, ModelRequestParameters()
                ) as stream:
                    async for _ in stream:
                        raise asyncio.CancelledError()
        async with db_manager.session_factory() as session:
            counter = (
                await session.scalars(
                    select(UsageLimitCounter).where(
                        UsageLimitCounter.user_id == user_id,
                        UsageLimitCounter.window_kind == "user_week",
                    )
                )
            ).one()
            assert counter.reserved_usd == Decimal("1")
            assert counter.used_usd == 0
            record = (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.user_id == user_id)
                )
            ).one()
            assert record.record_metadata is not None
            assert record.record_metadata["uncertain_usd"] == "1.000000000"
    finally:
        configure_usage_limit_provider(None)
        UsageService._SYSTEM_MODEL_PRICING.pop(name, None)
