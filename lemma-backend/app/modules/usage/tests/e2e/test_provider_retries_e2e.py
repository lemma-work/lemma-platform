"""Each provider retry checks current usage and records its own outcome."""

from collections.abc import AsyncIterator, Callable, Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import select

from app.core.config import settings
from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.usage.contracts.metering import with_external_stream_retries
from app.modules.usage.domain.errors import (
    ProviderAttemptsExhaustedError,
    UsageLimitExceededError,
)
from app.modules.usage.infrastructure.metered_model import MeteredModel
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord
from app.modules.usage.infrastructure.provider_retries import retry_delay
from app.modules.usage.services.metering_scope import metering_execution
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.services.usage_service import ModelPricing, UsageService

pytestmark = pytest.mark.e2e


@pytest.fixture
def retry_model_name(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"retry-test-{uuid4()}"
    monkeypatch.setattr(settings, "usage_user_weekly_limit_usd", 10.0)
    UsageService.register_model_pricing({name: ModelPricing(1000, 0)})
    try:
        yield name
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop(name, None)


class TransientProvider:
    def __init__(
        self, status: int, failures: int, on_failure: Callable[[], None] | None = None
    ) -> None:
        self.status = status
        self.failures = failures
        self.attempts = 0
        self.on_failure = on_failure

    def attempt(self) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            if self.on_failure is not None:
                self.on_failure()
            raise ModelHTTPError(self.status, "test", headers={"Retry-After": "0"})

    async def request(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        self.attempt()
        return ModelResponse(
            parts=[TextPart("ok")], usage=RequestUsage(input_tokens=10, output_tokens=1)
        )

    async def stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        self.attempt()
        yield "ok"


async def invoke(
    db_manager: DatabaseManager,
    name: str,
    provider: TransientProvider,
    user_id: UUID,
    streaming: bool,
    *,
    external_stream_retries: bool = False,
) -> None:
    model = MeteredModel(
        FunctionModel(provider.request, stream_function=provider.stream),
        {
            "profile_id": "system:test",
            "scope": "SYSTEM",
            "model_name": name,
            "model_metadata": {"context_window": 1000},
        },
    )
    async with metering_execution(
        UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None),
        factory=SessionUnitOfWorkFactory(db_manager.session_factory),
    ):
        if streaming:
            stream_model = (
                with_external_stream_retries(model)
                if external_stream_retries
                else model
            )
            async with stream_model.request_stream(
                [], None, ModelRequestParameters()
            ) as stream:
                async for _ in stream:
                    pass
                assert stream.get().parts == [TextPart("ok")]
                assert stream.usage.input_tokens == 50
        else:
            response = await model.request([], None, ModelRequestParameters())
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 1


async def test_external_stream_retries_keep_each_attempt_in_the_ledger(
    db_manager: DatabaseManager, retry_model_name: str
) -> None:
    provider = TransientProvider(503, 1)
    user_id = uuid4()
    with pytest.raises(ModelHTTPError):
        await invoke(
            db_manager,
            retry_model_name,
            provider,
            user_id,
            True,
            external_stream_retries=True,
        )
    assert provider.attempts == 1
    await assert_ledger(db_manager, user_id, attempts=1, used=Decimal(0), unconfirmed=1)
    await invoke(
        db_manager,
        retry_model_name,
        provider,
        user_id,
        True,
        external_stream_retries=True,
    )
    assert provider.attempts == 2
    await assert_ledger(
        db_manager, user_id, attempts=2, used=Decimal(".05"), unconfirmed=1
    )


async def assert_ledger(
    db_manager: DatabaseManager,
    user_id: UUID,
    *,
    attempts: int,
    used: Decimal,
    unconfirmed: int = 0,
) -> None:
    async with db_manager.session_factory() as session:
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == user_id,
                    UsageLimitCounter.window_kind == "user_week",
                )
            )
        ).one()
        assert counter.used_usd == used
        assert counter.reserved_usd == 0
        records = (
            await session.scalars(
                select(UsageRecord).where(UsageRecord.user_id == user_id)
            )
        ).all()
        total_requests = 0
        total_cost = Decimal(0)
        uncertain_records = 0
        for record in records:
            assert record.record_metadata is not None
            request_count = record.record_metadata["request_count"]
            assert isinstance(request_count, int)
            total_requests += request_count
            total_cost += record.cost_amount or Decimal(0)
            uncertain_records += (
                record.record_metadata["metering_state"] == "UNCONFIRMED"
            )
        assert total_requests == attempts
        assert total_cost == used
        assert uncertain_records == unconfirmed


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("status", [429, 503])
async def test_transient_attempts_are_metered_before_eventual_success(
    db_manager: DatabaseManager, retry_model_name: str, streaming: bool, status: int
) -> None:
    provider = TransientProvider(status, failures=2)
    user_id = uuid4()
    await invoke(db_manager, retry_model_name, provider, user_id, streaming)
    assert provider.attempts == 3
    await assert_ledger(
        db_manager,
        user_id,
        attempts=3,
        used=Decimal("0.05") if streaming else Decimal("0.01"),
        unconfirmed=2 if status == 503 else 0,
    )


@pytest.mark.parametrize("streaming", [False, True])
async def test_validation_rejection_is_not_retried_or_reserved(
    db_manager: DatabaseManager, retry_model_name: str, streaming: bool
) -> None:
    provider = TransientProvider(400, failures=10)
    user_id = uuid4()
    with pytest.raises(ModelHTTPError) as error:
        await invoke(db_manager, retry_model_name, provider, user_id, streaming)
    assert error.value.status_code == 400
    assert provider.attempts == 1
    await assert_ledger(db_manager, user_id, attempts=1, used=Decimal(0))


@pytest.mark.parametrize("streaming", [False, True])
async def test_unconfirmed_attempts_do_not_invent_a_charge(
    db_manager: DatabaseManager,
    retry_model_name: str,
    streaming: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "usage_user_weekly_limit_usd", 1.0)
    provider = TransientProvider(503, failures=10)
    user_id = uuid4()
    with pytest.raises(ProviderAttemptsExhaustedError):
        await invoke(db_manager, retry_model_name, provider, user_id, streaming)
    assert provider.attempts == 3
    await assert_ledger(db_manager, user_id, attempts=3, used=Decimal(0), unconfirmed=3)


@pytest.mark.parametrize("streaming", [False, True])
async def test_exhausted_provider_attempts_are_terminal_and_all_remain_accounted(
    db_manager: DatabaseManager, retry_model_name: str, streaming: bool
) -> None:
    provider = TransientProvider(503, failures=10)
    user_id = uuid4()
    with pytest.raises(ProviderAttemptsExhaustedError) as error:
        await invoke(db_manager, retry_model_name, provider, user_id, streaming)
    assert provider.attempts == 3
    assert retry_delay(error.value, 0) is None
    await assert_ledger(db_manager, user_id, attempts=3, used=Decimal(0), unconfirmed=3)


@pytest.mark.parametrize("streaming", [False, True])
async def test_each_provider_retry_observes_changed_shared_budget(
    db_manager: DatabaseManager,
    retry_model_name: str,
    streaming: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exhaust_allowance() -> None:
        monkeypatch.setattr(settings, "usage_user_weekly_limit_usd", 0.0)

    provider = TransientProvider(503, failures=10, on_failure=exhaust_allowance)
    user_id = uuid4()
    with pytest.raises(UsageLimitExceededError):
        await invoke(db_manager, retry_model_name, provider, user_id, streaming)
    assert provider.attempts == 1
    await assert_ledger(db_manager, user_id, attempts=1, used=Decimal(0), unconfirmed=1)
