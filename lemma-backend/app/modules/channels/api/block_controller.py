"""Who the reader has blocked in a pod's chats.

A block is the reader's own, in this pod: lines from the person they blocked
come to them marked ``blocked``, with nothing of what they said, and they are
not told of them outside the app. Nobody else's view changes, the person is
not told, and the pod's agents answer them as before -- stopping an agent
answering a contact is the contact's own setting (``contacts``).

A member of the pod or one of its contacts can be blocked; an agent cannot,
and neither can the reader. Anybody who may read the pod's chats may block.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.api.access import (
    ERRORS,
    CallerDep,
    missing,
    unprocessable,
)
from app.modules.channels.api.schemas import PersonRef, ref
from app.modules.channels.domain.entities import Principal, SenderKind
from app.modules.channels.infrastructure.blocks import BlockRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.contacts.contracts import contact_by_id
from app.modules.pod.contracts.members import pod_member_id

router = APIRouter(prefix="/pods/{pod_id}/blocks", tags=["Channels"], responses=ERRORS)


class BlockResponse(BaseModel):
    person: PersonRef
    created_at: datetime


class BlocksResponse(BaseModel):
    items: list[BlockResponse]


async def _person(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    me: UUID,
    kind: SenderKind,
    person_id: UUID,
) -> Principal:
    """The person ``kind:person_id`` names in this pod -- a contact since merged
    is the one they became -- or a refusal."""
    if kind is SenderKind.AGENT:
        raise unprocessable("A Lemma's agent can't be blocked")
    if kind is SenderKind.MEMBER:
        if person_id == me:
            raise unprocessable("You can't block yourself")
        if await pod_member_id(uow, pod_id, person_id) is None:
            raise missing("Person")
        return Principal.member(person_id)
    contact = await contact_by_id(uow, pod_id=pod_id, contact_id=person_id)
    if contact is None:
        raise missing("Person")
    return Principal.contact(contact.id)


async def _shown(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, blocks: list[tuple[Principal, datetime]]
) -> list[BlockResponse]:
    names = await Names(uow).of(pod_id=pod_id, people=[person for person, _ in blocks])
    return [
        BlockResponse(person=ref(person, names), created_at=at) for person, at in blocks
    ]


@router.get(
    "",
    operation_id="channel.block.list",
    response_model=BlocksResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def list_blocks(pod_id: UUID, uow: UoWDep, caller: CallerDep) -> BlocksResponse:
    """Everybody the reader blocked in this pod, newest first."""
    blocks = await BlockRepository(uow.session).listed(
        pod_id=pod_id, user_id=caller.user_id
    )
    return BlocksResponse(items=await _shown(uow, pod_id, blocks))


@router.put(
    "/{kind}:{person_id}",
    operation_id="channel.block.add",
    response_model=BlockResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def block_person(
    pod_id: UUID, kind: SenderKind, person_id: UUID, uow: UoWDep, caller: CallerDep
) -> BlockResponse:
    """Block a member or a contact, for the reader alone. Blocking somebody
    already blocked changes nothing."""
    person = await _person(
        uow, pod_id=pod_id, me=caller.user_id, kind=kind, person_id=person_id
    )
    made = await BlockRepository(uow.session).block(
        pod_id=pod_id, user_id=caller.user_id, person=person
    )
    await uow.commit()
    (shown,) = await _shown(uow, pod_id, [(person, made)])
    return shown


@router.delete(
    "/{kind}:{person_id}",
    operation_id="channel.block.remove",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def unblock_person(
    pod_id: UUID, kind: SenderKind, person_id: UUID, uow: UoWDep, caller: CallerDep
) -> None:
    """Lift a block: their lines read as anybody's again, the old ones too."""
    if kind is SenderKind.AGENT:
        raise missing("Block")
    person = (
        Principal.member(person_id)
        if kind is SenderKind.MEMBER
        else Principal.contact(person_id)
    )
    if not await BlockRepository(uow.session).unblock(
        pod_id=pod_id, user_id=caller.user_id, person=person
    ):
        raise missing("Block")
    await uow.commit()
