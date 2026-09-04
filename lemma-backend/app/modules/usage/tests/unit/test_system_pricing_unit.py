"""DB-free unit tests for system-model pricing and cost.

These pin optional pricing semantics: cached input is billed at the discounted
rate when pricing is registered, while an unpriced model still creates a usage
record with a null cost. Provider-specific pricing is registered by composed
deployments; the tests below use a local fixture.
"""

from __future__ import annotations

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.modules.agent.domain.value_objects import AgentRunUsage
from app.modules.test_support.fakes import FakeUnitOfWork
from app.modules.usage.domain.ports import UsageLimitValues
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.domain.entities import UsageReservation
from app.modules.usage.domain.entities import CostSource
from app.modules.usage.services.cost_resolver import UsageTokens, resolve_cost
from app.modules.usage.services.usage_service import (
    ModelPricing,
    UsageService,
    assert_system_pricing_covers_catalog,
)

pytestmark = pytest.mark.unit

SYSTEM = "SYSTEM"


def _cost(
    model_name: str,
    *,
    provider_model_name: str | None = None,
    base_url: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    units: float = 0.0,
):
    """Resolve one cost through the real layered resolver."""
    return resolve_cost(
        model_name=model_name,
        provider_model_name=provider_model_name,
        base_url=base_url,
        tokens=UsageTokens(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            units=units,
        ),
        pricing_table=UsageService._SYSTEM_MODEL_PRICING,
    )


# Pricing fixture — mirrors the Fireworks rates registered in lemma-cloud.
# Kept here so the cost-calculation tests remain hermetic without depending on
# the cloud module. Full coverage (catalog × pricing) is tested in lemma-cloud.
_TEST_PRICING: dict[str, ModelPricing] = {
    "glm-5.2": ModelPricing(1.40, 4.40, cached_input_per_million_usd=0.26),
    "accounts/fireworks/models/glm-5p2": ModelPricing(
        1.40, 4.40, cached_input_per_million_usd=0.26
    ),
    "kimi-k2.6": ModelPricing(0.95, 4.00, cached_input_per_million_usd=0.16),
    "accounts/fireworks/models/kimi-k2p6": ModelPricing(
        0.95, 4.00, cached_input_per_million_usd=0.16
    ),
}


@pytest.fixture(autouse=True)
def _pricing_setup():
    """Register test pricing and clean up after each test."""
    UsageService.register_model_pricing(_TEST_PRICING)
    yield
    for key in _TEST_PRICING:
        UsageService._SYSTEM_MODEL_PRICING.pop(key, None)


class _RecordingUsageRepository:
    """Captures created records / released reservations; no DB."""

    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.created: list = []
        self.released: list = []
        self.consumed: list = []

    async def create(self, record):
        self.created.append(record)
        return record

    async def release_reservation(self, *, counter_ids, amount_usd):
        self.released.append((counter_ids, amount_usd))

    async def consume_reservation(self, **kwargs):
        self.consumed.append(kwargs)


def _service() -> UsageService:
    return UsageService(
        usage_repository=_RecordingUsageRepository(), usage_limit_port=None
    )


class _LimitPort:
    async def resolve_limits(self, *, organization_id, user_id):
        del organization_id, user_id
        return UsageLimitValues(user_weekly_limit_usd=10.0)


def _limited_service(repo) -> UsageService:
    return UsageService(
        usage_repository=repo,
        usage_limit_port=_LimitPort(),
    )


def _runtime_profile(model_name, provider_model_name=None, scope=SYSTEM):
    return {
        "profile_id": "system:lemma",
        "scope": scope,
        "model_name": model_name,
        "provider_model_name": provider_model_name,
    }


def _usage(model_name, *, input_tokens, output_tokens, cache_read_tokens=0):
    return AgentRunUsage(
        model_name=model_name,
        usage_kind="llm",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        metadata={"cache_read_tokens": cache_read_tokens},
    )


