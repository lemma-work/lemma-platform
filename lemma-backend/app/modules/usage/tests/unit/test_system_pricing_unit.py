"""DB-free unit tests for system-model pricing and cost.

These pin the fixes for the metering-breakaway bug: every shipped system:lemma
model must be priced (so none escapes metering), cached input is billed at the
discounted rate, the corrected kimi-k2.6 rate, and the fail-safe path that
records usage at a fallback price instead of raising and dropping the record.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import AgentRunUsage
from app.modules.agent.services.runtime_profile_service import (
    _openai_compat_provider_model_name,
    system_lemma_openai_catalog_model_names,
)
from app.modules.test_support.fakes import FakeUnitOfWork
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.services.usage_service import (
    UsageService,
    assert_system_pricing_covers_catalog,
)

pytestmark = pytest.mark.unit

SYSTEM = "SYSTEM"


class _RecordingUsageRepository:
    """Captures created records / released reservations; no DB."""

    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.created: list = []
        self.released: list = []

    async def create(self, record):
        self.created.append(record)
        return record

    async def release_reservation(self, *, counter_ids, amount_usd):
        self.released.append((counter_ids, amount_usd))


def _service() -> UsageService:
    return UsageService(
        usage_repository=_RecordingUsageRepository(), usage_limit_port=None
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


def _default_openai_catalog() -> list[tuple[str, str | None]]:
    """The Fireworks system:lemma catalog the pricing table must cover.

    Pinned explicitly rather than read from the config field defaults: in the
    public OSS repo those defaults are provider-agnostic (OpenAI), so the
    metering coverage gate targets the models we actually price (Fireworks),
    deterministically across environments."""
    names = [
        "minimax-m3",
        "glm-5.2",
        "kimi-k2.7-code",
        "kimi-k2.6",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]
    return [(name, _openai_compat_provider_model_name(name)) for name in names]


def test_pricing_table_covers_default_system_catalog():
    # Regression gate: every shipped system model must be priced. This would have
    # failed for glm-5.2 before the fix (it was in the catalog, not the table).
    assert assert_system_pricing_covers_catalog(_default_openai_catalog()) == []


def test_glm_5_2_is_priced_by_both_names():
    # The exact breakaway model, by public name and provider id.
    uncovered = assert_system_pricing_covers_catalog(
        [("glm-5.2", "accounts/fireworks/models/glm-5p2")]
    )
    assert uncovered == []


def test_coverage_invariant_reports_unpriced_models():
    uncovered = assert_system_pricing_covers_catalog(
        [("brand-new-model", "accounts/fireworks/models/brand-new")]
    )
    assert uncovered == ["brand-new-model"]


def test_live_catalog_helper_includes_glm_and_is_priced(monkeypatch):
    from app.core.config import settings

    # Pin the Fireworks catalog (shipped OSS defaults are provider-agnostic now).
    monkeypatch.setattr(
        settings,
        "lemma_openai_model_names",
        "minimax-m3,glm-5.2,kimi-k2.7-code,kimi-k2.6,deepseek-v4-pro,deepseek-v4-flash",
    )
    monkeypatch.setattr(settings, "lemma_openai_default_model", "minimax-m3")
    monkeypatch.delenv("LEMMA_OPENAI_MODEL_NAMES", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_DEFAULT_MODEL", raising=False)
    catalog = system_lemma_openai_catalog_model_names()
    assert ("glm-5.2", "accounts/fireworks/models/glm-5p2") in catalog
    assert assert_system_pricing_covers_catalog(catalog) == []


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


def test_unpriced_model_uses_fallback_and_does_not_raise():
    service = _service()
    pricing, fallback = service._resolve_pricing("totally-unknown-model", None)
    assert fallback is True
    assert pricing is UsageService._FALLBACK_PRICING

    cost, cost_fallback = service._calculate_system_cost(
        profile_scope=SYSTEM,
        model_name="totally-unknown-model",
        provider_model_name=None,
        input_tokens=1000,
        output_tokens=0,
        units=0.0,
    )
    assert cost_fallback is True
    assert cost > 0


async def test_record_persists_with_fallback_pricing():
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
    assert record.cost_usd is not None and record.cost_usd > 0
    assert record.metadata.get("pricing_fallback") is True


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
    assert "pricing_fallback" not in record.metadata
