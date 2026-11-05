"""What proving a handle does to the contacts a pod already has.

A person is one contact in a pod, however they reach it. Most of the time they
are recognised by the handle they write from. Sometimes they prove a second
handle -- a web visitor enters a code sent to an email address -- and that is
when the pod learns that two contacts it kept were one person all along.

The rules are small and they are the whole of it, so they live here, pure:

* A handle nobody holds joins the contact who proved it.
* A handle somebody else holds means the two are one person. They merge, but
  only on proof: a code, a host token, a platform's signed payload. A member
  typing a handle in, or a browser holding a session, proves nothing.
* Which one survives: a contact known by a vouched handle over an anonymous
  one, then the older. The survivor's id is the one already written into the
  pod's rows and conversations most often, so it is the one that should stay.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.domain.errors import DomainError
from app.modules.contacts.domain.entities import (
    PROVING_STRENGTHS,
    Contact,
    IdentityStrength,
)


class ProofStep(StrEnum):
    """What proving a handle comes to."""

    #: Nobody holds the handle and nobody proved it before: a new contact.
    OPEN = "OPEN"
    #: Nobody holds it: it joins the contact who proved it.
    ATTACH = "ATTACH"
    #: The person proving it is already the contact who holds it.
    ALREADY_THEIRS = "ALREADY_THEIRS"
    #: Somebody holds it and nobody else is claiming it: that contact.
    HOLDER = "HOLDER"
    #: Two contacts turn out to be one person.
    MERGE = "MERGE"


@dataclass(frozen=True, slots=True)
class ProofPlan:
    step: ProofStep
    #: The contact the person is, once the step is taken. ``None`` only for OPEN.
    survivor: Contact | None = None
    #: The contact that stops being one, for MERGE.
    merged: Contact | None = None


class ProofTooWeak(DomainError):
    """A handle another contact holds was offered without proof that it is theirs."""

    def __init__(self) -> None:
        super().__init__(
            "That handle belongs to another contact, and nothing proved it is "
            "this person's",
            code="contact_proof_too_weak",
            status_code=409,
        )


def survivor_of(first: Contact, second: Contact) -> tuple[Contact, Contact]:
    """``(survivor, merged)`` of two contacts that are one person.

    Vouched over anonymous, then the older, then the lower id -- a total order,
    so two merges racing over the same pair agree on the answer.
    """
    ranked = sorted(
        (first, second),
        key=lambda contact: (contact.is_anonymous, contact.created_at, contact.id),
    )
    return ranked[0], ranked[1]


def plan_proof(
    *,
    claimant: Contact | None,
    holder: Contact | None,
    strength: IdentityStrength,
) -> ProofPlan:
    """What happens when ``claimant`` proves a handle ``holder`` already has.

    ``claimant`` is who the person is so far (an anonymous web visitor, say),
    or ``None`` when they are nobody yet. ``holder`` is the contact the handle
    names in the pod, or ``None``.
    """
    if holder is None:
        if claimant is None:
            return ProofPlan(ProofStep.OPEN)
        return ProofPlan(ProofStep.ATTACH, survivor=claimant)
    if claimant is None:
        return ProofPlan(ProofStep.HOLDER, survivor=holder)
    if claimant.id == holder.id:
        return ProofPlan(ProofStep.ALREADY_THEIRS, survivor=holder)
    if strength not in PROVING_STRENGTHS:
        raise ProofTooWeak()
    survivor, merged = survivor_of(claimant, holder)
    return ProofPlan(ProofStep.MERGE, survivor=survivor, merged=merged)