def _ctx() -> UsageExecutionContext:
    return UsageExecutionContext(
        user_id=uuid4(),
        organization_id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
    )


def test_coverage_invariant_reports_unpriced_models():
    uncovered = assert_system_pricing_covers_catalog(
        [("brand-new-model", "accounts/example/models/brand-new")]
    )
    assert uncovered == ["brand-new-model"]


def test_coverage_invariant_passes_when_pricing_registered():
    # When pricing is registered (fixture does this), the invariant passes.
    uncovered = assert_system_pricing_covers_catalog(
        [("glm-5.2", "accounts/fireworks/models/glm-5p2")]
    )
    assert uncovered == []


def test_register_model_pricing_hook_works():
    extra = {"test-model": ModelPricing(1.0, 2.0)}
    UsageService.register_model_pricing(extra)
    assert "test-model" in UsageService._SYSTEM_MODEL_PRICING
    UsageService._SYSTEM_MODEL_PRICING.pop("test-model")


async def test_injected_limit_uses_legacy_default_reservation_amount():
    repo = AsyncMock()
    repo.get_system_cost.return_value = 0.0
    repo.get_system_cost_by_window.return_value = {"user_week": 0.0, "user_month": 0.0}
    repo.get_reserved_costs.return_value = {
        "user_week": 0.0,
        "user_month": 0.0,
        "org_month": 0.0,
    }
    repo.reserve_limit_scopes.return_value = []
    service = _limited_service(repo)

    reservation = await service.reserve_for_profile(
        organization_id=None,
        user_id=uuid4(),
        profile_id="system:lemma",
        profile_scope=SYSTEM,
        model_name="glm-5.2",
    )

    assert reservation is not None
    assert reservation.amount_usd == UsageService.DEFAULT_RESERVATION_USD


async def test_unlimited_default_skips_admission_for_unpriced_custom_model():
    repo = AsyncMock()
    service = UsageService(usage_repository=repo, usage_limit_port=None)

    reservation = await service.reserve_for_profile(
        organization_id=uuid4(),
        user_id=uuid4(),
        profile_id="system:local",
        profile_scope=SYSTEM,
        model_name="accounts/fireworks/models/minimax-m3",
    )

    assert reservation is None
    repo.reserve_limit_scopes.assert_not_awaited()


async def test_injected_limit_does_not_reject_unpriced_custom_model():
    repo = AsyncMock()
    repo.get_system_cost.return_value = 0.0
    repo.get_system_cost_by_window.return_value = {"user_week": 0.0, "user_month": 0.0}
    repo.get_reserved_costs.return_value = {
        "user_week": 0.0,
        "user_month": 0.0,
        "org_month": 0.0,
    }
    repo.reserve_limit_scopes.return_value = []
    service = _limited_service(repo)

    reservation = await service.reserve_for_profile(
        organization_id=uuid4(),
        user_id=uuid4(),
        profile_id="system:limited",
        profile_scope=SYSTEM,
        model_name="accounts/fireworks/models/minimax-m3",
    )

    assert reservation is not None
    assert reservation.amount_usd == UsageService.DEFAULT_RESERVATION_USD


def test_glm_cost_uses_glm_pricing():
    resolved = _cost(
        "glm-5.2",
        provider_model_name="accounts/fireworks/models/glm-5p2",
        input_tokens=1000,
        output_tokens=500,
    )
    # 1000/1e6*1.40 + 500/1e6*4.40
    assert resolved.cost_usd == pytest.approx(0.0036)
    assert resolved.source is CostSource.REGISTERED


def test_glm_cost_resolves_by_provider_id_only():
    resolved = _cost(
        "unknown-public-alias",
        provider_model_name="accounts/fireworks/models/glm-5p2",
        input_tokens=1_000_000,
    )
    assert resolved.cost_usd == pytest.approx(1.40)
    assert resolved.source is CostSource.REGISTERED


