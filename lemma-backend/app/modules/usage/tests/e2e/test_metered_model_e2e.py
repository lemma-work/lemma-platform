"""Provider streaming and settings cannot refund or bypass dispatch authority."""

from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import httpx2
from openai import AsyncOpenAI
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ModelResponseState,
    TextPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from sqlalchemy import select

from app.core.config import settings
from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.domain.accounting import (
    AccountingConflictError,
    BudgetWindow,
    MeteringIdentity,
    TokenCounts,
    UsageBatch,
)
from app.modules.usage.infrastructure.allocation_repository import (
    checkpoint,
    open_allocation,
)
from app.modules.usage.infrastructure.price_catalog import RateCard
from app.modules.usage.infrastructure.metered_model import MeteredModel
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord
from app.modules.usage.services.metering_scope import metering_execution
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.services.usage_service import ModelPricing, UsageService

pytestmark = pytest.mark.e2e


@pytest.fixture
def bounded_model_name(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"dispatch-bound-{uuid4()}"
    monkeypatch.setattr(settings, "usage_user_weekly_limit_usd", 1.0)
    UsageService.register_model_pricing({name: ModelPricing(1000, 0)})
    try:
        yield name
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop(name, None)


async def test_early_stream_exit_retains_unconfirmed_authority(
    db_manager: DatabaseManager, bounded_model_name: str
) -> None:
    async def provider(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "partial answer"
        yield "the provider has more output"

    user_id = uuid4()
    model = MeteredModel(
        FunctionModel(stream_function=provider),
        {
            "profile_id": "system:test",
            "scope": "SYSTEM",
            "model_name": bounded_model_name,
            "model_metadata": {"context_window": 1000},
        },
    )
    async with metering_execution(
        UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None),
        factory=SessionUnitOfWorkFactory(db_manager.session_factory),
    ):
        async with model.request_stream([], None, ModelRequestParameters()) as stream:
            async for _event in stream:
                break

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
        receipt = (
            await session.scalars(
                select(UsageRecord).where(UsageRecord.user_id == user_id)
            )
        ).one()
        assert receipt.record_metadata is not None
        assert receipt.record_metadata["uncertain_usd"] == "1.000000000"


@pytest.mark.parametrize(
    "settings_as_default", [False, True], ids=["explicit-settings", "model-defaults"]
)
@pytest.mark.parametrize(
    "model_settings",
    [
        ModelSettings(max_tokens=8192, extra_body={"max_completion_tokens": 128000}),
        ModelSettings(service_tier="priority"),
        OpenAIChatModelSettings(openai_service_tier="priority"),
        AnthropicModelSettings(anthropic_speed="fast"),
    ],
    ids=["body-output-override", "priority-tier", "openai-priority", "anthropic-fast"],
)
async def test_unbounded_provider_settings_are_refused_before_dispatch(
    db_manager: DatabaseManager,
    bounded_model_name: str,
    model_settings: ModelSettings,
    settings_as_default: bool,
) -> None:
    dispatched = False

    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal dispatched
        dispatched = True
        return ModelResponse(
            parts=[TextPart("ok")], usage=RequestUsage(input_tokens=10)
        )

    model = MeteredModel(
        FunctionModel(
            provider, settings=model_settings if settings_as_default else None
        ),
        {
            "profile_id": "system:test",
            "scope": "SYSTEM",
            "model_name": bounded_model_name,
            "model_metadata": {"context_window": 1000},
        },
    )
    with pytest.raises(UsageLimitExceededError):
        async with metering_execution(
            UsageExecutionContext(user_id=uuid4(), organization_id=None, pod_id=None),
            factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        ):
            await model.request(
                [],
                None if settings_as_default else model_settings,
                ModelRequestParameters(),
            )
    assert not dispatched


async def test_standard_provider_settings_record_confirmed_usage(
    db_manager: DatabaseManager, bounded_model_name: str
) -> None:
    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart("ok")], usage=RequestUsage(input_tokens=10)
        )

    user_id = uuid4()
    model = MeteredModel(
        FunctionModel(provider),
        {
            "profile_id": "system:test",
            "scope": "SYSTEM",
            "model_name": bounded_model_name,
            "model_metadata": {"context_window": 1000},
        },
    )
    async with metering_execution(
        UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None),
        factory=SessionUnitOfWorkFactory(db_manager.session_factory),
    ):
        response = await model.request(
            [],
            ModelSettings(max_tokens=128, temperature=0.1),
            ModelRequestParameters(),
        )
        assert response.parts == [TextPart("ok")]
    async with db_manager.session_factory() as session:
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == user_id,
                    UsageLimitCounter.window_kind == "user_week",
                )
            )
        ).one()
        assert counter.used_usd == Decimal("0.01")
        assert counter.reserved_usd == 0


