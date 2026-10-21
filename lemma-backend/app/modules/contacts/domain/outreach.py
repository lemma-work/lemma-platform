"""What the pod may send a contact, decided before anything is sent.

Every line that leaves Lemma for a contact -- a member writing to them, an
agent answering them, an acknowledgement -- is held to the same rules, in
this order, and the first that refuses decides:

1. **Opt-out first.** A handle that said STOP, unsubscribed or bounced is
   never written to, by anybody, whatever else holds. Nothing below is
   consulted for it -- not even whether anybody looks after the contact.
2. **A handle there.** The contact must have a handle of the kind the place
   reaches (an email address for email, a number for WhatsApp).
3. **The platform's window**, where the platform has one: a fact the caller
   supplies (``SendWindow``), because only the platform's adapter knows it.
4. **The contact's day** (member lines only, counted elsewhere: see
   ``services.outreach.count_against_today``).

Pushes to members never come here: they go to members, never to contacts.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.modules.contacts.domain.entities import ContactHandle
from app.modules.contacts.domain.handles import normalize_handle
from app.modules.contacts.domain.kinds import IdentityKind


class OutreachKind(StrEnum):
    """Who is speaking to the contact, which decides what is counted."""

    #: A member writing to them: counts against the contact's day.
    MEMBER_LINE = "member_line"
    #: An agent answering what they wrote.
    AGENT_ANSWER = "agent_answer"
    #: An automatic acknowledgement ("someone will reply").
    ACK = "ack"


class RefusalCode(StrEnum):
    SUPPRESSED = "suppressed"
    NO_HANDLE = "no_handle"
    OUTSIDE_WINDOW = "outside_window"
    RATE_LIMITED = "rate_limited"
    #: The rules could not be read (Redis away): try again, do not give up.
    UNAVAILABLE = "unavailable"


class Address(BaseModel):
    """Where a line goes: a kind of handle, and the place's own address there.

    ``value`` is the address the place names, when it names one: that handle
    decides. ``None`` lets the contact's most recently heard-from handle of
    the kind decide.
    """

    model_config = ConfigDict(frozen=True)

    kind: IdentityKind
    value: str | None = None


class SendWindow(BaseModel):
    """A platform's rule about when a business may write, as a fact.

    ``closes_at`` is when the place stops taking free-form messages; ``None``
    when it never opened. ``explanation`` is what the member reads when it is
    shut -- the platform's own reason, in the adapter's words.
    """

    model_config = ConfigDict(frozen=True)

    closes_at: datetime | None
    explanation: str


class Verdict(BaseModel):
    """Whether a line may go, and if so where and with what."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    code: RefusalCode | None = None
    #: The member-facing sentence, when refused.
    reason: str | None = None
    #: Worth asking again: the refusal is an outage, not a rule.
    retryable: bool = False
    #: The handle it goes to, when allowed.
    handle: ContactHandle | None = None
    #: Text the line carries at its end: email's "how to stop" line.
    footer: str = ""
    #: The signed token an email's ``List-Unsubscribe`` headers carry.
    unsubscribe_token: str | None = None

    @classmethod
    def refused(
        cls, code: RefusalCode, reason: str, *, retryable: bool = False
    ) -> Verdict:
        return cls(allowed=False, code=code, reason=reason, retryable=retryable)


SUPPRESSED_REASON = "The contact asked not to be written to there"
NO_HANDLE_REASON = "The contact has no handle on that channel"
RATE_LIMITED_REASON = "That is as many messages as one contact gets in a day"
UNAVAILABLE_REASON = (
    "Messages to contacts are unavailable right now. Try again shortly."
)


def decide(
    *,
    to: Address,
    handles: Sequence[ContactHandle],
    suppressed: Collection[str],
    at: datetime,
    window: SendWindow | None,
) -> Verdict:
    """The rules, in order, over facts already read.

    ``suppressed`` is the normalised values of this kind that are opted out,
    among the contact's handles and the place's own address: an address the
    contact no longer holds -- forgotten, merged away -- is still opted out if
    it said so. With no address named, a handle that opted out silences only
    itself: the line goes to their most recent other one.
    """
    mine = [handle for handle in handles if handle.kind is to.kind]
    if to.value is not None:
        address = normalize_handle(to.kind, to.value)
        if address in suppressed:
            return Verdict.refused(RefusalCode.SUPPRESSED, SUPPRESSED_REASON)
        there = [handle for handle in mine if handle.value == address]
        if there:
            mine = there
    live = [handle for handle in mine if handle.value not in suppressed]
    if mine and not live:
        return Verdict.refused(RefusalCode.SUPPRESSED, SUPPRESSED_REASON)
    if not live:
        return Verdict.refused(RefusalCode.NO_HANDLE, NO_HANDLE_REASON)
    if window is not None and (window.closes_at is None or at > window.closes_at):
        return Verdict.refused(RefusalCode.OUTSIDE_WINDOW, window.explanation)
    return Verdict(allowed=True, handle=_latest(live))


def _latest(handles: Sequence[ContactHandle]) -> ContactHandle:
    return max(
        handles,
        key=lambda handle: (
            handle.last_inbound_at or datetime.min.replace(tzinfo=timezone.utc)
        ),
    )
