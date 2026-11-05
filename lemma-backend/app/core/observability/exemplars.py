"""Exemplars: a histogram bucket that remembers one real trace that landed in it.

Exemplars were off because the SDK copies every measurement attribute the View
dropped into the exemplar's ``filtered_attributes`` -- which would carry the
identifiers the attribute allowlist exists to keep out. They are on now with
that hole closed: the reservoir below is offered the measurement *without* its
attributes, so an exemplar carries only value, time, trace_id and span_id.

Only explicit-bucket histograms keep exemplars. Counters and gauges get a
reservoir that keeps nothing: a trace link is useful on a latency bucket ("click
the spike, see the request") and noise on a sum.

The filter is trace-based: a measurement is offered only when a *sampled* span
is current, so every exemplar points at a trace that was actually exported.
"""

from __future__ import annotations

from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.metrics._internal.aggregation import (
    _Aggregation,
    _ExplicitBucketHistogramAggregation,
)
from opentelemetry.sdk.metrics._internal.exemplar import (
    AlignedHistogramBucketExemplarReservoir,
    Exemplar,
    ExemplarReservoir,
    ExemplarReservoirBuilder,
)
from opentelemetry.sdk.metrics.view import View
from opentelemetry.util.types import Attributes

from app.core.observability.span_sanitizer import METRIC_ATTRIBUTE_KEYS

__all__ = ["exemplar_reservoir_factory", "metric_view"]


class _TraceOnlyHistogramReservoir(AlignedHistogramBucketExemplarReservoir):
    """One exemplar per bucket, stripped of every measurement attribute."""

    def offer(
        self,
        value: int | float,
        time_unix_nano: int,
        attributes: Attributes,
        context: Context,
    ) -> None:
        super().offer(value, time_unix_nano, None, context)


class _NoExemplarReservoir(ExemplarReservoir):
    def offer(
        self,
        value: int | float,
        time_unix_nano: int,
        attributes: Attributes,
        context: Context,
    ) -> None:
        return None

    def collect(self, point_attributes: Attributes) -> list[Exemplar]:
        return []


def _no_exemplars(**_: Any) -> ExemplarReservoir:
    return _NoExemplarReservoir()


def exemplar_reservoir_factory(
    aggregation_type: type[_Aggregation],
) -> ExemplarReservoirBuilder:
    """The View's reservoir factory: trace-only exemplars on histograms, none elsewhere."""
    if issubclass(aggregation_type, _ExplicitBucketHistogramAggregation):
        return _TraceOnlyHistogramReservoir
    return _no_exemplars


def metric_view() -> View:
    """The one View every instrument goes through: label allowlist + trace-only exemplars."""
    return View(
        instrument_name="*",
        attribute_keys=set(METRIC_ATTRIBUTE_KEYS),
        exemplar_reservoir_factory=exemplar_reservoir_factory,
    )
