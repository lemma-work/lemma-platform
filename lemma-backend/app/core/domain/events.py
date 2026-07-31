from __future__ import annotations

from uuid import UUID, uuid7
from datetime import datetime, timezone
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
from pydantic import BaseModel, Field, model_validator

from app.core.request_context import (
    get_causation_id,
    get_correlation_id,
    get_request_id,
)


class DomainEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid7)
    event_type: str
    schema_version: int = 1
    producer: str = "lemma-backend"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    request_id: str | None = Field(default_factory=get_request_id)
    traceparent: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    tracestate: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def populate_event_lineage(self) -> "DomainEvent":
        """Give roots a correlation id and inherit lineage inside consumers."""
        if self.correlation_id is None:
            self.correlation_id = get_correlation_id() or self.event_id
        if self.causation_id is None:
            self.causation_id = get_causation_id()
        carrier: dict[str, str] = {}
        if self.traceparent:
            carrier["traceparent"] = self.traceparent
            if self.tracestate:
                carrier["tracestate"] = self.tracestate
            extracted = trace.get_current_span(
                extract(carrier, context=Context())
            ).get_span_context()
            if not extracted.is_valid:
                self.traceparent = None
                self.tracestate = None
        else:
            inject(carrier)
            self.traceparent = carrier.get("traceparent")
            self.tracestate = carrier.get("tracestate")
        return self

    @classmethod
    def get_event_type(cls) -> str:
        """Return the default event_type value for this event class.

        Pydantic v2 does not expose fields with defaults as class attributes,
        so ``MyEvent.event_type`` raises ``AttributeError``.  Use this
        classmethod instead when comparing against an incoming event dict.
        """
        return cls.model_fields["event_type"].default

    @classmethod
    def stream_name(cls) -> str:
        """Return the stream name for publishing this event.

        Override in subclasses to specify the stream.
        """
        raise NotImplementedError("Subclasses must define stream_name()")


class RawWebhookReceivedEvent(DomainEvent):
    event_type: str = "webhook.received"
    source: str
    payload: dict[str, Any]
    headers: dict[str, str] | None = None
    surface_id: UUID | None = None

    @classmethod
    def stream_name(cls) -> str:
        return "webhook_events"
