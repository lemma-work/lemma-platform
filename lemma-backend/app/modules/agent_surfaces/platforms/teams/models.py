from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.platforms.common import SurfaceFileAttachment


class TeamsToolResult(BaseModel):
    success: bool = False
    error: str | None = None
    message: str | None = None


class TeamsFileAttachment(SurfaceFileAttachment):
    pass


class TeamsMessageAttachmentSnapshot(TeamsFileAttachment):
    download_url: str


class TeamsChannelMessageSnapshot(BaseModel):
    message_id: str | None = None
    reply_to_id: str | None = None
    user_id: str | None = None
    display_name: str | None = None
    #: Sent by an application (a bot, ours included), not a person.
    from_application: bool = False
    # Author attribution making clear this is another channel participant, not
    # the user who mentioned the agent. Background-context framing only.
    author_label: str | None = None
    text: str
    attachments: list[TeamsMessageAttachmentSnapshot] = Field(default_factory=list)


class TeamsGetRecentMessagesParams(BaseModel):
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of recent channel messages to return.",
    )
    scope: Literal["auto", "channel", "thread"] = Field(
        default="auto",
        description=(
            "Whether to read from the full current channel or the current thread. "
            "Use 'auto' to pick the current thread when the conversation is thread-based."
        ),
    )


class TeamsGetRecentMessagesResult(TeamsToolResult):
    messages: list[TeamsChannelMessageSnapshot] = Field(default_factory=list)
