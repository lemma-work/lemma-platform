"""A pod's contacts: the people its bots answer who are not members.

Every member who can read the pod can read its contacts, as every member can
read its groups: a support inbox nobody else can see is not one. Renaming a
contact takes what editing the pod takes. Deleting one -- a request to forget a
person -- takes a pod admin.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.agent.contracts.contact_conversations import (
    ExportedConversation,
    export_contact_conversations,
    forget_contact_conversations,
)
from app.modules.contacts.domain.entities import (
    Contact,
    IdentityKind,
    IdentityStrength,
)
from app.modules.contacts.contracts.visitor_sessions import (
    forget_session_liveness,
    revoke_visitor_sessions,
)
from app.modules.contacts.infrastructure.repository import (
    MAX_PAGE,
    ContactRepository,
)

router = APIRouter(prefix="/pods/{pod_id}/contacts", tags=["Contacts"])


class ContactIdentityResponse(BaseModel):
    kind: IdentityKind
    value: str
    strength: IdentityStrength
    verified_at: datetime


class ContactResponse(BaseModel):
    id: UUID
    display_name: str | None = None
    created_at: datetime
    identities: list[ContactIdentityResponse] = Field(default_factory=list)


class ContactListResponse(BaseModel):
    items: list[ContactResponse]
    next_before: datetime | None = Field(
        default=None,
        description="Pass as `before` for the next page; absent on the last.",
    )


class ContactExportResponse(BaseModel):
    """Everything the pod holds about one contact, for a request to see it."""

    contact: ContactResponse
    conversations: list[ExportedConversation]


class ContactUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)


def _response(contact: Contact) -> ContactResponse:
    return ContactResponse(
        id=contact.id,
        display_name=contact.display_name,
        created_at=contact.created_at,
        identities=[
            ContactIdentityResponse(
                kind=identity.kind,
                value=identity.value,
                strength=identity.strength,
                verified_at=identity.verified_at,
            )
            for identity in contact.identities
        ],
    )


async def _found(uow: UoWDep, *, pod_id: UUID, contact_id: UUID) -> Contact:
    contact = await ContactRepository(uow.session).get(
        pod_id=pod_id, contact_id=contact_id
    )
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return contact


@router.get(
    "",
    operation_id="contact.list",
    response_model=ContactListResponse,
    dependencies=[require_action(Permissions.POD_READ)],
)
async def list_contacts(
    pod_id: UUID,
    uow: UoWDep,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    before: datetime | None = Query(default=None),
) -> ContactListResponse:
    """The pod's contacts, newest first."""
    contacts = await ContactRepository(uow.session).list(
        pod_id=pod_id, limit=limit, before=before
    )
    return ContactListResponse(
        items=[_response(contact) for contact in contacts],
        next_before=contacts[-1].created_at if len(contacts) == limit else None,
    )


@router.get(
    "/{contact_id}",
    operation_id="contact.get",
    response_model=ContactResponse,
    dependencies=[require_action(Permissions.POD_READ)],
)
async def get_contact(pod_id: UUID, contact_id: UUID, uow: UoWDep) -> ContactResponse:
    return _response(await _found(uow, pod_id=pod_id, contact_id=contact_id))


@router.patch(
    "/{contact_id}",
    operation_id="contact.update",
    response_model=ContactResponse,
    dependencies=[require_action(Permissions.POD_UPDATE)],
)
async def update_contact(
    pod_id: UUID, contact_id: UUID, request: ContactUpdateRequest, uow: UoWDep
) -> ContactResponse:
    repository = ContactRepository(uow.session)
    if not await repository.rename(
        pod_id=pod_id, contact_id=contact_id, display_name=request.display_name
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Contact not found")
    await uow.commit()
    return _response(await _found(uow, pod_id=pod_id, contact_id=contact_id))


@router.get(
    "/{contact_id}/export",
    operation_id="contact.export",
    response_model=ContactExportResponse,
    dependencies=[require_action(Permissions.POD_MEMBER_MANAGE)],
)
async def export_contact(
    pod_id: UUID, contact_id: UUID, uow: UoWDep
) -> ContactExportResponse:
    """A contact's handles and what was said with them, for a request to see it.

    Takes a pod admin, as forgetting does: both answer the person the data is
    about, not the member reading it.
    """
    contact = await _found(uow, pod_id=pod_id, contact_id=contact_id)
    return ContactExportResponse(
        contact=_response(contact),
        conversations=await export_contact_conversations(
            uow, pod_id=pod_id, contact_id=contact_id
        ),
    )


@router.delete(
    "/{contact_id}",
    operation_id="contact.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.POD_MEMBER_MANAGE)],
)
async def delete_contact(pod_id: UUID, contact_id: UUID, uow: UoWDep) -> None:
    """Forget a contact: their handles and their conversations go with them.

    One transaction, so a contact is never half forgotten. Their web sessions
    end with it, before the contact goes: a session the forgetting left behind
    would otherwise go on as an anonymous visitor's, holding a token that still
    names them.
    """
    await _found(uow, pod_id=pod_id, contact_id=contact_id)
    ended = await revoke_visitor_sessions(uow, contact_id=contact_id)
    await forget_contact_conversations(uow, pod_id=pod_id, contact_id=contact_id)
    await ContactRepository(uow.session).delete(pod_id=pod_id, contact_id=contact_id)
    await uow.commit()
    await forget_session_liveness(ended)
