"""A pod's contacts: the people its bots answer who are not members.

Reading a pod's contacts takes what reading its conversations takes: a contact
is a person the pod talks to, and their handles are as private as what was
said. Renaming a contact takes what editing the pod takes. Exporting or
deleting one -- a request to see or to forget a person -- takes a pod admin.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep, get_uow_factory
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts.contacts import ExportedConversation
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
from app.modules.contacts.services.outreach import opted_out_handles
from app.modules.datastore.contracts.contact_rows import ContactRow

router = APIRouter(prefix="/pods/{pod_id}/contacts", tags=["Contacts"])


class ContactIdentityResponse(BaseModel):
    kind: IdentityKind
    value: str
    strength: IdentityStrength
    verified_at: datetime
    opted_out: bool = Field(
        default=False,
        description=(
            "They asked the pod to stop writing to this handle (STOP, an "
            "unsubscribe link, a bounce or a complaint): nothing is sent to it "
            "until they write from it again."
        ),
    )


class ContactResponse(BaseModel):
    id: UUID
    display_name: str | None = None
    label: str = Field(
        description=(
            "What to call them: their name, else the handle they are known "
            "by (an email address, a number), else that they are a web visitor."
        )
    )
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
    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` for the next page; absent on the last.",
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


async def _response(uow: SqlAlchemyUnitOfWork, contact: Contact) -> ContactResponse:
    [shown] = await _responses(uow, [contact])
    return shown


async def _responses(
    uow: SqlAlchemyUnitOfWork, contacts: Sequence[Contact]
) -> list[ContactResponse]:
    """The contacts as shown, with which handles opted out: one read per kind
    of handle for the page."""
    if not contacts:
        return []
    stopped = await opted_out_handles(
        uow,
        [handle for contact in contacts for handle in contact.identities],
        pod_id=contacts[0].pod_id,
    )
    return [_shown(contact, stopped) for contact in contacts]


def _shown(contact: Contact, stopped: set[UUID]) -> ContactResponse:
    return ContactResponse(
        id=contact.id,
        display_name=contact.display_name,
        label=contact.label,
        created_at=contact.created_at,
        identities=[
            ContactIdentityResponse(
                kind=identity.kind,
                value=identity.value,
                strength=identity.strength,
                verified_at=identity.verified_at,
                opted_out=identity.id in stopped,
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


def _encode_cursor(contact: Contact) -> str:
    """The last contact of a page, opaque: ``created_at|id``, base64."""
    raw = f"{contact.created_at.isoformat()}|{contact.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _list_cursor(cursor: str | None) -> ContactCursor | None:
    """The page after the one a cursor of ours ended; anything else is a 400."""
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, _, contact_id = raw.partition("|")
        return ContactCursor(
            created_at=datetime.fromisoformat(at), contact_id=UUID(contact_id)
        )
    except ValueError:  # bad base64, text or fields alike
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="That cursor is not one we gave"
        ) from None


@router.get(
    "",
    operation_id="contact.list",
    response_model=ContactListResponse,
    dependencies=[require_action(Permissions.CONTACT_READ)],
)
async def list_contacts(
    pod_id: UUID,
    uow: UoWDep,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    cursor: str | None = Query(default=None, max_length=200),
    q: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description="Only contacts whose name, email address or number holds this.",
    ),
) -> ContactListResponse:
    """The pod's contacts, newest first. Follow `next_cursor` until it is
    absent."""
    contacts = await ContactRepository(uow.session).list(
        pod_id=pod_id,
        limit=limit,
        before=_list_cursor(cursor),
        matching=(q or "").replace("\x00", "").strip() or None,
    )
    last = contacts[-1] if len(contacts) == limit else None
    return ContactListResponse(
        items=await _responses(uow, contacts),
        next_cursor=_encode_cursor(last) if last else None,
    )


@router.get(
    "/{contact_id}",
    operation_id="contact.get",
    response_model=ContactResponse,
    dependencies=[require_action(Permissions.CONTACT_READ)],
)
async def get_contact(pod_id: UUID, contact_id: UUID, uow: UoWDep) -> ContactResponse:
    return await _response(uow, await _found(uow, pod_id=pod_id, contact_id=contact_id))


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
    return await _response(uow, await _found(uow, pod_id=pod_id, contact_id=contact.id))


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
    # The person a merged contact's id leads to is the one exported: what was
    # said under the old id has moved to them.
    contact = await _found(uow, pod_id=pod_id, contact_id=contact_id)
    page = await export_page(
        uow_factory, pod_id=pod_id, contact_id=contact.id, cursor=after
    )
    return ContactExportResponse(
        contact=await _response(uow, contact),
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
