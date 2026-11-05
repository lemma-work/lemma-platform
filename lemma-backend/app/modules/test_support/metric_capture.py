"""Read what the process's instruments recorded, through the export boundary.

Instruments are module-level and created from the global meter, so a test
cannot hand them a provider. This installs one process-wide SDK provider with
an in-memory reader, once, behind the same catch-all View production uses --
so a label that the allowlist (``METRIC_ATTRIBUTE_KEYS``) would strip is
stripped here too, and a test asserting on it fails the way the dashboard
would.
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.metrics.view import View

from app.core.observability.span_sanitizer import METRIC_ATTRIBUTE_KEYS

_reader: InMemoryMetricReader | None = None


@dataclass(frozen=True)
class Point:
    attributes: dict[str, object]
    #: A counter's sum, or a gauge's value; ``None`` for a histogram.
    value: float | None
    #: A histogram's count; ``None`` otherwise.
    count: int | None


def metric_reader() -> InMemoryMetricReader:
    global _reader
    if _reader is None:
        reader = InMemoryMetricReader()
        provider = MeterProvider(
            metric_readers=[reader],
            views=[
                View(instrument_name="*", attribute_keys=set(METRIC_ATTRIBUTE_KEYS))
            ],
        )
        metrics.set_meter_provider(provider)
        if metrics.get_meter_provider() is not provider:
            raise RuntimeError("another meter provider was installed first")
        _reader = reader
    return _reader


def points(name: str) -> list[Point]:
    """Every point of ``name`` in a fresh collection (cumulative totals)."""
    data = metric_reader().get_metrics_data()
    found: list[Point] = []
    if data is None:
        return found
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    found.append(
                        Point(
                            attributes=dict(point.attributes or {}),
                            value=getattr(point, "value", None),
                            count=getattr(point, "count", None),
                        )
                    )
    return found


def total(name: str, **attributes: object) -> float:
    """Sum (counter) or count (histogram) of the points carrying ``attributes``."""
    result = 0.0
    for point in points(name):
        if all(point.attributes.get(k) == v for k, v in attributes.items()):
            result += point.value if point.value is not None else (point.count or 0)
    return result
