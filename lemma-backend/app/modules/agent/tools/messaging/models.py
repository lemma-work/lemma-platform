from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import BaseToolResponse


class MessagePersonRequest(BaseModel):
    person: str = Field(
        description=(
            "Who to reach: a pod member's email address, or their name. Email is "
            "exact and unambiguous; a name only resolves when it matches exactly "
            "one member."
        )
    )
    message: str = Field(
        description=(
            "What to say, written for the person receiving it. They see only this "
            "text — not your reasoning, not the task you are working on, and not "
            "the conversation you are in. Say who you are and why you are asking."
        )
    )
    title: str | None = Field(
        default=None,
        description="Optional short subject line shown in their Lemma inbox.",
    )


class MessagePersonResponse(BaseToolResponse):
    delivered_via: str | None = Field(
        default=None,
        description=(
            "Where it landed: APP means only their Lemma inbox has it, so they "
            "will see it next time they look rather than being pinged."
        ),
    )
    conversation_id: str | None = Field(
        default=None,
        description="The conversation their reply will arrive in, if any.",
    )
