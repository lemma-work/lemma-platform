"""Carry the enqueuer's trace into the work item, so async work joins it.

A request that queues an agent turn, a delivery or a function run should show
that work in its own trace, and a request that was sampled -- by ratio, by an
upstream ``traceparent``, or because its account is always traced -- should
have the work it caused sampled too. So the writer stores the current W3C
``traceparent`` in the item, and the dispatcher starts ``work.execute`` as its
child; the worker's parent-based sampler then follows the request's decision.

Two keys in the stored payload, removed again when the item is claimed, so no
handler ever sees them:

* ``_traceparent`` -- the parent.
* ``_trace_link`` -- a periodic's re-arm. Each run of a periodic is queued by
  the run before it; as a parent that would chain every run since boot into
  one endless trace, so the next run is a root that *links* to the last.

Retries keep the stored row, so every attempt is a child of the same parent.
An item queued with no span current (start-up, maintenance) carries nothing
and runs as a root.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Link
from pydantic import JsonValue

from app.core.infrastructure.work.types import JsonObject
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

PARENT_KEY = "_traceparent"
LINK_KEY = "_trace_link"
TraceCarry = Literal["parent", "link"]

_w3c = TraceContextTextMapPropagator()


def with_current_trace(payload: JsonObject, carry: TraceCarry = "parent") -> JsonObject:
    """``payload`` plus the current span's ``traceparent``, if there is one."""
    if not trace.get_current_span().get_span_context().is_valid:
        return payload
    carrier: dict[str, str] = {}
    _w3c.inject(carrier)
    traceparent = carrier.get("traceparent")
    if not traceparent:
        return payload
    return {**payload, (PARENT_KEY if carry == "parent" else LINK_KEY): traceparent}


def split_trace(
    payload: Mapping[str, JsonValue],
) -> tuple[JsonObject, str | None, str | None]:
    """The handler's payload, and the parent and link ``traceparent`` it carried."""
    clean = {k: v for k, v in payload.items() if k not in (PARENT_KEY, LINK_KEY)}
    parent, link = payload.get(PARENT_KEY), payload.get(LINK_KEY)
    return (
        clean,
        parent if isinstance(parent, str) else None,
        link if isinstance(link, str) else None,
    )


def _span_context(traceparent: str | None) -> trace.SpanContext | None:
    if not traceparent:
        return None
    context = _w3c.extract({"traceparent": traceparent}, context=Context())
    span_context = trace.get_current_span(context).get_span_context()
    return span_context if span_context.is_valid else None


def work_span_parent(
    traceparent: str | None, trace_link: str | None
) -> tuple[Context, list[Link]]:
    """What ``work.execute`` starts under: the carried parent, else a root
    (linked to the run that queued it, for a periodic)."""
    parent = _span_context(traceparent)
    if parent is not None:
        return trace.set_span_in_context(trace.NonRecordingSpan(parent)), []
    root = trace.set_span_in_context(trace.INVALID_SPAN)
    linked = _span_context(trace_link)
    return root, ([Link(linked)] if linked is not None else [])
