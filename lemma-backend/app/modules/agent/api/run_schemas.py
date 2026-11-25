"""One run of a conversation, and what a conversation has cost, as their read
routes answer them."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.agent.application.conversations.read import ConversationLedger
from app.modules.agent.domain.entities import AgentRun
from app.modules.agent.domain.value_objects import AgentRunStatus, JsonValue
from app.modules.usage.contracts.run_usage import LedgerTotals, RunTokenUsage


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
            "memory kept after the run, which land later. Tokens only: no cost "
            "(the conversation's usage route has it)."
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


class RunUsageResponse(BaseModel):
    """One run of a conversation and what it cost."""

    agent_run_id: UUID
    status: AgentRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    usage: LedgerTotals | None = Field(
        default=None,
        description=(
            "What the run cost and the tokens it used; null when nothing was "
            "recorded against it, which is not the same as nothing spent. A run "
            "on an Agent Host is recorded only if its harness reports usage, "
            "and only as each turn ends. A run still working reads what has "
            "been recorded so far."
        ),
    )


class ConversationUsageResponse(BaseModel):
    """What one conversation has cost its owner so far."""

    conversation_id: UUID
    totals: LedgerTotals = Field(
        description=(
            "Summed over every run of the conversation, the title and the "
            "memory kept after a run included. A sub-agent's conversation is "
            "its own: list them with `parent_id` and read each one's usage."
        )
    )
    run_count: int = Field(description="Every run of the conversation.")
    unreported_run_count: int = Field(
        description=(
            "Runs with nothing recorded against them. Above zero, `totals` "
            "leaves those runs out: show the total as incomplete, not as "
            "everything spent."
        )
    )
    runs: list[RunUsageResponse] = Field(
        description="The newest runs, newest first, each with its own usage."
    )
    has_more_runs: bool = Field(
        description="Whether older runs exist than `runs` lists; `totals` has them."
    )

    @classmethod
    def of(
        cls, conversation_id: UUID, ledger: ConversationLedger
    ) -> ConversationUsageResponse:
        return cls(
            conversation_id=conversation_id,
            totals=ledger.totals,
            run_count=ledger.run_count,
            # The two counts are read one after the other: a run started in
            # between is counted, and cannot have been reported yet.
            unreported_run_count=max(ledger.run_count - ledger.reported_run_count, 0),
            runs=[
                RunUsageResponse(
                    agent_run_id=window.id,
                    status=window.status,
                    started_at=window.started_at,
                    finished_at=window.finished_at,
                    usage=ledger.by_run.get(window.id),
                )
                for window in ledger.windows
            ],
            has_more_runs=ledger.run_count > len(ledger.windows),
        )
