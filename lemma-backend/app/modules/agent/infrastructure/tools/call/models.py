"""What the call tools take."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.agent.infrastructure.tools.responses import BaseToolResponse

Urgency = Literal["context", "when_idle", "now"]


class RelayToVoiceRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "What the voice should know or say, as notes, not a script: one or "
            "two spoken sentences' worth, in English. Detail goes on the canvas."
        ),
    )
    urgency: Urgency = Field(
        default="when_idle",
        description=(
            "context: known, not said now. when_idle: said at the next pause. "
            "now: said once the voice finishes its sentence and the caller is "
            "not talking -- failures, questions, and the answer they wait on."
        ),
    )


class RelayToVoiceResponse(BaseToolResponse):
    pass
