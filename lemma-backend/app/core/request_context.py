"""Request and event lineage context shared across async boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID


_request_id: ContextVar[str | None] = ContextVar("lemma_request_id", default=None)
_correlation_id: ContextVar[UUID | None] = ContextVar(
    "lemma_event_correlation_id", default=None
)
_causation_id: ContextVar[UUID | None] = ContextVar(
    "lemma_event_causation_id", default=None
)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_correlation_id() -> UUID | None:
    return _correlation_id.get()


def get_causation_id() -> UUID | None:
    return _causation_id.get()


@contextmanager
def event_lineage(*, correlation_id: UUID, causation_id: UUID) -> Iterator[None]:
    """Make an inbound event the parent of events emitted by its handler."""
    correlation_token = _correlation_id.set(correlation_id)
    causation_token = _causation_id.set(causation_id)
    try:
        yield
    finally:
        _causation_id.reset(causation_token)
        _correlation_id.reset(correlation_token)
