"""Request/response models for the messaging toolset."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import BaseToolResponse

# The inbox line has to fit one row in a list and one subject line in an email.
MAX_TITLE_LENGTH = 120

# You can name at most this many notifications in one status check. A standup is
# four people; anything reaching for fifty is polling, which is the failure mode
# the prompt guidance exists to prevent.
MAX_STATUS_CHECK = 25


class MessageUserRequest(BaseModel):
    to: str = Field(
        description="Pod member id, user id, or email address."
    )
    message: str = Field(
        description="Delivered verbatim. Write it to them, not about them."
    )
    background_instruction: str | None = Field(
        default=None,
        description=(
            "Never shown to them; tells the agent handling their reply what "
            "counts as an answer and where to put it. Omit and nothing comes "
            "back to you."
        ),
    )
    title: str | None = Field(
        default=None,
        max_length=MAX_TITLE_LENGTH,
        description="Inbox label and email subject. Defaults to the message.",
    )
    expects_response: bool = Field(
        default=True, description="False for a pure FYI."
    )
    expires_in_seconds: int | None = Field(
        default=None, gt=0, description="Default 72h."
    )


class MessageUserResponse(BaseToolResponse):
    notification_id: UUID | None = Field(
        default=None, description="Pass to check_messages. Keep it."
    )
    delivery_status: str | None = Field(
        default=None,
        description=(
            "DELIVERED / UNDELIVERABLE (inbox only — not a failure) / FAILED."
        ),
    )
    delivered_via: str | None = Field(default=None)
    undeliverable_reason: str | None = Field(
        default=None, description="Worth repeating to whoever asked you to send."
    )
    interaction_fallback: bool = Field(
        default=False, description="This runtime cannot send; say so and carry on."
    )


class NotificationStatusReport(BaseModel):
    notification_id: UUID
    status: str = Field(
        description=(
            "OPEN (still waiting) / RESPONDED (read response_summary) / "
            "ACKNOWLEDGED / EXPIRED / CANCELLED."
        )
    )
    delivery_status: str
    recipient_user_id: UUID
    title: str
    response_summary: str | None = None
    response_data: dict | None = None
    responded_at: str | None = None


class CheckMessagesRequest(BaseModel):
    notification_ids: list[UUID] = Field(
        max_length=MAX_STATUS_CHECK,
        description="Ids from message_user.",
    )


class CheckMessagesResponse(BaseToolResponse):
    messages: list[NotificationStatusReport] = Field(default_factory=list)
    pending: int = Field(default=0, description="How many are still OPEN.")


class ListPodMembersRequest(BaseModel):
    search: str = Field(
        default="",
        description="Name or email fragment, e.g. 'priya'. Omit to list everyone.",
    )
    limit: int = Field(default=50, ge=1, le=200)


class PodMemberSummary(BaseModel):
    # Named `to` because that is the whole job: the model copies one value into
    # message_user without having to know which of three id kinds it holds.
    to: str = Field(description="Pass this verbatim as message_user's `to`.")
    name: str | None = None
    email: str | None = None
    role: str | None = None
    is_you: bool = Field(
        default=False, description="True for the person this run belongs to."
    )


class ListPodMembersResponse(BaseToolResponse):
    members: list[PodMemberSummary] = Field(default_factory=list)
    total_matched: int = Field(
        default=0, description="Matches found, which may exceed those returned."
    )
    truncated: bool = Field(
        default=False, description="True when narrowing `search` would help."
    )
