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


class ContactIdentity(BaseModel):
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
    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    display_name: str | None
    created_at: datetime
    identities: tuple[ContactIdentity, ...] = ()
