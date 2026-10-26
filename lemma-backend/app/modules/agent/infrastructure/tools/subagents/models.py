"""Request models for the sub-agent control toolset.

The surface is intentionally small: ``spawn`` to launch a child, ``interact``
to drive a running child (send/await/stop), and ``query`` to inspect children
(list/messages). Each multi-purpose tool selects behaviour via an action/mode
discriminator so the model sees three tools instead of six.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.agent.domain.value_objects import JsonObject

InteractAction = Literal["send", "await", "stop"]
QueryMode = Literal["list", "messages"]


class SpawnSubagentRequest(BaseModel):
    agent_name: str | None = Field(
        default=None,
        description=(
            "Name of a pod agent you may execute. Omit (or pass your own name) "
            "to spawn another instance of yourself for a targeted subtask."
        ),
    )
    input: JsonObject | str = Field(
        default_factory=dict,
        description=(
            "Task for the sub-agent as its first message: a plain instruction "
            "string (most common), or a JSON object of structured inputs."
        ),
    )


class InteractSubagentRequest(BaseModel):
    """Drive a running sub-agent you spawned."""

    conversation_id: str = Field(..., description="Sub-conversation id from spawn.")
    action: InteractAction = Field(
        ...,
        description=(
            "'send' a follow-up message (starts a new turn), 'await' a run to "
            "finish, or 'stop' a running sub-agent."
        ),
    )
    content: str | None = Field(
        default=None,
        description="Message to send. Required when action='send'.",
    )
    run_id: str | None = Field(
        default=None,
        description="Run id from spawn. Required when action='await'.",
    )
    timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=60.0,
        description=(
            "Max seconds to block when action='await' before returning a "
            "still-running handle. This is for a child that should finish "
            "almost immediately — blocking holds the parent's run open. For "
            "anything longer, end this call and use "
            "`wait_for(subagent_run_id=...)`, which costs nothing while it "
            "waits."
        ),
    )


class QuerySubagentsRequest(BaseModel):
    """Inspect the sub-agents you spawned."""

    mode: QueryMode = Field(
        ...,
        description=(
            "'list' the children you spawned (with latest run status), or read "
            "the latest 'messages' from one child conversation."
        ),
    )
    conversation_id: str | None = Field(
        default=None,
        description="Sub-conversation id. Required when mode='messages'.",
    )
    status: str | None = Field(
        default=None,
        description="mode='list' filter: 'ACTIVE' for only running children.",
    )
    after_sequence: int | None = Field(
        default=None,
        description="mode='messages': only return messages after this sequence.",
    )
    limit: int = Field(default=50, ge=1, le=200)
