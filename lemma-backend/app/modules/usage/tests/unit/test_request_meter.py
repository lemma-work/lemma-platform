"""A failed settlement must finish before another provider request starts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.modules.usage.domain.accounting import RequestReceipt, TokenCounts
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.services.request_meter import RequestMeter


class Accounting:
    def __init__(self) -> None:
        self.starts = 0
        self.used = Decimal(0)
        self.receipts: dict[UUID, RequestReceipt] = {}
        self.lose_ack = False
        self.ack_error: Exception = ConnectionError("Commit acknowledgement lost")

    async def begin(
        self, request_id: UUID, now: datetime, *, priceable: bool = True
    ) -> bool:
        if self.used >= Decimal(1):
            raise UsageLimitExceededError()
        self.starts += 1
        return True

    async def record(self, receipt: RequestReceipt) -> bool:
        if receipt.request_id not in self.receipts:
            self.receipts[receipt.request_id] = receipt
            self.used += receipt.cost or Decimal(0)
        else:
            assert self.receipts[receipt.request_id] == receipt
        if self.lose_ack:
            self.lose_ack = False
            raise self.ack_error
        return self.used >= Decimal(1)


async def test_each_request_commits_usage_before_another_budget_check() -> None:
    gateway = Accounting()
    meter = RequestMeter(gateway)
    request_id, occurred_at, limited = await meter.before(priceable=True)
    assert limited
    await meter.after(
        RequestReceipt(
            request_id=request_id,
            occurred_at=occurred_at,
            counts=TokenCounts(request_count=1),
            cost=Decimal("1.2"),
        )
    )
    assert gateway.used == Decimal("1.2")
    with pytest.raises(UsageLimitExceededError):
        await meter.before(priceable=True)
    assert gateway.starts == 1
    await meter.close()


async def test_lost_ack_replays_the_same_receipt_before_next_dispatch() -> None:
    gateway = Accounting()
    meter = RequestMeter(gateway)
    request_id, occurred_at, _ = await meter.before(priceable=True)
    gateway.lose_ack = True
    with pytest.raises(ConnectionError):
        await meter.after(
            RequestReceipt(
                request_id=request_id,
                occurred_at=occurred_at,
                counts=TokenCounts(request_count=1),
                cost=Decimal("1.2"),
            )
        )
    with pytest.raises(UsageLimitExceededError):
        await meter.before(priceable=True)
    assert gateway.starts == 1
    assert gateway.used == Decimal("1.2")
    assert not meter.pending
    await meter.close()


@pytest.mark.parametrize("provider_status", [None, 400], ids=["success", "rejection"])
async def test_settlement_timeout_does_not_repeat_provider_request(
    provider_status: int | None,
) -> None:
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from pydantic_ai.usage import RequestUsage

    from app.modules.usage.infrastructure.metered_model import MeteredModel
    from app.modules.usage.services.metering_scope import metering_execution
    from app.modules.usage.services.usage_context import UsageExecutionContext
    from app.modules.usage.services.usage_service import ModelPricing, UsageService

    provider_calls = 0

    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal provider_calls
        provider_calls += 1
        if provider_status is not None:
            raise ModelHTTPError(provider_status, "test")
        return ModelResponse(
            parts=[TextPart("successful response")],
            usage=RequestUsage(input_tokens=10, output_tokens=1),
        )

    model_name = f"accounting-timeout-{uuid4()}"
    profile: dict[str, object] = {
        "profile_id": "system:timeout-test",
        "scope": "SYSTEM",
        "model_name": model_name,
    }
    gateway = Accounting()
    gateway.lose_ack = True
    gateway.ack_error = TimeoutError("Accounting commit acknowledgement timed out")
    UsageService.register_model_pricing({model_name: ModelPricing(1000, 0)})
    try:
        async with metering_execution(
            UsageExecutionContext(user_id=uuid4(), organization_id=None, pod_id=None)
        ) as scope:
            meter, _ = scope.meter(profile, None)
            meter.gateway = gateway
            model = MeteredModel(FunctionModel(provider), profile)
            with pytest.raises(TimeoutError, match="Accounting commit"):
                await model.request([], None, ModelRequestParameters())
            assert provider_calls == 1
            assert gateway.starts == 1
            assert len(meter.pending) == 1
        assert not meter.pending
        assert len(gateway.receipts) == 1
        assert gateway.used == (
            Decimal(".01") if provider_status is None else Decimal(0)
        )
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop(model_name, None)
