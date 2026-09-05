"""Opt-in real provider receipts reconcile against disposable PostgreSQL state."""

import os
from decimal import Decimal, ROUND_CEILING
from uuid import uuid4

import pytest
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage
from sqlalchemy import select

from app.core.config import settings
from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts.model_runtime import resolve_system_runtime
from app.modules.usage.config import UsageSettings
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord
from app.modules.usage.infrastructure.price_catalog import RateCard
from app.modules.usage.services.metering_scope import metering_execution
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.services.usage_service import UsageService


_DEPLOYMENT_PRICE_METADATA = os.environ.get("LEMMA_SYSTEM_MODEL_METADATA_JSON", "{}")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.provider,
    pytest.mark.real_llm,
]


@pytest.fixture
def deployment_pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shared e2e fixture supplies synthetic prices; real-provider checks must
    # use only operator overrides or the bundled provider catalog.
    monkeypatch.setenv("LEMMA_SYSTEM_MODEL_METADATA_JSON", _DEPLOYMENT_PRICE_METADATA)
    monkeypatch.setattr(UsageService, "_SYSTEM_MODEL_PRICING", {})
    monkeypatch.setattr(UsageService, "_ENV_METADATA_SOURCE", None)


def _receipt_price(card: RateCard, usage: RunUsage) -> Decimal:
    # Keep this arithmetic independent of RateCard.price and genai_prices.calc_price.
    assert {
        "input_mtok",
        "cache_read_mtok",
        "cache_write_mtok",
        "output_mtok",
    } <= set(card.rates)
    assert usage.input_audio_tokens == usage.output_audio_tokens == 0
    assert usage.cache_audio_read_tokens == 0
    rates: dict[str, Decimal] = {}
    for name, rate in card.rates.items():
        applicable = [
            value for start, value in rate.tiers if usage.input_tokens > start
        ]
        rates[name] = applicable[-1] if applicable else rate.base
    uncached = usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
    total = (
        Decimal(uncached) * rates["input_mtok"]
        + Decimal(usage.cache_read_tokens)
        * rates.get("cache_read_mtok", rates["input_mtok"])
        + Decimal(usage.cache_write_tokens)
        * rates.get("cache_write_mtok", rates["input_mtok"])
        + Decimal(usage.output_tokens) * rates["output_mtok"]
    ) / 1_000_000
    return total.quantize(Decimal("0.000000001"), rounding=ROUND_CEILING)


@pytest.mark.parametrize("streaming", [False, True], ids=["request", "stream"])
@pytest.mark.usefixtures("deployment_pricing")
async def test_real_agent_receipt_matches_provider_tokens_and_frozen_rates(
    db_manager: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
) -> None:
    monkeypatch.setattr(settings, "usage_user_weekly_limit_usd", 5.0)
    user_id = uuid4()
    runtime = await resolve_system_runtime(
        user_id=user_id, usage_limits=UsageLimits(request_limit=1)
    )
    agent: Agent[None, str] = Agent(
        runtime.model,
        retries=0,
        model_settings=ModelSettings(max_tokens=512, timeout=60),
    )
    async with metering_execution(
        UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None),
        factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        settings=UsageSettings(),
    ):
        if streaming:
            async with agent.run_stream(
                "Reply with the single word OK.", usage_limits=runtime.usage_limits
            ) as result:
                output = await result.get_output()
                usage = result.usage
        else:
            completed = await agent.run(
                "Reply with the single word OK.", usage_limits=runtime.usage_limits
            )
            output = completed.output
            usage = completed.usage
        assert output.strip()
        assert usage.requests == 1
        assert usage.input_tokens > 0
        if not streaming:
            monkeypatch.setattr(settings, "usage_user_weekly_limit_usd", 0.000000001)
            with pytest.raises(UsageLimitExceededError):
                await agent.run(
                    "Reply with the single word OK.", usage_limits=runtime.usage_limits
                )

    async with db_manager.session_factory() as session:
        receipt = (
            await session.scalars(
                select(UsageRecord).where(UsageRecord.user_id == user_id)
            )
        ).one()
        assert receipt.input_tokens == usage.input_tokens
        assert receipt.output_tokens == usage.output_tokens
        assert receipt.cached_input_tokens == usage.cache_read_tokens
        assert receipt.cache_write_tokens == usage.cache_write_tokens
        assert receipt.record_metadata is not None
        assert receipt.request_id is not None
        card = RateCard.model_validate(receipt.record_metadata["pricing"])
        expected_cost = _receipt_price(card, usage)
        assert expected_cost > 0
        assert receipt.cost_amount == expected_cost
        assert receipt.record_metadata["metering_state"] == "RECORDED"
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == user_id,
                    UsageLimitCounter.window_kind == "user_week",
                )
            )
        ).one()
        assert counter.used_usd == expected_cost
        assert counter.reserved_usd == 0
        print(
            f"real-accounting streaming={streaming} model={receipt.model_name} "
            f"input={usage.input_tokens} cached={usage.cache_read_tokens} "
            f"output={usage.output_tokens} cost_usd={expected_cost} source={card.source.value}"
        )
