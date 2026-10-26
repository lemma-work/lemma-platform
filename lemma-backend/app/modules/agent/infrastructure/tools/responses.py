"""The response shape every tool's result model shares."""

from __future__ import annotations

from pydantic import BaseModel, Field

TOOL_COMMENT_DESC = "One-line statement of intent, shown to the user."


class BaseToolResponse(BaseModel):
    """Base class for tool responses."""

    success: bool = Field(
        default=False,
        description="Whether the tool completed successfully.",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable error details when the tool fails.",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable status or follow-up information for the tool call.",
    )
