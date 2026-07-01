from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.platforms.common import SurfaceFileAttachment


class EmailToolResult(BaseModel):
    success: bool = False
    error: str | None = None
    message: str | None = None


class EmailFileAttachment(SurfaceFileAttachment):
    message_id: str | None = None
    content_bytes_base64: str | None = None
    is_inline: bool = False


class GmailFileAttachment(EmailFileAttachment):
    pass


class OutlookFileAttachment(EmailFileAttachment):
    content_id: str | None = None
    odata_type: str | None = None


class EmailReplyParams(BaseModel):
    content: str = Field(description="Email body to send.")
    content_type: Literal["text", "markdown", "html"] = Field(
        default="markdown",
        description=(
            "How to interpret the content body. Use markdown for normal agent-authored "
            "responses, html for pre-rendered HTML, or text for plain text only."
        ),
    )
    attachment_paths: list[str] = Field(
        default_factory=list,
        description="Optional relative workspace file paths to attach to the email reply.",
    )
    subject: str | None = Field(
        default=None,
        description="Optional subject override. Defaults to replying on the current thread subject.",
    )


class EmailReplyResult(EmailToolResult):
    thread_id: str | None = None
    message_id: str | None = None
    attachment_count: int = 0


class GmailReplyEmailParams(EmailReplyParams):
    pass


class GmailReplyEmailResult(EmailReplyResult):
    pass


class OutlookReplyEmailParams(EmailReplyParams):
    pass


class OutlookReplyEmailResult(EmailReplyResult):
    pass


class ResendReplyEmailParams(EmailReplyParams):
    pass


class ResendReplyEmailResult(EmailReplyResult):
    pass
