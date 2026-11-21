"""One run of a conversation, as its read route answers it."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.agent.domain.entities import AgentRun
from app.modules.agent.domain.value_objects import AgentRunStatus, JsonValue
from app.modules.usage.contracts.run_usage import RunTokenUsage


class AgentRunResponse(BaseModel):
    """One run of a conversation: how it went, and the model tokens it used."""

    id: UUID
    conversation_id: UUID
    agent_id: UUID | None = None
    parent_run_id: UUID | None = None
    status: AgentRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    output: JsonValue | None = None
    usage: RunTokenUsage | None = Field(
        default=None,
        description=(
            "Model tokens the run used, summed over its model calls; null when "
            "none were recorded against it. A run still working reads what it "
            "has used so far. Leaves out the conversation's title and the "
            "memory kept after the run, which land later. Tokens only: no cost."
        ),
    )

    @classmethod
    def of(cls, run: AgentRun, usage: RunTokenUsage | None) -> AgentRunResponse:
        return cls(
            id=run.id,
            conversation_id=run.conversation_id,
            agent_id=run.agent_id,
            parent_run_id=run.parent_run_id,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error=run.error,
            output=run.output_data,
            usage=usage,
        )
