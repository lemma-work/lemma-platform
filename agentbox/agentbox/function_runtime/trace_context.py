"""W3C trace-context handoff for the isolated function runtime.

The runtime does not export spans. It uses the standard OpenTelemetry
propagator API only to retain an inbound parent across its execution task and
inject that parent into gateway callbacks.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager

from opentelemetry.context import attach, detach
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)


_PROPAGATOR = TraceContextTextMapPropagator()


@contextmanager
def bind_trace_context(carrier: Mapping[str, str]) -> Iterator[None]:
    token = attach(_PROPAGATOR.extract(carrier=carrier))
    try:
        yield
    finally:
        detach(token)


def inject_trace_context(carrier: MutableMapping[str, str]) -> None:
    _PROPAGATOR.inject(carrier=carrier)