@pytest.mark.parametrize("response_state", ["incomplete", "interrupted", "suspended"])
async def test_noncomplete_response_retains_authority_until_usage_is_final(
    db_manager: DatabaseManager,
    bounded_model_name: str,
    response_state: ModelResponseState,
) -> None:
    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Background/suspended responses may continue spending after this receipt.
        return ModelResponse(
            parts=[TextPart("pending")],
            usage=RequestUsage(input_tokens=10),
            state=response_state,
        )

    user_id = uuid4()
    model = MeteredModel(
        FunctionModel(provider),
        {
            "profile_id": "system:test",
            "scope": "SYSTEM",
            "model_name": bounded_model_name,
            "model_metadata": {"context_window": 1000},
        },
    )
    async with metering_execution(
        UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None),
        factory=SessionUnitOfWorkFactory(db_manager.session_factory),
    ):
        await model.request([], None, ModelRequestParameters())
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


async def test_confirmed_overage_counts_toward_subsequent_admission(
    db_manager: DatabaseManager,
    bounded_model_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "usage_user_weekly_limit_usd", 2.0)
    dispatched = 0

    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal dispatched
        dispatched += 1
        # The provider violates the configured context ceiling but reports usage.
        return ModelResponse(
            parts=[TextPart("done")], usage=RequestUsage(input_tokens=1500)
        )

    user_id = uuid4()
    context = UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None)
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    model = MeteredModel(
        FunctionModel(provider),
        {
            "profile_id": "system:test",
            "scope": "SYSTEM",
            "model_name": bounded_model_name,
            "model_metadata": {"context_window": 1000},
        },
    )
    with pytest.raises(AccountingConflictError):
        async with metering_execution(context, factory=factory):
            await model.request([], None, ModelRequestParameters())

    with pytest.raises(UsageLimitExceededError):
        async with metering_execution(context, factory=factory):
            await model.request([], None, ModelRequestParameters())
    assert dispatched == 1

    async with db_manager.session_factory() as session:
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == user_id,
                    UsageLimitCounter.window_kind == "user_week",
                )
            )
        ).one()
        assert counter.used_usd == Decimal("1.5")
        assert counter.reserved_usd == 0
        receipt = (
            await session.scalars(
                select(UsageRecord).where(UsageRecord.user_id == user_id)
            )
        ).one()
        assert receipt.cost_amount == Decimal("1.5")
        assert receipt.input_tokens == 1500


async def test_late_inflight_uncertainty_survives_exhausted_overage_allocation(
    db_manager: DatabaseManager,
) -> None:
    now = datetime.now(timezone.utc)
    user_id = uuid4()
    identity = MeteringIdentity(
        execution_id=uuid4(),
        user_id=user_id,
        profile_id="system:test",
        profile_scope="SYSTEM",
        model_name="overage",
        provider_model_name="overage",
    )
    window = BudgetWindow(
        organization_id=None,
        user_id=user_id,
        kind="user_week",
        start=now - timedelta(days=1),
        end=now + timedelta(days=1),
        limit=Decimal("3"),
    )
    async with db_manager.session_factory() as session, session.begin():
        allocation = await open_allocation(
            session,
            allocation_id=uuid4(),
            identity=identity,
            pricing=RateCard(model="overage"),
            windows=[window],
            required=Decimal("1"),
            target=Decimal("2"),
            now=now,
            timeout_seconds=120,
        )
    async with db_manager.session_factory() as session, session.begin():
        await checkpoint(
            session,
            UsageBatch(
                allocation_id=allocation.id,
                sequence=1,
                counts=TokenCounts(request_count=1),
                cost=Decimal("2.5"),
                occurred_at=now,
            ),
            now=now,
            timeout_seconds=120,
        )
    async with db_manager.session_factory() as session, session.begin():
        closed = await checkpoint(
            session,
            UsageBatch(
                allocation_id=allocation.id,
                sequence=2,
                counts=TokenCounts(request_count=1, unconfirmed_requests=1),
                uncertain=Decimal("1"),
                occurred_at=now,
                close=True,
            ),
            now=now,
            timeout_seconds=120,
        )
        assert closed.amount == 0
    async with db_manager.session_factory() as session:
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(UsageLimitCounter.user_id == user_id)
            )
        ).one()
        assert counter.used_usd == Decimal("2.5")
        assert counter.reserved_usd == Decimal("1")


async def test_complete_response_without_provider_usage_retains_authority(
    db_manager: DatabaseManager, bounded_model_name: str
) -> None:
    def provider(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "missing-usage",
                "object": "chat.completion",
                "created": 1,
                "model": bounded_model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": None,
            },
        )

    user_id = uuid4()
    async with AsyncOpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(provider)),
    ) as client:
        model = MeteredModel(
            OpenAIChatModel(
                bounded_model_name, provider=OpenAIProvider(openai_client=client)
            ),
            {
                "profile_id": "system:test",
                "scope": "SYSTEM",
                "model_name": bounded_model_name,
                "model_metadata": {"context_window": 1000},
            },
        )
        async with metering_execution(
            UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None),
            factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        ):
            response = await model.request([], None, ModelRequestParameters())
            assert response.state == "complete"
            assert response.usage.total_tokens == 0

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
