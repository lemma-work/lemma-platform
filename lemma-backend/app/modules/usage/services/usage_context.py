"""Current agent usage context."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from app.modules.usage.domain.accounting import OUTSIDE_AUDIENCE_SOURCES


@dataclass(slots=True)
class UsageExecutionContext:
    user_id: UUID
    organization_id: UUID | None
    pod_id: UUID | None
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    parent_agent_run_id: UUID | None = None
    source_type: str = "agent_run"
    source_id: str | None = None
    workload_type: str | None = None
    workload_id: UUID | None = None
    #: The outside-audience source (``CONTACT_RUN`` or ``OUTSIDER_RUN``) of an
    #: execution answering somebody outside the organization, else ``None``.
    #: Apart from ``source_type`` because work done for such a run -- a vision
    #: delegate, history compaction, a title -- names its own source, and that
    #: must not turn the spend back into the allowance of the member who looks
    #: after the conversation. See ``MeteringScope.recorded_source``.
    outside_audience: str | None = None

    def __post_init__(self) -> None:
        if self.outside_audience is None and self.source_type in (
            OUTSIDE_AUDIENCE_SOURCES
        ):
            self.outside_audience = self.source_type
        if (
            self.outside_audience is not None
            and self.outside_audience not in OUTSIDE_AUDIENCE_SOURCES
        ):
            raise ValueError(f"Not an outside audience: {self.outside_audience!r}")


_current_usage_context: ContextVar[UsageExecutionContext | None] = ContextVar(
    "usage_execution_context",
    default=None,
)


def current_usage_context() -> UsageExecutionContext | None:
    return _current_usage_context.get()


@contextmanager
def usage_execution_context(ctx: UsageExecutionContext):
    token = _current_usage_context.set(ctx)
    try:
        yield ctx
    finally:
        _current_usage_context.reset(token)


def usage_context_from_agent_context(
    ctx,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    parent_agent_run_id: UUID | None = None,
) -> UsageExecutionContext:
    return UsageExecutionContext(
        user_id=ctx.user_id,
        organization_id=getattr(ctx, "org_id", None),
        pod_id=getattr(ctx, "pod_id", None),
        agent_id=(
            getattr(ctx, "workload_id", None)
            if getattr(ctx, "workload_type", None) in {None, "agent", "agent_tool"}
            else None
        ),
        conversation_id=getattr(ctx, "conversation_id", None),
        agent_run_id=getattr(ctx, "agent_run_id", None),
        parent_agent_run_id=parent_agent_run_id,
        source_type=source_type or getattr(ctx, "workload_type", None) or "agent_run",
        source_id=source_id or str(getattr(ctx, "agent_run_id", "") or ""),
        workload_type=getattr(ctx, "workload_type", None),
        workload_id=getattr(ctx, "workload_id", None),
    )
