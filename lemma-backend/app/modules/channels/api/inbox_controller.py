"""Each member's inbox across a pod's channels, and search over them.

Both read only channels the member may see (the one visibility rule, in SQL):
a channel they cannot open is never counted, listed or found.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.api.access import (
    ERRORS,
    Caller,
    CallerDep,
    decode_cursor,
    encode_cursor,
    unprocessable,
    visible,
)
from app.modules.channels.api.message_schemas import (
    InboxItem,
    InboxResponse,
    InboxTotals,
    SearchItem,
    SearchResponse,
)
from app.modules.channels.api.schemas import UserText
from app.modules.channels.api.views import channel_rows, lines_across
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ContactStatus,
    SenderKind,
)
from app.modules.channels.infrastructure.inbox import UNREAD_CAP, InboxReads
from app.modules.channels.infrastructure.line_search import (
    LineSearch,
    SearchFilter,
    SpeakerRef,
)
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.questions import QuestionRepository
from app.modules.channels.infrastructure.repository import ChannelRepository

#: Routes with a fixed segment under the pod; included before the channels'.
router = APIRouter(prefix="/pods/{pod_id}", tags=["Channels"], responses=ERRORS)

#: The most items each of the inbox's lists shows.
INBOX_ITEMS = 50


@router.get(
    "/inbox",
    operation_id="channel.inbox.get",
    response_model=InboxResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def get_inbox(pod_id: UUID, uow: UoWDep, caller: CallerDep) -> InboxResponse:
    """What is new for the reader across the pod: the badges, the questions
    waiting on them, the unread lines naming them -- newest first -- and the
    contacts waiting on a reply."""
    inbox = InboxReads(uow.session)
    totals = await inbox.totals(caller.viewer)
    waiting = await QuestionRepository(uow.session).waiting_on(
        caller.viewer, limit=INBOX_ITEMS
    )
    mentioned = await inbox.mentioned_in(caller.viewer, limit=INBOX_ITEMS)
    lines = await LineRepository(uow.session).by_ids(
        [question.message_id for question in waiting]
        + [message_id for _, message_id in mentioned]
    )
    channels = await ChannelRepository(uow.session).by_ids(
        {line.channel_id for line in lines.values()}
    )

    async def items(ids: list[UUID]) -> list[InboxItem]:
        found = [
            (channels[line.channel_id], line)
            for line in (lines.get(each) for each in ids)
            if line is not None and line.channel_id in channels
        ]
        return [
            InboxItem(channel=channel, message=message)
            for channel, message in await lines_across(uow, caller, found)
        ]

    contacts = await _contacts_waiting(uow, caller)
    return InboxResponse(
        totals=InboxTotals(
            channels=totals.channels,
            mentions=totals.mentions,
            waiting=totals.waiting,
            contacts=len(contacts),
        ),
        waiting=await items([question.message_id for question in waiting]),
        mentions=await items([message_id for _, message_id in mentioned]),
        contacts=await channel_rows(uow, caller, contacts),
    )


async def _contacts_waiting(uow: SqlAlchemyUnitOfWork, caller: Caller) -> list[Channel]:
    """The contacts waiting on a reply the reader should know of: asking for
    a person (nobody holds them), and the reader's own whose last word was
    the contact's -- whether or not the reader is in their channel."""
    if not caller.viewer.sees_contacts:
        return []
    channels = ChannelRepository(uow.session)
    asking = await channels.list_for(
        caller.viewer, kind=ChannelKind.CONTACT, needs_person=True, limit=INBOX_ITEMS
    )
    theirs = await channels.list_for(
        caller.viewer,
        kind=ChannelKind.CONTACT,
        assignee_user_id=caller.user_id,
        status=ContactStatus.OPEN,
        limit=INBOX_ITEMS,
    )
    found = {channel.id: channel for channel in [*asking, *theirs]}
    return sorted(
        found.values(), key=lambda each: (each.last_line_at, each.id), reverse=True
    )[:INBOX_ITEMS]


@router.get(
    "/search",
    operation_id="channel.search",
    response_model=SearchResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def search(
    pod_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
    q: Annotated[UserText, Query(min_length=1, max_length=200)],
    channel_id: UUID | None = Query(default=None),
    kind: ChannelKind | None = Query(
        default=None, description="Only channels of this kind."
    ),
    sender_kind: SenderKind | None = Query(
        default=None, description="Only lines said by this person: with `sender_id`."
    ),
    sender_id: UUID | None = Query(
        default=None,
        description="A member's user id, a contact's id or an agent's id.",
    ),
    since: datetime | None = Query(default=None, description="Said at or after."),
    until: datetime | None = Query(default=None, description="Said before."),
    archived: bool = Query(default=False, description="Search archived channels too."),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=30, ge=1, le=UNREAD_CAP),
) -> SearchResponse:
    """Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them -- each with its rank and the words around the
    match. An answer withheld from the reader is not found by what it said."""
    if (sender_kind is None) != (sender_id is None):
        raise unprocessable("Name a sender by both sender_kind and sender_id")
    if channel_id is not None:
        await visible(uow, caller, channel_id)
    hits = await LineSearch(uow.session).search(
        caller.viewer,
        query=q,
        limit=limit,
        within=SearchFilter(
            channel_id=channel_id,
            kind=kind,
            sender=SpeakerRef(kind=sender_kind, id=sender_id)
            if sender_kind is not None and sender_id is not None
            else None,
            after=since,
            until=until,
            archived=archived,
        ),
        before=decode_cursor(cursor),
    )
    channels = await ChannelRepository(uow.session).by_ids(
        {hit.message.channel_id for hit in hits}
    )
    by_line = {hit.message.id: hit for hit in hits}
    shown = [
        (channels[hit.message.channel_id], hit.message)
        for hit in hits
        if hit.message.channel_id in channels
    ]
    last = hits[-1].message if len(hits) == limit else None
    return SearchResponse(
        items=[
            SearchItem(
                channel=channel,
                message=message,
                rank=by_line[message.id].rank,
                snippet=by_line[message.id].snippet,
            )
            for channel, message in await lines_across(uow, caller, shown)
        ],
        next_cursor=encode_cursor(last.created_at, last.id) if last else None,
    )
