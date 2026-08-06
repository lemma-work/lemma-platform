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
        description=(
            "Who to reach: their pod member id, user id, or email address. They "
            "must be a member of this pod."
        )
    )
    message: str = Field(
        description=(
            "What they read, verbatim. Write it to them, not about them — they "
            "see it on Slack or Telegram like any other message. Say who needs "
            "what, and by when if that matters."
        )
    )
    background_instruction: str | None = Field(
        default=None,
        description=(
            "NEVER shown to them. Instructions for the agent that handles their "
            "reply: what counts as an answer, and where to put it. Be concrete — "
            "'record their status update as the response summary', or 'write the "
            "PO number into the purchase_orders table, column po_number'. Without "
            "this their reply is just a chat message and nothing comes back."
        ),
    )
    title: str | None = Field(
        default=None,
        max_length=MAX_TITLE_LENGTH,
        description=(
            "Short label for their inbox and the email subject. Defaults to a "
            "truncated message."
        ),
    )
    expects_response: bool = Field(
        default=True,
        description=(
            "False for pure FYI. It changes what they are offered in the app: a "
            "Respond box, or just Dismiss."
        ),
    )
    expires_in_seconds: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Give up waiting after this long. Defaults to 72 hours, which is "
            "deliberately generous — people are asleep, on leave, in meetings."
        ),
    )


class MessageUserResponse(BaseToolResponse):
    notification_id: UUID | None = Field(
        default=None,
        description=(
            "Pass this to check_messages to find out whether they answered. Keep "
            "it — there is no other way to look this message up later."
        ),
    )
    delivery_status: str | None = Field(
        default=None,
        description=(
            "DELIVERED: it reached a chat app or mailbox. UNDELIVERABLE: no "
            "channel could carry it, but it IS in their Lemma inbox, so this is "
            "not a failure. FAILED: a channel was tried and errored."
        ),
    )
    delivered_via: str | None = Field(
        default=None, description="Which platform took it, when one did."
    )
    undeliverable_reason: str | None = Field(
        default=None,
        description=(
            "Why no channel worked, in words worth repeating to whoever asked "
            "you to send it."
        ),
    )
    interaction_fallback: bool = Field(
        default=False,
        description="True when this runtime cannot send; say so and carry on.",
    )


class NotificationStatusReport(BaseModel):
    notification_id: UUID
    status: str = Field(
        description=(
            "OPEN: still waiting. RESPONDED: they answered — read "
            "response_summary. ACKNOWLEDGED: seen, nothing owed. EXPIRED: the "
            "deadline passed. CANCELLED: withdrawn."
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
        description="The ids message_user gave you, for the messages you are waiting on.",
    )


class CheckMessagesResponse(BaseToolResponse):
    messages: list[NotificationStatusReport] = Field(default_factory=list)
    pending: int = Field(
        default=0, description="How many are still OPEN — nobody has answered those."
    )
