"""One outbound message the platform must receive exactly once.

Written in the transaction that produced its content -- a run's answer, a
held WhatsApp reply, a "please send that again" -- and sent by the destination
pump (``surface.reply``)::

    PENDING -> SENDING -> SENT
                       -> PENDING   (a transient refusal: not before Retry-After)
                       -> UNKNOWN   (the sender died mid-send: never sent again)
                       -> FAILED    (a permanent refusal, or attempts exhausted)

A row is claimed SENDING *before* the platform call, with a deadline
(``sending_until``). A pump that later finds it still SENDING past that
deadline cannot know whether the message reached the person, and marks it
UNKNOWN rather than risk saying it twice -- and says so in the conversation,
so the miss is visible rather than silent.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


#: Waits before each retry of a transient refusal, one per retry. A
#: ``Retry-After`` longer than the step is honoured instead. Carried over from
#: the owed-reply job: together they span an ordinary outage and stop well
#: short of an answer nobody still wants.
RETRY_DELAYS = (
    timedelta(seconds=30),
    timedelta(seconds=300),
    timedelta(seconds=1800),
)
#: The send and every retry of it.
MAX_SEND_ATTEMPTS = 1 + len(RETRY_DELAYS)

#: How long a claimed send may take before an observer of it reads the outcome
#: as lost. Above every adapter's own retry budget for one send.
SENDING_LEASE = timedelta(seconds=90)
#: How long the pump waits on one send before it calls the send a transient
#: failure and retries it. Below ``SENDING_LEASE`` with room to settle, so a
#: slow platform is retried rather than left SENDING until the row goes
#: UNKNOWN -- at the cost that a send which did arrive after all, only late,
#: is sent again.
SEND_TIMEOUT = timedelta(seconds=60)


class _Target(BaseModel):
    model_config = ConfigDict(frozen=True)


class ConversationTarget(_Target):
    """The surface thread a conversation is bound to, as it is when sent."""

    kind: Literal["conversation"] = "conversation"
    conversation_id: UUID


class InboundSenderTarget(_Target):
    """The person who sent one stored inbound message, outside any run.

    For a message that will never be answered -- its processing failed for
    good -- so the sender is told once, through the stranger-reply window.
    """

    kind: Literal["inbound_sender"] = "inbound_sender"
    inbound_message_id: UUID


class ReplyContextTarget(_Target):
    """A reply prepared outside any run: a sign-up link, a refusal, a nudge.

    Carries the prepared ``SurfaceReplyContext`` itself (the parsed event it
    answers, the text, the surface), because the person it answers has no
    conversation to resolve it from. Sent through the fallback path, which
    holds the stranger-reply window.
    """

    kind: Literal["reply_context"] = "reply_context"
    context: dict[str, Any]


DeliveryTarget = Annotated[
    ConversationTarget | InboundSenderTarget | ReplyContextTarget,
    Field(discriminator="kind"),
]
_TARGET: TypeAdapter[DeliveryTarget] = TypeAdapter(DeliveryTarget)


def parse_target(raw: object) -> DeliveryTarget:
    return _TARGET.validate_python(raw)


class DeliveryContent(BaseModel):
    """What is sent. ``plain`` skips agent formatting (a held message)."""

    text: str
    #: Extra platform metadata for the send (a retry control on a failure notice).
    metadata: dict[str, object] = Field(default_factory=dict)
    #: The run whose held display files ride this message, if any.
    attach_files_of_run: UUID | None = None
    plain: bool = False
    #: ``answer`` | ``failure`` | ``held`` | ``resend`` | ``notice``: for logs.
    purpose: str = "answer"


__all__ = [
    "MAX_SEND_ATTEMPTS",
    "RETRY_DELAYS",
    "SENDING_LEASE",
    "SEND_TIMEOUT",
    "ConversationTarget",
    "DeliveryContent",
    "DeliveryStatus",
    "DeliveryTarget",
    "InboundSenderTarget",
    "ReplyContextTarget",
    "parse_target",
]
