"""Usage domain events."""

from __future__ import annotations

from uuid import UUID

from app.core.domain.events import DomainEvent


USAGE_EVENTS_STREAM = "usage_events"


class ModelUsageEvent(DomainEvent):
    """Emitted after one model usage record is persisted."""

    event_type: str = "usage.model.recorded"
    usage_id: UUID
    organization_id: UUID | None = None
    pod_id: UUID | None = None
    user_id: UUID
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    parent_agent_run_id: UUID | None = None
    source_type: str
    source_id: str | None = None
    profile_id: str
    profile_scope: str
    model_name: str
    usage_kind: str = "llm"
    input_tokens: int = 0
    output_tokens: int = 0
    units: float = 0.0
    cost_usd: float | None = None
    status: str | None = None
    metadata: dict[str, object] | None = None

    @classmethod
    def stream_name(cls) -> str:
        return USAGE_EVENTS_STREAM


class UsageLimitDeniedEvent(DomainEvent):
    """Emitted when a system-profile request is blocked by limits."""

    event_type: str = "usage.limit.denied"
    organization_id: UUID | None = None
    user_id: UUID
    profile_id: str
    model_name: str
    reason: str

    @classmethod
    def stream_name(cls) -> str:
        return USAGE_EVENTS_STREAM
