"""Request and response bodies of the calls routes."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.calls.config import VoiceEngineName


class OpenCallRequest(BaseModel):
    conversation_id: UUID | None = Field(
        default=None,
        description=(
            "Hold the call in this chat -- your own, in this pod -- so the call, "
            "the agent's work and the transcript are that one chat; a call "
            "already held there is picked up again. Empty starts a new "
            "conversation."
        ),
    )
    agent_name: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "The pod agent to call in a new conversation; empty calls the pod's "
            "default agent. A call in an existing chat is with that chat's agent."
        ),
    )
    display_name: str = Field(
        default="your teammate",
        min_length=1,
        max_length=120,
        description="What the agent is called in a new call's title.",
    )
    title: str | None = Field(
        default=None,
        max_length=200,
        description="A new call conversation's title; an existing chat keeps its own.",
    )


class CallResponse(BaseModel):
    call_id: UUID = Field(description="The call, which is also its conversation.")
    engines: list[VoiceEngineName] = Field(
        description="The voice engines this server can open, the default first."
    )


class CallConfigResponse(BaseModel):
    engines: list[VoiceEngineName] = Field(
        description="The voice engines this server can open, the default first."
    )
