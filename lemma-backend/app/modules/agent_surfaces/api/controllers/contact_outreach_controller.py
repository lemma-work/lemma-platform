"""A member writing to a contact first: ``POST /pods/{pod}/contacts/{c}/outreach``.

Here, not in ``contacts``, because what it does is open a place and post a
line there -- surfaces and channels -- while ``contacts`` only says whether
the line may go (``services.contact_outreach``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.services.contact_outreach import (
    ContactOutreach,
    Outreach,
    OutreachRefused,
)
from app.modules.channels.contracts import DeliveryStatus, keyed_client_id
from app.modules.contacts.contracts import IdentityKind

router = APIRouter(prefix="/pods/{pod_id}/contacts", tags=["Contact Outreach"])


class OutreachRequest(BaseModel):
    handle: str = Field(
        min_length=1,
        max_length=320,
        description=(
            "One of the contact's handles to write to: an email address, a "
            "phone number (WhatsApp), a Telegram user id."
        ),
    )
    kind: IdentityKind | None = Field(
        default=None,
        description="Which kind of handle it is, when the value could be two.",
    )
    text: str = Field(min_length=1, max_length=4000)
    client_message_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="The same id twice is the same line: a retry sends nothing new.",
    )


class OutreachLine(BaseModel):
    id: UUID
    channel_id: UUID
    seq: int
    source_id: UUID | None = None
    text: str
    created_at: datetime


class OutreachDelivery(BaseModel):
    id: UUID
    source_id: UUID | None = None
    status: DeliveryStatus = Field(
        description="PENDING until the line is sent; SENT, SKIPPED or FAILED after."
    )
    platform: str | None = None
    reason: str | None = None


class OutreachResponse(BaseModel):
    line: OutreachLine
    delivery: OutreachDelivery


@router.post(
    "/{contact_id}/outreach",
    operation_id="contact.outreach.send",
    response_model=OutreachResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        require_action(Permissions.CHANNEL_WRITE),
        require_action(Permissions.CONTACT_MESSAGE),
    ],
    responses={
        404: {"description": "No such contact."},
        409: {
            "description": "They asked not to be written to there, or the pod has nowhere to write from."
        },
        422: {
            "description": "Not one of their handles, or not one that may be written to first now."
        },
    },
)
async def send_outreach(
    pod_id: UUID,
    contact_id: UUID,
    body: OutreachRequest,
    user: CurrentUser,
    uow: UoWDep,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=255
    ),
) -> OutreachResponse:
    """Write to a contact first, on one of their handles: email any time,
    WhatsApp within 24 hours of their last message there. The line goes in
    their channel, at that handle's place -- opened if new -- and is sent from
    there. The same `client_message_id`, or `Idempotency-Key`, from the same
    member is the same line: a retry sends nothing new."""
    try:
        reached = await ContactOutreach(uow).write(
            Outreach(
                pod_id=pod_id,
                contact_id=contact_id,
                user_id=user.id,
                handle=body.handle,
                kind=body.kind,
                text=body.text,
                client_message_id=body.client_message_id
                or keyed_client_id(idempotency_key, user_id=user.id),
            )
        )
    except OutreachRefused as refused:
        raise HTTPException(refused.status_code, detail=refused.message) from refused
    await uow.commit()
    line, delivery = reached.line, reached.delivery
    return OutreachResponse(
        line=OutreachLine(
            id=line.id,
            channel_id=line.channel_id,
            seq=line.seq,
            source_id=line.place_id,
            text=line.text,
            created_at=line.created_at,
        ),
        delivery=OutreachDelivery(
            id=delivery.id,
            source_id=delivery.source_id,
            status=delivery.status,
            platform=delivery.platform,
            reason=delivery.reason,
        ),
    )
