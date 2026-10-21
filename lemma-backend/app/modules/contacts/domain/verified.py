"""The handles a pod may treat as a contact's own: the ones something proved.

A function opened to contacts matches the person asking against the pod's
records -- an order placed with an email address, a project kept under a phone
number. What it matches by has to be a handle the person was shown to hold,
not one they typed to the model: anybody can type somebody else's address.

So only handles whose strength proves them count (``PROVING_STRENGTHS``): a
platform's signed payload or a mail service's verdict (``CHANNEL``), the
customer's own server (``HOST``), a code the person entered (``CODE``). A
handle a member typed in (``MEMBER``) says what the member believes; a browser
session (``SESSION``) says the same browser came back. Neither is the person's
word, let alone proof, so neither is listed.

A handle the contact asked not to be written to is still theirs, and still
listed: a stop is about the pod writing first, and says nothing about who holds
the address. Outreach checks suppressions on its own.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.contacts.domain.entities import Contact
from app.modules.contacts.domain.kinds import PROVING_STRENGTHS, IdentityKind


class VerifiedHandles(BaseModel):
    """One contact's proved handles, by kind, each in its stored spelling.

    Phones are E.164, email addresses lower-cased, host identities
    ``host:{issuer}:{subject}``, Slack and Teams ones ``{team}:{user}``
    (``domain/handles``). Each list is sorted, so the same contact reads the
    same every time. A browser session is never a handle here (``WEB``).
    """

    model_config = ConfigDict(frozen=True)

    contact_id: UUID
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    telegram_ids: tuple[str, ...] = ()
    host_ids: tuple[str, ...] = ()
    slack_ids: tuple[str, ...] = ()
    teams_ids: tuple[str, ...] = ()


_FIELD_OF_KIND: dict[IdentityKind, str] = {
    IdentityKind.EMAIL: "emails",
    IdentityKind.PHONE: "phones",
    IdentityKind.TELEGRAM: "telegram_ids",
    IdentityKind.HOST: "host_ids",
    IdentityKind.SLACK: "slack_ids",
    IdentityKind.TEAMS: "teams_ids",
}


def verified_handles(contact: Contact) -> VerifiedHandles:
    """The handles of this contact that something proved they hold."""
    found: dict[str, set[str]] = {field: set() for field in _FIELD_OF_KIND.values()}
    for handle in contact.identities:
        field = _FIELD_OF_KIND.get(handle.kind)
        if field is not None and handle.strength in PROVING_STRENGTHS:
            found[field].add(handle.value)
    return VerifiedHandles.model_validate(
        {
            "contact_id": contact.id,
            **{field: tuple(sorted(values)) for field, values in found.items()},
        }
    )
