"""What other modules may ask of contacts.

Operations, not storage. A surface asks "who is this handle in this pod",
optionally creating the contact; an agent run asks for the name to address
somebody by; a function run for a contact asks which of their handles were
proved (``verified_contact_handles``). Nothing here lists a pod's contacts or
deletes one -- that is a member's action, through this module's own API.
What the pod may send a contact, and how they stop it, is
``contracts.outreach``.

Every outsider the pod talks to is a contact, opened on the first thing they
*write* -- a message, a row, a function call -- and never on a read. A web
visitor nobody has vouched for is one too: an anonymous contact, known by the
browser's session (``open_anonymous_contact``), until they prove a handle
(``prove_handle``), which may show they were an older contact all along.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.contacts.domain.entities import (
    Contact,
    ContactHandle,
    IdentityKind,
    IdentityStrength,
    is_stop_request,
    normalize_handle,
)
from app.modules.contacts.domain.merging import ProofTooWeak
from app.modules.contacts.domain.verified import VerifiedHandles, verified_handles
from app.modules.contacts.infrastructure.repository import (
    ContactRepository,
    UnusableHandle,
)
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorSessionRepository,
)
from app.modules.contacts.services.proofs import ContactProofs

__all__ = [
    "ContactHandle",
    "ContactRef",
    "IdentityKind",
    "IdentityStrength",
    "ProofTooWeak",
    "UnusableHandle",
    "VerifiedHandles",
    "answered_contacts_in_organization",
    "contact_by_id",
    "contact_names",
    "contact_names_across",
    "contact_handles",
    "find_contact",
    "find_contact_by_user",
    "is_stop_request",
    "lock_answered_contacts_count",
    "normalize_handle",
    "note_answered",
    "note_inbound",
    "note_never_answered",
    "open_anonymous_contact",
    "open_contact",
    "prove_handle",
    "verified_contact_handles",
]


class ContactRef(BaseModel):
    """A contact, as another module needs to know them."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    display_name: str | None
    #: What to call them: their name, else a handle, else what they are.
    label: str
    is_anonymous: bool = False
    #: Whether an agent has answered them yet: what the contact limit counts.
    answered: bool = False


def _ref(contact: Contact | None) -> ContactRef | None:
    if contact is None:
        return None
    return _as_ref(contact)


def _as_ref(contact: Contact) -> ContactRef:
    return ContactRef(
        id=contact.id,
        pod_id=contact.pod_id,
        display_name=contact.display_name,
        label=contact.label,
        is_anonymous=contact.is_anonymous,
        answered=contact.answered_at is not None,
    )


async def find_contact(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, kind: IdentityKind, value: str
) -> ContactRef | None:
    """The contact this handle names in this pod, or ``None``."""
    return _ref(
        await ContactRepository(uow.session).find_by_handle(
            pod_id=pod_id, kind=kind, value=value
        )
    )


async def find_contact_by_user(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, kind: IdentityKind, user_id: str
) -> ContactRef | None:
    """The contact a platform user id belongs to, for a handle kept as
    ``{workspace}:{user}`` (Slack), when exactly one handle has it."""
    return _ref(
        await ContactRepository(uow.session).find_by_user_part(
            pod_id=pod_id, kind=kind, user_id=user_id
        )
    )


async def open_contact(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    kind: IdentityKind,
    value: str,
    strength: IdentityStrength,
    display_name: str | None,
) -> ContactRef:
    """The contact this handle names in this pod, created if there is none.

    The caller is the one that checked the handle: only a handle a platform
    or mail service vouched for may be passed with ``CHANNEL`` strength.
    Raises ``UnusableHandle`` for one with nothing left once normalised.
    """
    contact = await ContactRepository(uow.session).open(
        pod_id=pod_id,
        kind=kind,
        value=value,
        strength=strength,
        display_name=display_name,
    )
    return _as_ref(contact)


async def open_anonymous_contact(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, session_id: UUID
) -> ContactRef:
    """The contact a web chat session's browser is, opened on its first write.

    Named on the session in the same transaction, so a session names at most
    one contact however many first messages race.
    """
    contact = await ContactRepository(uow.session).open(
        pod_id=pod_id,
        kind=IdentityKind.WEB,
        value=str(session_id),
        strength=IdentityStrength.SESSION,
        display_name=None,
    )
    await VisitorSessionRepository(uow.session).name_contact(
        session_id, contact_id=contact.id
    )
    return _as_ref(contact)


