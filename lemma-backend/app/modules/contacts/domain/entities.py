"""A contact: somebody a pod knows who is not a member.

A contact never signs in, never joins the pod and never holds a grant. What
makes them a contact rather than an outsider is a handle something trustworthy
vouched for -- a WhatsApp number in a payload Meta signed, an email address the
receiving mail service authenticated -- and that handle is all the pod keeps
about who they are.

How a handle was vouched for is kept with it (``IdentityStrength``), because
the things a contact may do will depend on it: reading their own order status
needs less than changing a payout account.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IdentityKind(StrEnum):
    """What sort of handle names a contact."""

    PHONE = "PHONE"
    EMAIL = "EMAIL"
    TELEGRAM = "TELEGRAM"
    #: A signed-in user of the customer's own product, as named in a token
    #: their server signed: ``host:{their user id}``. Keyed by the pod, not the
    #: widget, so the same customer reached through another of the pod's
    #: widgets -- or a widget made again -- is still the same contact.
    HOST = "HOST"
    #: Somebody from another company in a Slack channel shared with it:
    #: ``{their team id}:{their user id}``, as Slack names them in a signed event.
    SLACK = "SLACK"
    #: Somebody from another tenant in a Teams chat: ``{tenant id}:{aad id}``.
    TEAMS = "TEAMS"
    #: A browser, by the web chat session it holds: the session's id. Proves
    #: only that the same browser came back -- see ``IdentityStrength.SESSION``.
    WEB = "WEB"


class IdentityStrength(StrEnum):
    """Who vouched for a handle.

    ``CHANNEL``: the platform the message came through, in a payload whose
    signature was checked (WhatsApp, Telegram), or the receiving mail service's
    authentication verdict (email). ``HOST`` and ``CODE``: see their values.
    ``MEMBER``: a pod member added it by hand,
    which says who the member believes it is and nothing about who writes from
    it -- so it never makes a message count as that contact on its own.
    """

    CHANNEL = "CHANNEL"
    MEMBER = "MEMBER"
    #: The customer's server, in a token signed with a web widget's secret.
    HOST = "HOST"
    #: The person entered a one-time code sent to the handle.
    CODE = "CODE"
    #: A browser holds the web chat session's secret. Says the same browser came
    #: back and nothing about who uses it, so it never proves another handle.
    SESSION = "SESSION"


#: The strengths that prove a person holds a handle, and so may join it to the
#: contact they already are -- or merge two contacts into one. A member typing
#: a handle in, or a browser holding a session, proves nothing of the kind.
PROVING_STRENGTHS = frozenset(
    {IdentityStrength.CHANNEL, IdentityStrength.HOST, IdentityStrength.CODE}
)


_NOT_DIGITS = re.compile(r"\D")


def normalize_handle(kind: IdentityKind, value: str) -> str:
    """The one spelling a handle is stored and looked up under.

    A phone number keeps its digits only, which is how WhatsApp names a sender
    (``wa_id``) and what makes ``+44 7700 900123`` and ``447700900123`` one
    person. An email address is case-folded: providers treat the local part as
    case-insensitive in practice, and two contacts for one inbox would split a
    customer's history in two.
    """
    cleaned = value.strip()
    if kind is IdentityKind.PHONE:
        return _NOT_DIGITS.sub("", cleaned)
    if kind is IdentityKind.EMAIL:
        return cleaned.lower()
    return cleaned


#: What a contact writes to stop hearing from the pod, as carriers and mail
#: senders have long taught people to: ``STOP`` on a text channel, and the
#: words people use for the same thing by email.
STOP_WORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "OPTOUT", "OPT OUT"})


def is_stop_request(text: str) -> bool:
    """Whether a whole message is a request to stop, and nothing else.

    The whole message only: "stop sending me the wrong size" is a question for
    the bot, not an unsubscribe. Trailing punctuation is forgiven, case and
    surrounding space are ignored.
    """
    words = " ".join(text.strip().rstrip(".!").split()).upper()
    return words in STOP_WORDS


class ContactHandle(BaseModel):
    """One handle of one contact, and what decides whether the pod may write to it.

    The one shape for a handle, whichever side of the module boundary reads it:
    the contacts API lists it, a follow-up picks the handle to write to by it.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    contact_id: UUID
    kind: IdentityKind
    value: str
    strength: IdentityStrength
    verified_at: datetime
    last_inbound_at: datetime | None = None
    unsubscribed_at: datetime | None = None


class Contact(BaseModel):
    """Somebody the pod talks to who is not a member.

    *Anonymous* while the only thing known about them is a browser
    (``IdentityKind.WEB``); every other handle was vouched for by something.
    Derived from the handles rather than stored, so it cannot disagree with them.

    ``merged_into_id`` is set once this contact turned out to be another, older
    one: the row stays, holding nothing, so anything that still names it can be
    led to the contact it became.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    display_name: str | None
    created_at: datetime
    identities: tuple[ContactHandle, ...] = ()
    merged_into_id: UUID | None = None
    answered_at: datetime | None = None

    @property
    def is_anonymous(self) -> bool:
        return all(handle.kind is IdentityKind.WEB for handle in self.identities)
