"""Minimal W3C trace-context handoff for the isolated function runtime.

The runtime image deliberately does not carry an OpenTelemetry SDK. It only
needs to retain the backend's sampled parent across its inherited execution
task and forward that parent on gateway callbacks.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import re
from typing import Iterator


_TRACEPARENT_RE = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)
_traceparent: ContextVar[str | None] = ContextVar(
    "function_runtime_traceparent",
    default=None,
)


def validated_traceparent(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    match = _TRACEPARENT_RE.fullmatch(normalized)
    if match is None:
        return None
    trace_id, span_id, _flags = match.groups()
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return normalized


@contextmanager
def bind_traceparent(value: str | None) -> Iterator[None]:
    token = _traceparent.set(validated_traceparent(value))
    try:
        yield
    finally:
        _traceparent.reset(token)


def current_trace_headers() -> dict[str, str]:
    value = _traceparent.get()
    return {"traceparent": value} if value is not None else {}
