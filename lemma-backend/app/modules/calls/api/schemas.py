"""Request and response bodies of the calls routes."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.calls.config import VoiceEngineName


class OpenCallRequest(BaseModel):
    agent_name: str | None = Field(
        default=None,
        max_length=200,
        description="The pod agent to call; empty calls the pod's default agent.",
    )
    display_name: str = Field(
        default="your teammate",
        min_length=1,
        max_length=120,
        description="What the agent is called on the call.",
    )
    title: str | None = Field(
        default=None, max_length=200, description="The call conversation's title."
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
