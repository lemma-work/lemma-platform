"""A send of a line outside Lemma, as the channels module and its senders read it.

Kept beside the other entities (``entities`` re-exports them) for size.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    #: Did not go; will be tried again.
    FAILED = "FAILED"
    #: Not sent, on purpose: a rule said not to (opt-out, a window, a cap).
    SKIPPED = "SKIPPED"
    #: Tried as often as it will be.
    DEAD = "DEAD"


class DeliveryReasonCode(StrEnum):
    """Why a delivery is ``SKIPPED``, ``FAILED`` or ``DEAD``, as code reads it.

    ``Delivery.reason`` is the sentence a member reads beside it and may be
    reworded at any time; anything that decides by why a delivery stands where
    it does matches on this instead.
    """

    # -- SKIPPED: not sent, on purpose ------------------------------------------
    #: The line was deleted, or is gone, before it went.
    LINE_GONE = "LINE_GONE"
    #: A note for the pod's members goes nowhere outside.
    MEMBERS_ONLY = "MEMBERS_ONLY"
    #: The place is no longer the channel's, or its bot was removed.
    PLACE_GONE = "PLACE_GONE"
    #: Nothing there to send into, or nothing left once it was cleaned up.
    NOTHING_TO_SEND = "NOTHING_TO_SEND"
    #: Nobody there has spoken to the bot, and it cannot write first.
    CANNOT_WRITE_FIRST = "CANNOT_WRITE_FIRST"
    #: A contact's rules: they opted out, have no handle there, the
    #: platform's window is shut, or their day is spent.
    SUPPRESSED = "SUPPRESSED"
    NO_HANDLE = "NO_HANDLE"
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
    RATE_LIMITED = "RATE_LIMITED"
    #: The contact was forgotten before their line went.
    FORGOTTEN = "FORGOTTEN"
    #: A push with nowhere to reach the member, or a member no longer in the pod.
    UNREACHABLE = "UNREACHABLE"
    #: A push to a member who had the app open, and read it there.
    IN_APP = "IN_APP"
    #: A push held by the member's hourly budget: its lines go in their next
    #: push of the channel (``channel_push_pending``).
    FOLDED = "FOLDED"
    # -- FAILED: tried again after a backoff --------------------------------------
    #: The platform could not take it just now, or our side could not get it
    #: ready (a contact's rules unreadable, an error before the send).
    TRANSIENT = "TRANSIENT"
    #: No answer within the send's bound, before a platform was called.
    TIMED_OUT = "TIMED_OUT"
    # -- DEAD: given up -------------------------------------------------------------
    #: Part of a long message arrived: sending it again would repeat that part.
    PARTIAL = "PARTIAL"
    #: The platform never answered, so it may be there: not sent again.
    MAY_HAVE_ARRIVED = "MAY_HAVE_ARRIVED"
    #: The platform refused it; again would meet the same refusal.
    REFUSED = "REFUSED"
    #: The sender died mid-send, without saying what happened.
    INTERRUPTED = "INTERRUPTED"
    #: The platform took it and reported later that it never arrived.
    UNDELIVERED = "UNDELIVERED"


class Delivery(BaseModel):
    """One send of a line outside Lemma, and what became of it."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    message_id: UUID
    pod_id: UUID
    channel_id: UUID
    #: The line's place in its channel: a target's sends go in this order.
    seq: int
    source_id: UUID | None = None
    recipient_user_id: UUID | None = None
    platform: str | None = None
    status: DeliveryStatus
    #: Why, for code; ``reason`` is the same in words a member reads.
    reason_code: DeliveryReasonCode | None = None
    reason: str | None = None
    #: A push: the first of the lines it stands for (``seq`` is the line it
    #: leads with), how many of them the member hears, and whether one names
    #: or asks them. ``None`` / 1 / ``False`` for a place.
    from_seq: int | None = None
    lines: int = 1
    urgent: bool = False
    external_message_id: str | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None
    #: While a sender holds it (``claim``); cleared with the outcome.
    claimed_at: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def target_key(self) -> str:
        """Where it goes, as one key: one place's (or one member's) sends run
        one at a time, in the order their lines were written."""
        if self.source_id is not None:
            return f"place:{self.source_id}"
        return f"member:{self.recipient_user_id}"