async def prove_handle(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    claimant_id: UUID | None,
    kind: IdentityKind,
    value: str,
    strength: IdentityStrength,
    display_name: str | None = None,
) -> ContactRef:
    """Who the person is, having proved they hold this handle.

    ``claimant_id`` is the contact they are so far, or ``None``. The handle
    joins that contact when nobody holds it; when another contact does, the
    two are merged and the survivor returned (``domain/merging``). Raises
    ``ProofTooWeak`` for a ``strength`` that proves nothing.
    """
    proven = await ContactProofs(uow).prove(
        pod_id=pod_id,
        claimant_id=claimant_id,
        kind=kind,
        value=value,
        strength=strength,
        display_name=display_name,
    )
    return _as_ref(proven.contact)


async def contact_by_id(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> ContactRef | None:
    """This pod's contact, or ``None`` once they have been deleted.

    A contact since merged into another answers as the one it became: every
    reader that kept the old id is led to the same person.
    """
    return _ref(await _as_now(uow, pod_id=pod_id, contact_id=contact_id))


async def verified_contact_handles(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> VerifiedHandles | None:
    """The handles something proved this contact holds, or ``None`` once they
    have been forgotten.

    Of the contact they are now: one merged into another answers with the
    survivor's id and handles, which by then include the ones it brought.
    What counts as proved is ``domain/verified``.
    """
    contact = await _as_now(uow, pod_id=pod_id, contact_id=contact_id)
    return None if contact is None else verified_handles(contact)


async def _as_now(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> Contact | None:
    """The contact this id names now: itself, or the one it was merged into."""
    repository = ContactRepository(uow.session)
    contact = await repository.get(pod_id=pod_id, contact_id=contact_id)
    if contact is not None and contact.merged_into_id is not None:
        contact = await repository.get(pod_id=pod_id, contact_id=contact.merged_into_id)
    return contact


async def contact_names(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_ids: Sequence[UUID]
) -> dict[UUID, str | None]:
    """What to call each of this pod's contacts named, in one read."""
    return await ContactRepository(uow.session).names(
        pod_id=pod_id, contact_ids=contact_ids
    )


async def contact_names_across(
    uow: SqlAlchemyUnitOfWork, *, contact_ids_by_pod: Mapping[UUID, Sequence[UUID]]
) -> dict[UUID, str | None]:
    """What to call contacts in several pods, each asked for under its pod."""
    return await ContactRepository(uow.session).names_across(
        contact_ids_by_pod=contact_ids_by_pod
    )


async def note_answered(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> bool:
    """An agent answered this contact. Whether it was the first time."""
    return await ContactRepository(uow.session).note_answered(
        pod_id=pod_id, contact_id=contact_id
    )


async def note_never_answered(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> bool:
    """The run this contact was let in for failed, and none ever answered
    them: they no longer take a place in the count. Whether they had one."""
    return await ContactRepository(uow.session).note_never_answered(
        pod_id=pod_id, contact_id=contact_id
    )


async def note_inbound(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, kind: IdentityKind, value: str
) -> None:
    """Record that a contact just wrote from this handle."""
    await ContactRepository(uow.session).note_inbound(
        pod_id=pod_id, kind=kind, value=value
    )


async def contact_handles(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> list[ContactHandle]:
    """Every handle this contact is known by, or none once they are forgotten."""
    contact = await ContactRepository(uow.session).get(
        pod_id=pod_id, contact_id=contact_id
    )
    return [] if contact is None else list(contact.identities)


async def lock_answered_contacts_count(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID
) -> None:
    """Serialise counting the organization's answered contacts and marking one
    more, until this transaction ends.

    Two new contacts writing at once would each count room for one more and
    each be marked, past the limit. The second waits here, then counts the
    first.
    """
    await uow.session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"contacts-answered:{organization_id}"},
    )


async def answered_contacts_in_organization(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID
) -> int:
    """How many contacts the organization's agents have answered: what its
    contact limit counts."""
    return await ContactRepository(uow.session).answered_in_organization(
        organization_id
    )
