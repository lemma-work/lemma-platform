"""A pod's contacts: the people its bots answer who are not members.

Reading a pod's contacts takes what reading its conversations takes: a contact
is a person the pod talks to, and their handles are as private as what was
said. Renaming a contact takes what editing the pod takes. Exporting or
deleting one -- a request to see or to forget a person -- takes a pod admin.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep, get_uow_factory
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts.contact_conversations import ExportedConversation
from app.modules.contacts.domain.entities import (
    Contact,
    IdentityKind,
    IdentityStrength,
)
from app.modules.contacts.infrastructure.repository import (
    MAX_PAGE,
    ContactCursor,
    ContactRepository,
)
from app.modules.contacts.services.export import (
    BadExportCursor,
    ExportCursor,
    export_contact as export_page,
)
from app.modules.contacts.services.forget import forget_contact
from app.modules.datastore.contracts.contact_rows import ContactRow

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
    is_anonymous: bool = Field(
        default=False,
        description=(
            "Known only by the browser they wrote from: a web visitor nobody "
            "has vouched for yet."
        ),
    )
    answered_at: datetime | None = Field(
        default=None,
        description=(
            "When an agent first answered them; what the contact limit counts."
        ),
    )


class ContactListResponse(BaseModel):
    items: list[ContactResponse]
    next_before: str | None = Field(
        default=None,
        description="Pass as `before` for the next page; absent on the last.",
    )


class ContactExportResponse(BaseModel):
    """Everything the pod holds about one contact, a page at a time.

    Their conversations come first, then their rows in the pod's
    contact-owned tables. Follow `next_cursor` until it is absent.
    """

    contact: ContactResponse
    conversations: list[ExportedConversation]
    rows: list[ContactRow] = Field(default_factory=list)
    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` for the next page; absent on the last.",
    )


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
        is_anonymous=contact.is_anonymous,
        answered_at=contact.answered_at,
    )


async def _found(uow: UoWDep, *, pod_id: UUID, contact_id: UUID) -> Contact:
    """The contact, or the one it was merged into: a link kept from before a
    merge still leads to the person."""
    contacts = ContactRepository(uow.session)
    contact = await contacts.get(pod_id=pod_id, contact_id=contact_id)
    if contact is not None and contact.merged_into_id is not None:
        contact = await contacts.get(pod_id=pod_id, contact_id=contact.merged_into_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return contact


def _list_cursor(before: str | None) -> ContactCursor | None:
    """``created_at|id`` of the last contact on the previous page."""
    if before is None:
        return None
    at, _, contact_id = before.partition("|")
    try:
        return ContactCursor(
            created_at=datetime.fromisoformat(at), contact_id=UUID(contact_id)
        )
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="That cursor is not one we gave"
        ) from None


@router.get(
    "",
    operation_id="contact.list",
    response_model=ContactListResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def list_contacts(
    pod_id: UUID,
    uow: UoWDep,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    before: str | None = Query(default=None, max_length=100),
) -> ContactListResponse:
    """The pod's contacts, newest first."""
    contacts = await ContactRepository(uow.session).list(
        pod_id=pod_id, limit=limit, before=_list_cursor(before)
    )
    last = contacts[-1] if len(contacts) == limit else None
    return ContactListResponse(
        items=[_response(contact) for contact in contacts],
        next_before=f"{last.created_at.isoformat()}|{last.id}" if last else None,
    )


@router.get(
    "/{contact_id}",
    operation_id="contact.get",
    response_model=ContactResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
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
    # The person a merged contact's id now leads to is the one renamed: the
    # absorbed row is never shown again.
    contact = await _found(uow, pod_id=pod_id, contact_id=contact_id)
    repository = ContactRepository(uow.session)
    if not await repository.rename(
        pod_id=pod_id, contact_id=contact.id, display_name=request.display_name
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Contact not found")
    await uow.commit()
    return _response(await _found(uow, pod_id=pod_id, contact_id=contact.id))


@router.get(
    "/{contact_id}/export",
    operation_id="contact.export",
    response_model=ContactExportResponse,
    dependencies=[require_action(Permissions.POD_MEMBER_MANAGE)],
)
async def export_contact(
    pod_id: UUID,
    contact_id: UUID,
    uow: UoWDep,
    cursor: str | None = Query(default=None, max_length=1000),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> ContactExportResponse:
    """A contact's handles, what was said with them, and the rows that are theirs.

    Takes a pod admin, as forgetting does: both answer the person the data is
    about, not the member reading it.
    """
    try:
        after = ExportCursor.decode(cursor) if cursor else None
    except BadExportCursor as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    contact = await _found(uow, pod_id=pod_id, contact_id=contact_id)
    page = await export_page(
        uow_factory, pod_id=pod_id, contact_id=contact_id, cursor=after
    )
    return ContactExportResponse(
        contact=_response(contact),
        conversations=list(page.conversations),
        rows=list(page.rows),
        next_cursor=page.next_cursor.encode() if page.next_cursor else None,
    )


@router.delete(
    "/{contact_id}",
    operation_id="contact.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.POD_MEMBER_MANAGE)],
)
async def delete_contact(
    pod_id: UUID,
    contact_id: UUID,
    user: CurrentUser,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> None:
    """Forget a contact: their rows, handles, conversations and chat sessions.

    See ``services/forget`` for the order, which is what makes a failure safe
    to retry.
    """
    if (
        await forget_contact(
            uow_factory, pod_id=pod_id, contact_id=contact_id, forgotten_by=user.id
        )
        is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Contact not found")
