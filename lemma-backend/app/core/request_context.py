"""Bounded observability context shared across async execution boundaries.

``request_id`` identifies an originating HTTP request, while
``correlation_id`` follows the complete logical operation.  Event and job
identifiers describe the boundary currently being executed.  Context is kept
in :mod:`contextvars`, so concurrent requests/tasks remain isolated and the
logging processor can attach the same fields to app and foreign records.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterator, Mapping
from contextlib import contextmanager
from contextvars import Context, ContextVar, Token
from dataclasses import dataclass
import re
from typing import Any, TypeVar
from uuid import UUID


_BOUNDED_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


_request_id: ContextVar[str | None] = ContextVar("lemma_request_id", default=None)
_correlation_id: ContextVar[UUID | None] = ContextVar(
    "lemma_correlation_id", default=None
)
_event_id: ContextVar[UUID | None] = ContextVar("lemma_event_id", default=None)
_event_causation_id: ContextVar[UUID | None] = ContextVar(
    "lemma_event_causation_id", default=None
)
_event_type: ContextVar[str | None] = ContextVar("lemma_event_type", default=None)
_consumer: ContextVar[str | None] = ContextVar("lemma_event_consumer", default=None)
_job_id: ContextVar[str | None] = ContextVar("lemma_job_id", default=None)
_task_name: ContextVar[str | None] = ContextVar("lemma_task_name", default=None)
_job_attempt: ContextVar[int | None] = ContextVar("lemma_job_attempt", default=None)


@dataclass(frozen=True, slots=True)
class ObservabilityContext:
    request_id: str | None = None
    correlation_id: UUID | None = None
    event_id: UUID | None = None
    causation_id: UUID | None = None
    event_type: str | None = None
    consumer: str | None = None
    job_id: str | None = None
    task_name: str | None = None
    job_attempt: int | None = None

    def as_log_fields(self) -> dict[str, str | int]:
        values: dict[str, str | int | None] = {
            "request_id": self.request_id,
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "event_id": str(self.event_id) if self.event_id else None,
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "event_type": self.event_type,
            "consumer": self.consumer,
            "job_id": self.job_id,
            "task_name": self.task_name,
            "job_attempt": self.job_attempt,
        }
        return {key: value for key, value in values.items() if value is not None}

    def as_transport(self) -> dict[str, str]:
        """Return the small JSON-safe envelope stored beside queued jobs."""
        return {key: str(value) for key, value in self.as_log_fields().items()}


def current_observability_context() -> ObservabilityContext:
    return ObservabilityContext(
        request_id=_request_id.get(),
        correlation_id=_correlation_id.get(),
        event_id=_event_id.get(),
        causation_id=_event_causation_id.get(),
        event_type=_event_type.get(),
        consumer=_consumer.get(),
        job_id=_job_id.get(),
        task_name=_task_name.get(),
        job_attempt=_job_attempt.get(),
    )


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str) -> Token[str | None]:
    """Compatibility setter used by older middleware/tests."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_correlation_id() -> UUID | None:
    return _correlation_id.get()


def get_event_id() -> UUID | None:
    return _event_id.get()


def get_causation_id() -> UUID | None:
    """Return the current event as the parent for a newly-created child event."""
    return _event_id.get()


@contextmanager
def bind_request_context(
    *, request_id: str, correlation_id: UUID
) -> Iterator[ObservabilityContext]:
    request_token = _request_id.set(request_id)
    correlation_token = _correlation_id.set(correlation_id)
    try:
        yield current_observability_context()
    finally:
        _correlation_id.reset(correlation_token)
        _request_id.reset(request_token)


@contextmanager
def event_lineage(
    *,
    correlation_id: UUID | None = None,
    event_id: UUID | None = None,
    causation_id: UUID | None = None,
    request_id: str | None = None,
    event_type: str | None = None,
    consumer: str | None = None,
) -> Iterator[ObservabilityContext]:
    """Bind an event while making it the parent of any emitted child events.

    ``causation_id`` is the parent of the *current* event and is therefore what
    appears on its logs.  ``get_causation_id()`` intentionally returns
    ``event_id`` so domain events created inside the handler point back to the
    event being processed.

    The ``event_id=None`` compatibility path treats ``causation_id`` as the
    current event ID, matching the pre-revamp call signature.
    """
    if event_id is None and causation_id is not None:
        event_id, causation_id = causation_id, None
    resolved_correlation_id = correlation_id or _correlation_id.get() or event_id
    if resolved_correlation_id is None:
        raise ValueError("event lineage requires an event or correlation id")

    tokens: list[tuple[ContextVar[Any], Token[Any]]] = []

    def set_value(var: ContextVar[Any], value: Any) -> None:
        tokens.append((var, var.set(value)))

    set_value(_correlation_id, resolved_correlation_id)
    set_value(_event_id, event_id)
    set_value(_event_causation_id, causation_id)
    set_value(_event_type, event_type)
    set_value(_consumer, consumer)
    if request_id is not None:
        set_value(_request_id, request_id)
    try:
        yield current_observability_context()
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


@contextmanager
def bind_job_context(
    *,
    job_id: str,
    task_name: str,
    attempt: int | None = None,
    inherited: Mapping[str, str] | None = None,
) -> Iterator[ObservabilityContext]:
    """Bind a queued job and its captured request/event lineage."""
    inherited = (
        current_observability_context().as_transport()
        if inherited is None
        else inherited
    )
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = []

    def set_value(var: ContextVar[Any], value: Any) -> None:
        tokens.append((var, var.set(value)))

    def parse_uuid(key: str) -> UUID | None:
        raw = inherited.get(key)
        if not raw:
            return None
        try:
            return UUID(raw)
        except ValueError:
            return None

    inherited_request_id = inherited.get("request_id")
    set_value(
        _request_id,
        inherited_request_id
        if inherited_request_id and _BOUNDED_IDENTIFIER_RE.fullmatch(inherited_request_id)
        else None,
    )
    set_value(_correlation_id, parse_uuid("correlation_id"))
    set_value(_event_id, parse_uuid("event_id"))
    set_value(_event_causation_id, parse_uuid("causation_id"))
    set_value(_event_type, inherited.get("event_type"))
    set_value(_consumer, inherited.get("consumer"))
    set_value(_job_id, job_id)
    set_value(_task_name, task_name)
    set_value(_job_attempt, attempt)
    try:
        yield current_observability_context()
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def correlation_headers() -> dict[str, str]:
    """Headers for trusted first-party HTTP calls only."""
    context = current_observability_context()
    headers: dict[str, str] = {}
    if context.request_id:
        headers["x-request-id"] = context.request_id
    if context.correlation_id:
        headers["x-lemma-correlation-id"] = str(context.correlation_id)
    if context.event_id:
        headers["x-lemma-event-id"] = str(context.event_id)
    if context.job_id:
        headers["x-lemma-job-id"] = context.job_id
    return headers


TaskResultT = TypeVar("TaskResultT")


def create_inherited_task(
    coroutine: Coroutine[Any, Any, TaskResultT], *, name: str | None = None
) -> asyncio.Task[TaskResultT]:
    """Spawn child work that deliberately belongs to the current operation."""
    return asyncio.create_task(coroutine, name=name)


def create_background_task(
    coroutine: Coroutine[Any, Any, TaskResultT], *, name: str | None = None
) -> asyncio.Task[TaskResultT]:
    """Spawn long-lived service work with a clean business/request context."""
    return asyncio.create_task(coroutine, name=name, context=Context())
