"""``agent_pauses``: every way a run waits, one row per pausing tool call.

It replaced three sources of truth for one thing: the open pausing call found
by its tool name, ``agent_approval_decisions`` (what a person decided) and
``agent_conversation_waits`` (a ``wait_for``). Those two tables are kept one
release and no longer written.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import (
    Enum as SQLEnum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.agent.domain.pause_opening import (
    AgentHostPermissionRequest,
    BudgetSpent,
)
from app.modules.agent.domain.pauses import Pause, PauseResolution
from app.modules.agent.domain.state_kinds import PauseKind, PauseStatus
from app.modules.agent.domain.wait import AgentWaitType, WaitSpec
from app.modules.agent.infrastructure.models.typed_state import (
    enum_column,
    json_column,
    timestamp_column,
)


class AgentPauseModel(UUIDAuditBase):
    """One pause. At most one PENDING wait per conversation (the partial index):
    a run waits on one thing at a time, and a duplicate wake cannot add a second.
    """

    __tablename__ = "agent_pauses"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "tool_call_id", name="uq_agent_pause_tool_call"
        ),
        Index("ix_agent_pause_conversation_status", "conversation_id", "status"),
        Index(
            "uq_agent_pause_one_pending_wait",
            "conversation_id",
            unique=True,
            postgresql_where=text("kind = 'WAIT' AND status = 'PENDING'"),
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[PauseKind] = mapped_column(
        SQLEnum(PauseKind, native_enum=False, length=32), nullable=False
    )
    status: Mapped[PauseStatus] = enum_column(
        PauseStatus, length=32, default=PauseStatus.PENDING
    )
    deadline: Mapped[datetime | None] = timestamp_column()
    resolution: Mapped[dict[str, object] | None] = json_column()
    host_permission: Mapped[dict[str, object] | None] = json_column()
    budget: Mapped[dict[str, object] | None] = json_column()
    wait_type: Mapped[AgentWaitType | None] = enum_column(AgentWaitType, length=30)
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    wake_at: Mapped[datetime | None] = timestamp_column()
    wait_spec: Mapped[dict[str, object] | None] = json_column()
    execution_claimed_at: Mapped[datetime | None] = timestamp_column()
    resolved_at: Mapped[datetime | None] = timestamp_column()

    @classmethod
    def values_of(cls, pause: Pause) -> dict[str, object]:
        """The columns a pause writes, as an INSERT or a fenced UPDATE sets them."""
        return {
            "status": pause.status,
            "deadline": pause.deadline,
            "resolution": _json(pause.resolution),
            "host_permission": _json(pause.host_permission),
            "budget": _json(pause.budget),
            "wait_type": pause.wait_type,
            "external_ref": pause.external_ref,
            "wake_at": pause.wake_at,
            "wait_spec": _json(pause.wait_spec),
            "execution_claimed_at": pause.execution_claimed_at,
            "resolved_at": pause.resolved_at,
        }

    @classmethod
    def from_entity(cls, pause: Pause) -> AgentPauseModel:
        return cls(
            id=pause.id,
            created_at=pause.created_at,
            updated_at=pause.updated_at,
            conversation_id=pause.conversation_id,
            run_id=pause.run_id,
            pod_id=pause.pod_id,
            tool_call_id=pause.tool_call_id,
            tool_name=pause.tool_name,
            kind=pause.kind,
            **cls.values_of(pause),
        )

    def to_entity(self) -> Pause:
        return Pause(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            conversation_id=self.conversation_id,
            run_id=self.run_id,
            pod_id=self.pod_id,
            tool_call_id=self.tool_call_id,
            tool_name=self.tool_name,
            kind=self.kind,
            status=self.status,
            deadline=self.deadline,
            resolution=_parsed(PauseResolution, self.resolution),
            host_permission=_parsed(AgentHostPermissionRequest, self.host_permission),
            budget=_parsed(BudgetSpent, self.budget),
            wait_type=self.wait_type,
            external_ref=self.external_ref,
            wake_at=self.wake_at,
            wait_spec=_parsed(WaitSpec, self.wait_spec),
            execution_claimed_at=self.execution_claimed_at,
            resolved_at=self.resolved_at,
        )


def _json(value: BaseModel | None) -> dict[str, object] | None:
    return value.model_dump(mode="json") if value is not None else None


def _parsed[T: BaseModel](kind: type[T], raw: object) -> T | None:
    return kind.model_validate(raw) if isinstance(raw, dict) else None


__all__ = ["AgentPauseModel"]