def test_kimi_k2_6_priced_at_corrected_rate():
    # Was mispriced at 0.50/2.00; Fireworks charges 0.95/4.00.
    resolved = _cost(
        "kimi-k2.6",
        provider_model_name="accounts/fireworks/models/kimi-k2p6",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert resolved.cost_usd == pytest.approx(0.95 + 4.00)
    assert resolved.source is CostSource.REGISTERED


def test_cost_applies_cached_token_discount():
    no_cache = _cost("glm-5.2", input_tokens=1000)
    with_cache = _cost("glm-5.2", input_tokens=1000, cache_read_tokens=400)
    # 600 uncached @ 1.40 + 400 cached @ 0.26
    expected = 600 / 1_000_000 * 1.40 + 400 / 1_000_000 * 0.26
    assert with_cache.cost_usd == pytest.approx(expected)
    assert with_cache.cost_usd < no_cache.cost_usd


def test_cost_cache_read_capped_at_input():
    # cache_read exceeding input must never produce negative non-cached cost.
    resolved = _cost("glm-5.2", input_tokens=100, cache_read_tokens=1000)
    assert resolved.cost_usd == pytest.approx(100 / 1_000_000 * 0.26)
    assert resolved.cost_usd >= 0


def test_cache_write_tokens_bill_at_their_own_rate():
    """Cache writes are a subset of input, priced separately when a rate exists.

    Anthropic charges a premium to *create* a cache entry. The rate used to be
    unrepresentable, so those tokens fell into the base-rate bucket and every
    cache write was undercharged.
    """
    UsageService.register_model_pricing(
        {
            "cache-write-model": ModelPricing(
                1.00,
                2.00,
                cached_input_per_million_usd=0.10,
                cache_write_per_million_usd=1.25,
            )
        }
    )
    try:
        resolved = _cost(
            "cache-write-model",
            input_tokens=1000,
            cache_read_tokens=400,
            cache_write_tokens=200,
        )
        # 400 uncached @ 1.00 + 400 cached @ 0.10 + 200 written @ 1.25
        expected = (
            400 / 1_000_000 * 1.00 + 400 / 1_000_000 * 0.10 + 200 / 1_000_000 * 1.25
        )
        assert resolved.cost_usd == pytest.approx(expected)
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop("cache-write-model", None)


def test_cache_write_without_a_rate_falls_back_to_the_base_input_rate():
    """No cache-write rate means the provider does not charge one (Fireworks)."""
    resolved = _cost("glm-5.2", input_tokens=1000, cache_write_tokens=200)
    # 800 uncached @ 1.40 + 200 written, also @ 1.40
    assert resolved.cost_usd == pytest.approx(1000 / 1_000_000 * 1.40)


def test_non_system_scope_is_priced_too():
    """A profile someone added with their own key still reports what it spent.

    Cost used to be computed only for SYSTEM scope, so every bring-your-own-key
    row carried a null forever and the person who added the profile could not see
    what their agents cost. Keeping that spend out of a Lemma plan limit is the
    job of the limit queries, which filter on profile_scope -- not of pricing.
    """
    resolved = _cost(
        "glm-5.2",
        provider_model_name="accounts/fireworks/models/glm-5p2",
        input_tokens=1000,
        output_tokens=1000,
    )
    assert resolved.cost_usd is not None
    assert resolved.cost_usd > 0


def test_unpriced_model_has_no_synthetic_cost_and_does_not_raise():
    resolved = _cost("totally-unknown-model-xyzzy", input_tokens=1000)
    assert resolved.cost_usd is None
    assert resolved.source is CostSource.UNKNOWN


def test_unregistered_model_is_estimated_from_the_public_dataset():
    """The layer that makes bring-your-own-key profiles reportable.

    Nothing registers a rate for `gpt-4o-mini`, and nobody should have to: the
    dataset behind pydantic-ai already knows it. The row is marked ESTIMATED so a
    best-effort number is never mistaken for a configured one.
    """
    resolved = _cost("gpt-4o-mini", input_tokens=1_000_000, output_tokens=0)
    assert resolved.source is CostSource.ESTIMATED
    assert resolved.cost_usd == pytest.approx(0.15)


def test_a_profile_base_url_identifies_the_provider():
    """A model reference alone is ambiguous; the profile's base URL is not."""
    resolved = _cost(
        "claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        input_tokens=100_000,
    )
    assert resolved.source is CostSource.ESTIMATED
    assert resolved.cost_usd == pytest.approx(0.30)  # 100k @ $3.00/MTok


def test_estimated_pricing_honours_context_tiers():
    """Something a four-field rate card cannot express, and Anthropic charges.

    Sonnet's input rate doubles above a 200k-token context. The registered table
    has one input rate per model, so a long-context run priced through it would
    be undercharged by half; the dataset carries the tier and applies it.
    """
    under = _cost(
        "claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        input_tokens=100_000,
    )
    over = _cost(
        "claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        input_tokens=1_000_000,
    )
    assert under.cost_usd == pytest.approx(0.30)  # 100k @ $3.00
    assert over.cost_usd == pytest.approx(6.00)  # 1M @ $6.00, the upper tier


def test_a_registered_rate_beats_the_public_dataset():
    """Explicit rates win, so a negotiated price is what gets charged."""
    UsageService.register_model_pricing({"gpt-4o-mini": ModelPricing(999.0, 999.0)})
    try:
        resolved = _cost("gpt-4o-mini", input_tokens=1_000_000)
        assert resolved.source is CostSource.REGISTERED
        assert resolved.cost_usd == pytest.approx(999.0)
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop("gpt-4o-mini", None)


async def test_record_persists_without_cost_when_pricing_is_missing():
    repo = _RecordingUsageRepository()
    service = UsageService(usage_repository=repo, usage_limit_port=None)

    record = await service.record_agent_run_usage(
        ctx=_ctx(),
        runtime_profile=_runtime_profile("mystery-model"),
        usage_data=_usage("mystery-model", input_tokens=1000, output_tokens=500),
        status="COMPLETED",
        reservation=None,
    )

    assert record is not None
    assert len(repo.created) == 1
    assert record.cost_usd is None
    assert record.metadata.get("pricing_missing") is True


async def test_record_priced_model_has_no_fallback_flag():
    repo = _RecordingUsageRepository()
    service = UsageService(usage_repository=repo, usage_limit_port=None)

    record = await service.record_agent_run_usage(
        ctx=_ctx(),
        runtime_profile=_runtime_profile(
            "glm-5.2", provider_model_name="accounts/fireworks/models/glm-5p2"
        ),
        usage_data=_usage(
            "glm-5.2", input_tokens=2000, output_tokens=1000, cache_read_tokens=500
        ),
        status="COMPLETED",
        reservation=None,
    )

    assert record is not None
    assert record.cost_usd == pytest.approx(
        1500 / 1_000_000 * 1.40 + 500 / 1_000_000 * 0.26 + 1000 / 1_000_000 * 4.40
    )
    assert "pricing_missing" not in record.metadata


async def test_actual_cost_consumes_reservation_without_admission_block():
    repo = _RecordingUsageRepository()
    service = UsageService(usage_repository=repo, usage_limit_port=None)
    reservation = UsageReservation(
        organization_id=uuid4(),
        user_id=uuid4(),
        amount_usd=0.000001,
        counter_ids=[],
    )

    await service.record_agent_run_usage(
        ctx=_ctx(),
        runtime_profile=_runtime_profile("glm-5.2"),
        usage_data=_usage("glm-5.2", input_tokens=1_000, output_tokens=500),
        status="COMPLETED",
        reservation=reservation,
    )

    assert repo.consumed[0]["actual_usd"] > reservation.amount_usd
