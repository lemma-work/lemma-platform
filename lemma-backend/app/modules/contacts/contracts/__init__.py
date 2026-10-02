"""What other modules may ask of contacts.

Operations, not storage. A surface asks "who is this handle in this pod",
optionally creating the contact; an agent run asks for the name to address
somebody by. Nothing here lists a pod's contacts or deletes one -- that is a
member's action, through this module's own API.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.contacts.domain.entities import (
    Contact,
    IdentityKind,
    IdentityStrength,
)
from app.modules.contacts.infrastructure.repository import ContactRepository

__all__ = [
    "ContactRef",
    "IdentityKind",
    "IdentityStrength",
    "contact_by_id",
    "find_contact",
    "open_contact",
]


class ContactRef(BaseModel):
    """A contact, as another module needs to know them."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    display_name: str | None


def _ref(contact: Contact | None) -> ContactRef | None:
    if contact is None:
        return None
    return ContactRef(
        id=contact.id, pod_id=contact.pod_id, display_name=contact.display_name
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
    """
    contact = await ContactRepository(uow.session).open(
        pod_id=pod_id,
        kind=kind,
        value=value,
        strength=strength,
        display_name=display_name,
    )
    return ContactRef(
        id=contact.id, pod_id=contact.pod_id, display_name=contact.display_name
    )


async def contact_by_id(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> ContactRef | None:
    """This pod's contact, or ``None`` once they have been deleted."""
    return _ref(
        await ContactRepository(uow.session).get(pod_id=pod_id, contact_id=contact_id)
    )
