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
from app.modules.usage.services.usage_service import (
    ModelPricing,
    UsageService,
    assert_system_pricing_covers_catalog,
)

pytestmark = pytest.mark.unit

SYSTEM = "SYSTEM"

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
    repo.get_reserved_cost.return_value = 0.0
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
    repo.get_reserved_cost.return_value = 0.0
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
    cost, fallback = _service()._calculate_system_cost(
        profile_scope=SYSTEM,
        model_name="glm-5.2",
        provider_model_name="accounts/fireworks/models/glm-5p2",
        input_tokens=1000,
        output_tokens=500,
        units=0.0,
    )
    # 1000/1e6*1.40 + 500/1e6*4.40
    assert cost == pytest.approx(0.0036)
    assert fallback is False


def test_glm_cost_resolves_by_provider_id_only():
    cost, fallback = _service()._calculate_system_cost(
        profile_scope=SYSTEM,
        model_name="unknown-public-alias",
        provider_model_name="accounts/fireworks/models/glm-5p2",
        input_tokens=1_000_000,
        output_tokens=0,
        units=0.0,
    )
    assert cost == pytest.approx(1.40)
    assert fallback is False


def test_kimi_k2_6_priced_at_corrected_rate():
    # Was mispriced at 0.50/2.00; Fireworks charges 0.95/4.00.
    cost, fallback = _service()._calculate_system_cost(
        profile_scope=SYSTEM,
        model_name="kimi-k2.6",
        provider_model_name="accounts/fireworks/models/kimi-k2p6",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        units=0.0,
    )
    assert cost == pytest.approx(0.95 + 4.00)
    assert fallback is False


def test_cost_applies_cached_token_discount():
    service = _service()
    no_cache, _ = service._calculate_system_cost(
        profile_scope=SYSTEM,
        model_name="glm-5.2",
        provider_model_name=None,
        input_tokens=1000,
        output_tokens=0,
        units=0.0,
        cache_read_tokens=0,
    )
    with_cache, _ = service._calculate_system_cost(
        profile_scope=SYSTEM,
        model_name="glm-5.2",
        provider_model_name=None,
        input_tokens=1000,
        output_tokens=0,
        units=0.0,
        cache_read_tokens=400,
    )
    # 600 uncached @ 1.40 + 400 cached @ 0.26
    expected = 600 / 1_000_000 * 1.40 + 400 / 1_000_000 * 0.26
    assert with_cache == pytest.approx(expected)
    assert with_cache < no_cache


def test_cost_cache_read_capped_at_input():
    # cache_read exceeding input must never produce negative non-cached cost.
    cost, _ = _service()._calculate_system_cost(
        profile_scope=SYSTEM,
        model_name="glm-5.2",
        provider_model_name=None,
        input_tokens=100,
        output_tokens=0,
        units=0.0,
        cache_read_tokens=1000,
    )
    assert cost == pytest.approx(100 / 1_000_000 * 0.26)
    assert cost >= 0


def test_non_system_scope_has_no_cost():
    cost, fallback = _service()._calculate_system_cost(
        profile_scope="ORGANIZATION",
        model_name="glm-5.2",
        provider_model_name=None,
        input_tokens=1000,
        output_tokens=1000,
        units=0.0,
    )
    assert cost is None
    assert fallback is False


def test_unpriced_model_has_no_synthetic_cost_and_does_not_raise():
    service = _service()
    pricing, missing = service._resolve_pricing("totally-unknown-model", None)
    assert missing is True
    assert pricing is None

    cost, cost_missing = service._calculate_system_cost(
        profile_scope=SYSTEM,
        model_name="totally-unknown-model",
        provider_model_name=None,
        input_tokens=1000,
        output_tokens=0,
        units=0.0,
    )
    assert cost_missing is True
    assert cost is None


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
