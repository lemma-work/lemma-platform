"""Latency histograms carry a trace exemplar, and nothing but the trace link."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider, TraceBasedExemplarFilter
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, Sampler

from app.core.observability.exemplars import metric_view


def _meter_provider() -> tuple[MeterProvider, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(
        metric_readers=[reader],
        exemplar_filter=TraceBasedExemplarFilter(),
        views=[metric_view()],
    )
    return provider, reader


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    assert data is not None
    return [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _tracer(sampler: Sampler):
    return TracerProvider(sampler=sampler).get_tracer("test")


@pytest.mark.unit
def test_histogram_recorded_in_sampled_span_carries_its_trace_id() -> None:
    provider, reader = _meter_provider()
    histogram = provider.get_meter("test").create_histogram(
        "lemma.http.server.duration_ms"
    )

    with _tracer(ALWAYS_ON).start_as_current_span("request") as span:
        histogram.record(42.0, {"http.route": "/pods/{pod_id}", "user_id": "u-123"})
        ctx = span.get_span_context()

    (point,) = _points(reader, "lemma.http.server.duration_ms")
    assert isinstance(point, HistogramDataPoint)
    assert dict(point.attributes) == {"http.route": "/pods/{pod_id}"}
    (exemplar,) = point.exemplars
    assert exemplar.trace_id == ctx.trace_id
    assert exemplar.span_id == ctx.span_id
    assert exemplar.value == 42.0
    # The attribute the allowlist dropped must not resurface in the exemplar.
    assert not exemplar.filtered_attributes


@pytest.mark.unit
def test_no_exemplar_outside_a_span_or_in_an_unsampled_one() -> None:
    provider, reader = _meter_provider()
    histogram = provider.get_meter("test").create_histogram(
        "lemma.work.handler.duration"
    )

    histogram.record(1.0, {"work_class": "fast"})
    with _tracer(ALWAYS_OFF).start_as_current_span("unsampled"):
        histogram.record(2.0, {"work_class": "fast"})

    (point,) = _points(reader, "lemma.work.handler.duration")
    assert point.count == 2
    assert point.exemplars == []


@pytest.mark.unit
def test_counters_keep_no_exemplars() -> None:
    provider, reader = _meter_provider()
    counter = provider.get_meter("test").create_counter("lemma.http.server.requests")

    with _tracer(ALWAYS_ON).start_as_current_span("request"):
        counter.add(1, {"http.route": "/x"})

    (point,) = _points(reader, "lemma.http.server.requests")
    assert point.exemplars == []


@pytest.mark.unit
async def test_work_handler_duration_is_recorded_inside_the_item_span(
    monkeypatch,
) -> None:
    """The worker histogram is recorded while ``work.execute`` is current."""
    from types import SimpleNamespace
    from uuid import uuid4

    from opentelemetry import trace

    from app.core.infrastructure.work import dispatcher as dispatcher_module
    from app.core.infrastructure.work.types import WorkClass, WorkMode

    provider, reader = _meter_provider()
    histogram = provider.get_meter("test").create_histogram(
        "lemma.work.handler.duration"
    )
    seen: list[int] = []

    class _Recorder:
        def record(self, value, attributes=None):
            seen.append(trace.get_current_span().get_span_context().trace_id)
            histogram.record(value, attributes)

    async def _run_fast(env, spec, item):
        return "done"

    monkeypatch.setattr(dispatcher_module, "tracer", _tracer(ALWAYS_ON))
    monkeypatch.setattr(dispatcher_module, "handler_duration", _Recorder())
    monkeypatch.setattr(dispatcher_module, "run_fast", _run_fast)
    env = SimpleNamespace(
        registry=SimpleNamespace(get=lambda kind: SimpleNamespace(mode=WorkMode.FAST)),
        local_wake=None,
    )
    cls = next(iter(WorkClass))
    dispatcher = dispatcher_module.Dispatcher(
        cls,
        env,
        slots=1,
        quantum=1,
        poll_interval=1.0,  # type: ignore[arg-type]
    )
    item = SimpleNamespace(
        id=uuid4(), kind="test.kind", ns="ns", traceparent=None, trace_link=None
    )

    assert await dispatcher.execute(item) == "done"  # type: ignore[arg-type]

    (point,) = _points(reader, "lemma.work.handler.duration")
    (exemplar,) = point.exemplars
    assert seen == [exemplar.trace_id] and exemplar.trace_id != 0
