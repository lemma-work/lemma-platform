"""Trace sampler configuration (Phase-3 readiness).

Exporters stay disabled in Phase 1, but the sampler must be plumbed so enabling
traces is a config flip. Verifies the provider honors
``parentbased_traceidratio`` + ``OTEL_TRACES_SAMPLER_ARG``.
"""

from __future__ import annotations


from app.core.config import settings
from app.core.observability import telemetry


def _sampler_for(monkeypatch, strategy, ratio):
    monkeypatch.setattr(settings, "otel_traces_sampler", strategy)
    monkeypatch.setattr(settings, "otel_traces_sampler_arg", ratio)
    return telemetry._build_sampler(settings)


def test_default_sampler_is_parentbased_5_percent(monkeypatch):
    monkeypatch.setattr(settings, "otel_traces_sampler", "parentbased_traceidratio")
    monkeypatch.setattr(settings, "otel_traces_sampler_arg", 0.05)
    sampler = telemetry._build_sampler(settings)
    assert type(sampler).__name__ == "ParentBased"


def _root_ratio(sampler):
    root = getattr(sampler, "_root", None)
    if root is None:
        root = getattr(sampler, "root", None)
    return getattr(root, "rate", None)


def test_parentbased_honors_ratio_arg(monkeypatch):
    sampler = _sampler_for(monkeypatch, "parentbased_traceidratio", 0.25)
    assert _root_ratio(sampler) == 0.25


def test_traceidratio_strategy(monkeypatch):
    sampler = _sampler_for(monkeypatch, "traceidratio", 0.1)
    assert type(sampler).__name__ == "TraceIdRatioBased"
    assert sampler.rate == 0.1


def test_always_on_strategy(monkeypatch):
    from opentelemetry.sdk.trace.sampling import Decision

    sampler = _sampler_for(monkeypatch, "always_on", 0.0)
    assert type(sampler).__name__ == "StaticSampler"
    assert sampler._decision == Decision.RECORD_AND_SAMPLE


def test_always_off_strategy(monkeypatch):
    from opentelemetry.sdk.trace.sampling import Decision

    sampler = _sampler_for(monkeypatch, "always_off", 1.0)
    assert type(sampler).__name__ == "StaticSampler"
    assert sampler._decision == Decision.DROP


def test_unrecognized_strategy_falls_back_to_parentbased(monkeypatch):
    sampler = _sampler_for(monkeypatch, "something-weird", 0.05)
    assert type(sampler).__name__ == "ParentBased"
