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

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.contacts.domain.kinds import (
    PROVING_STRENGTHS,
    IdentityKind,
    IdentityStrength,
)
from app.modules.contacts.domain.handles import (
    is_start_request,
    is_stop_request,
    normalize_handle,
)

__all__ = [
    "PROVING_STRENGTHS",
    "Contact",
    "ContactHandle",
    "IdentityKind",
    "IdentityStrength",
    "is_start_request",
    "is_stop_request",
    "normalize_handle",
]


class ContactHandle(BaseModel):
    """One handle of one contact.

    The one shape for a handle, whichever side of the module boundary reads it:
    the contacts API lists it, outreach picks the handle to write to by it.
    Whether the pod may write to it is not here: an opt-out is kept by the
    handle's hash (``contact_suppressions``), so it outlives the handle.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    contact_id: UUID
    kind: IdentityKind
    value: str
    strength: IdentityStrength
    verified_at: datetime
    last_inbound_at: datetime | None = None
    #: Who named the handle, where several could: the product a host identity
    #: is a user of. ``None`` for every other kind.
    issuer: str | None = None
    #: The handle as it arrived, before it was normalised -- what to show.
    value_raw: str | None = None


#: The kinds of handle a person would recognise themselves by, best first.
_LABELLED_BY = (IdentityKind.EMAIL, IdentityKind.PHONE)


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

    @property
    def label(self) -> str:
        """What to call them: their name, else the handle they would know
        themselves by (an email address, a number), else what they are."""
        if self.display_name:
            return self.display_name
        for kind in _LABELLED_BY:
            for handle in self.identities:
                if handle.kind is kind:
                    return handle.value_raw or handle.value
        return "Web visitor" if self.is_anonymous else "A contact"
