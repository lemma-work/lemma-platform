from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


# Areas, not kinds of defect. Someone who hit a bad error message knows it came
# from the CLI, but not whether to file it as a "tooling" or a "system" issue.
# These values were once SCREAMING_SNAKE kinds (SYSTEM_ISSUE, TOOLING_ISSUE, …)
# that no caller-facing surface named, while the CLI help and every skill
# example said cli/skill/platform/docs — so each documented example was rejected
# by the API. This is that vocabulary, made real. The docstring below is the
# published OpenAPI schema description; keep it about what callers should send.
class FeedbackCategory(str, Enum):
    """Which part of Lemma the report is about."""

    CLI = "cli"
    SKILL = "skill"
    PLATFORM = "platform"
    DOCS = "docs"
    OTHER = "other"


class ReportFeedbackRequest(BaseModel):
    """Request payload for maintainer feedback reports."""

    category: FeedbackCategory = Field(
        description=(
            "Which part of Lemma the report is about: 'cli' (the lemma "
            "command-line tool), 'skill' (a bundled skill's guidance), "
            "'platform' (the API, runtime, or product behavior), 'docs' "
            "(wrong or missing documentation), or 'other'."
        )
    )
    subject: str = Field(
        min_length=3,
        max_length=255,
        description="Short subject line summarizing the report.",
    )
    issue_encountered: str = Field(
        min_length=3,
        description="What issue, problem, or incorrect information was encountered.",
    )
    expected_behavior: str = Field(
        min_length=3,
        description="What the caller expected to happen instead.",
    )
    actual_behavior: str = Field(
        min_length=3,
        description="What actually happened.",
    )
    suggested_next_steps: str | None = Field(
        default=None,
        description="Optional proposed fixes, follow-ups, or next steps.",
    )


class ReportFeedbackResponse(BaseModel):
    """Response payload for maintainer feedback reports."""

    success: bool = Field(description="Whether the feedback was recorded successfully.")
    feedback_id: UUID | None = Field(
        default=None,
        description="Identifier of the created feedback report.",
    )
    user_id: UUID | None = Field(
        default=None,
        description="Authenticated user associated with the report.",
    )
    agent_id: UUID | None = Field(
        default=None,
        description="Delegated agent associated with the report, if available.",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable status message.",
    )
