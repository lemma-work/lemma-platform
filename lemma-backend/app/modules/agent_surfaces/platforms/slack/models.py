from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.domain.models import (
    APPROVAL_DECISION_APPROVE,
    APPROVAL_DECISION_DENY,
    APPROVAL_DECISION_SESSION,
)
from app.modules.agent_surfaces.platforms.common import SurfaceFileAttachment

# action_id of the Submit button on a native Slack form; the inbound
# block_actions parser matches on this to recognise a form submission.
SLACK_FORM_SUBMIT_ACTION_ID = "lemma_form_submit"

# action_id per approval decision on a native Slack approval prompt. The inbound
# block_actions parser matches these to recover the decision (the button's
# ``value`` carries the callback id). Kept as a decision→action_id map so the
# render and parse sides stay in sync.
SLACK_APPROVAL_ACTION_ID_BY_DECISION = {
    APPROVAL_DECISION_APPROVE: "lemma_approval_approve",
    APPROVAL_DECISION_DENY: "lemma_approval_deny",
    APPROVAL_DECISION_SESSION: "lemma_approval_session",
}
SLACK_APPROVAL_DECISION_BY_ACTION_ID = {
    action_id: decision
    for decision, action_id in SLACK_APPROVAL_ACTION_ID_BY_DECISION.items()
}


class SlackToolResult(BaseModel):
    success: bool = False
    error: str | None = None
    message: str | None = None


class SlackFileAttachment(SurfaceFileAttachment):
    pass


class SlackChannelMessageSnapshot(BaseModel):
    message_id: str | None = None
    user: str | None = None
    display_name: str | None = None
    # Author attribution making clear this is another channel participant, not
    # the user who mentioned the agent. Background-context framing only.
    author_label: str | None = None
    text: str
    thread_ts: str | None = None
    attachments: list[SlackFileAttachment] = Field(default_factory=list)


class SlackRecentChannelMessagesParams(BaseModel):
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of recent channel messages to return.",
    )
    include_current_thread: bool = Field(
        default=False,
        description=(
            "When false, exclude messages from the active thread and only inspect "
            "surrounding channel history."
        ),
    )


class SlackRecentChannelMessagesResult(SlackToolResult):
    messages: list[SlackChannelMessageSnapshot] = Field(default_factory=list)


class SlackSearchChannelMessagesParams(BaseModel):
    query: str = Field(description="Case-insensitive text query to search for.")
    limit: int = Field(
        default=10,
        ge=1,
        le=25,
        description="Maximum number of matches to return.",
    )
    scan_limit: int = Field(
        default=100,
        ge=10,
        le=500,
        description="Maximum number of recent channel messages to scan while searching.",
    )
    include_current_thread: bool = Field(
        default=False,
        description=(
            "When false, search surrounding channel history and exclude the active thread."
        ),
    )


class SlackSearchChannelMessagesResult(SlackToolResult):
    matches: list[SlackChannelMessageSnapshot] = Field(default_factory=list)
