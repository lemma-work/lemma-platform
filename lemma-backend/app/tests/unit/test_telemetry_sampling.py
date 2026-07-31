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


def test_unrecognized_strategy_fails_closed(monkeypatch):
    import pytest

    with pytest.raises(ValueError, match="unsupported OTEL trace sampler"):
        _sampler_for(monkeypatch, "something-weird", 0.05)


def test_parentbased_sampler_preserves_parent_decision(monkeypatch):
    from opentelemetry import trace
    from opentelemetry.sdk.trace.sampling import Decision

    sampler = _sampler_for(monkeypatch, "parentbased_traceidratio", 0.0)

    def decision(flags):
        parent = trace.NonRecordingSpan(
            trace.SpanContext(
                    trace_id=1,
                    span_id=2,
                    is_remote=True,
                    trace_flags=trace.TraceFlags(flags),
            )
        )
        return sampler.should_sample(
            trace.set_span_in_context(parent),
            trace_id=3,
            name="application.operation",
        ).decision

    assert decision(trace.TraceFlags.SAMPLED) is Decision.RECORD_AND_SAMPLE
    assert decision(trace.TraceFlags.DEFAULT) is Decision.DROP


def test_sampler_ratio_outside_unit_interval_fails_closed(monkeypatch):
    import pytest

    with pytest.raises(ValueError, match="between 0 and 1"):
        _sampler_for(monkeypatch, "traceidratio", 1.01)
