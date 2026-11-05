"""A channel's lines: the timeline and its threads, writing to it, answering
what it asks, and where each line went.

Lines are numbered per channel (``seq``) and paged by number both ways. A line
written through the API is the same line however often it is sent: the client
names it (``client_message_id``, or an ``Idempotency-Key``). Permissions are
tabled in ``api.access``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.authorization.context import ActorType
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.work import facts
from app.core.log.log import get_logger
from app.modules.channels.api.access import (
    ERRORS,
    Caller,
    CallerDep,
    conflict,
    forbidden,
    may_manage,
    missing,
    refusal,
    require_contact_message,
    unprocessable,
    visible,
)
from app.modules.channels.api.attachments import checked_parts
from app.modules.channels.api.schemas import MAX_SEQ
from app.modules.channels.api.message_schemas import (
    ChannelAnswerRequest,
    ChannelEditRequest,
    ChannelMessageResponse,
    ChannelMessagesResponse,
    ChannelPostRequest,
    DeliveriesResponse,
    DeliveryResponse,
    ThreadResponse,
    delivery_response,
)
from app.modules.channels.api.views import lines_response
from app.modules.channels.contracts.facts import ChannelDeliveryDue
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    SenderKind,
)
from app.modules.channels.domain.idempotency import client_line_name
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.channels.services.messages import LineRefused, MemberLines, MemberPost
from app.modules.channels.services.questions import AnswerRefused, answer

logger = get_logger(__name__)

router = APIRouter(
    prefix="/pods/{pod_id}/channels", tags=["Channels"], responses=ERRORS
)


def _refused(error: LineRefused | AnswerRefused) -> HTTPException:
    if isinstance(error, LineRefused):
        code = error.code
    else:
        code = {
            403: "INSUFFICIENT_PERMISSION",
            404: "LINE_NOT_FOUND",
            422: "VALIDATION_ERROR",
        }.get(error.status_code, "QUESTION_CLOSED")
    return refusal(error.status_code, code, str(error))


async def _one(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel: Channel, line: ChannelMessage
) -> ChannelMessageResponse:
    (shown,) = await lines_response(uow, caller, channel, [line])
    return shown


async def _line_in(
    uow: SqlAlchemyUnitOfWork, channel: Channel, message_id: UUID
) -> ChannelMessage:
    line = await LineRepository(uow.session).get(message_id)
    if line is None or line.channel_id != channel.id:
        raise missing("Line")
    return line


# -- reading -----------------------------------------------------------------------


@router.get(
    "/{channel_id}/messages",
    operation_id="channel.message.list",
    response_model=ChannelMessagesResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def list_messages(
    pod_id: UUID,
    channel_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
    before_seq: int | None = Query(default=None, ge=1, le=MAX_SEQ),
    after_seq: int | None = Query(default=None, ge=0, le=MAX_SEQ),
    limit: int = Query(default=50, ge=1, le=100),
) -> ChannelMessagesResponse:
    """The timeline, oldest first: the newest page, the page before
    `before_seq`, or the page after `after_seq`. A thread's replies are not in
    it; its first line counts them."""
    channel = await visible(uow, caller, channel_id)
    lines = await LineRepository(uow.session).page(
        channel.id, limit=limit + 1, before_seq=before_seq, after_seq=after_seq
    )
    forward = after_seq is not None
    has_more = len(lines) > limit
    if has_more:
        lines = lines[:limit] if forward else lines[1:]
    items = await lines_response(uow, caller, channel, lines)
    return ChannelMessagesResponse(
        items=items,
        has_more=has_more,
        next_before_seq=(
            items[0].seq
            if items and (has_more or forward) and items[0].seq > 1
            else None
        ),
        next_after_seq=items[-1].seq if items else after_seq,
    )


@router.get(
    "/{channel_id}/messages/{message_id}/thread",
    operation_id="channel.message.thread",
    response_model=ThreadResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def list_thread(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
    after_seq: int | None = Query(default=None, ge=0, le=MAX_SEQ),
    before_seq: int | None = Query(default=None, ge=1, le=MAX_SEQ),
    newest: bool = Query(
        default=False,
        description="The newest page of replies, rather than the first.",
    ),
    limit: int = Query(default=50, ge=1, le=100),
) -> ThreadResponse:
    """A thread: its first line, and a page of its replies, oldest first
    within it -- from the start, after `after_seq`, before `before_seq`, or
    (`newest`) the latest."""
    if after_seq is not None and (before_seq is not None or newest):
        raise unprocessable("Page a thread one way: after_seq, or before_seq/newest")
    channel = await visible(uow, caller, channel_id)
    found = await _line_in(uow, channel, message_id)
    lines = LineRepository(uow.session)
    root = await lines.get(found.thread_root_id) if found.thread_root_id else found
    if root is None:
        raise missing("Line")
    root = await lines.counted(root)
    backwards = newest or before_seq is not None
    replies = await lines.thread(
        root.id,
        limit=limit + 1,
        after_seq=after_seq,
        before_seq=before_seq,
        newest=newest,
    )
    has_more = len(replies) > limit
    if has_more:
        replies = replies[1:] if backwards else replies[:limit]
    shown = await lines_response(uow, caller, channel, [root, *replies])
    items = shown[1:]
    return ThreadResponse(
        root=shown[0],
        items=items,
        has_more=has_more,
        next_after_seq=items[-1].seq if items else after_seq,
        next_before_seq=items[0].seq if items and backwards and has_more else None,
    )


# -- writing -----------------------------------------------------------------------


async def _writable(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel_id: UUID
) -> Channel:
    channel = await visible(uow, caller, channel_id)
    if channel.kind is ChannelKind.CONTACT:
        require_contact_message(caller)
    return channel


def _client_name(body_id: str | None, idempotency_key: str | None) -> str | None:
    """The client's name for the line: its ``client_message_id``, else its
    ``Idempotency-Key``; both, only when they agree."""
    try:
        return client_line_name(body_id, idempotency_key)
    except ValueError as disagree:
        raise unprocessable(str(disagree)) from None


@router.post(
    "/{channel_id}/messages",
    operation_id="channel.message.post",
    response_model=ChannelMessageResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def post_message(
    pod_id: UUID,
    channel_id: UUID,
    body: ChannelPostRequest,
    uow: UoWDep,
    caller: CallerDep,
    ctx: PodContextDep,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=255
    ),
) -> ChannelMessageResponse:
    """Write in the channel. A reply goes where its thread is; otherwise to
    the place named, or the one the channel last heard from -- unless it is a
    note to members. Naming somebody who cannot see the channel is refused."""
    channel = await _writable(uow, caller, channel_id)
    poster = Principal.member(caller.user_id)
    named = await Names(uow).of(pod_id=caller.pod_id, people=[poster])
    try:
        line = await MemberLines(uow).post(
            channel,
            user_id=caller.user_id,
            display_name=named.get(poster),
            post=MemberPost(
                text=body.text,
                members_only=body.members_only,
                source_id=body.source_id,
                thread_root_id=body.thread_root_id,
                reply_to_id=body.reply_to_id,
                parts=await checked_parts(
                    uow, ctx, pod_id=caller.pod_id, parts=body.parts
                ),
                client_message_id=_client_name(body.client_message_id, idempotency_key),
                named_by=(
                    "Idempotency-Key"
                    if body.client_message_id is None and idempotency_key
                    else "client_message_id"
                ),
            ),
        )
    except LineRefused as refused:
        raise _refused(refused) from refused
    await uow.commit()
    return await _one(uow, caller, channel, line)


@router.patch(
    "/{channel_id}/messages/{message_id}",
    operation_id="channel.message.update",
    response_model=ChannelMessageResponse,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def edit_message(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    body: ChannelEditRequest,
    uow: UoWDep,
    caller: CallerDep,
    ctx: PodContextDep,
) -> ChannelMessageResponse:
    """Change what one's own line says: absent is unchanged, `null` parts
    clears them. A copy already sent outside stays as sent."""
    named = body.model_fields_set
    if "text" in named and body.text is None:
        raise unprocessable("Cannot clear text: delete the line instead")
    channel = await _writable(uow, caller, channel_id)
    try:
        line = await MemberLines(uow).edit(
            channel,
            message_id,
            user_id=caller.user_id,
            text=body.text,
            parts=(
                await checked_parts(
                    uow, ctx, pod_id=caller.pod_id, parts=body.parts or ()
                )
                if "parts" in named
                else None
            ),
        )
    except LineRefused as refused:
        raise _refused(refused) from refused
    await uow.commit()
    return await _one(uow, caller, channel, line)


@router.delete(
    "/{channel_id}/messages/{message_id}",
    operation_id="channel.message.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def delete_message(
    pod_id: UUID, channel_id: UUID, message_id: UUID, uow: UoWDep, caller: CallerDep
) -> None:
    """Delete a line: it keeps its place, saying nothing. One's own -- or,
    for who may change the channel, anybody's, shown as removed by them. A
    copy already sent outside Lemma stays as sent."""
    channel = await _writable(uow, caller, channel_id)
    try:
        deleted = await MemberLines(uow).delete(
            channel,
            message_id,
            user_id=caller.user_id,
            moderates=await may_manage(uow, caller, channel),
        )
    except LineRefused as refused:
        raise _refused(refused) from refused
    await uow.commit()
    if deleted.removed_by is not None:
        logger.info(
            "channels.line.removed_by_manager.observed",
            channel_id=str(channel.id),
            message_id=str(deleted.id),
            user_id=str(caller.user_id),
        )


@router.post(
    "/{channel_id}/messages/{message_id}/share",
    operation_id="channel.message.share",
    response_model=ChannelMessageResponse,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def share_message(
    pod_id: UUID, channel_id: UUID, message_id: UUID, uow: UoWDep, caller: CallerDep
) -> ChannelMessageResponse:
    """Share an agent's answer made with your own access with everybody in the
    channel. Until then only you read it; the others are shown that it was
    given. Yours alone to share; sharing it again changes nothing."""
    channel = await _writable(uow, caller, channel_id)
    try:
        line = await MemberLines(uow).share(channel, message_id, user_id=caller.user_id)
    except LineRefused as refused:
        raise _refused(refused) from refused
    await uow.commit()
    return await _one(uow, caller, channel, line)


@router.post(
    "/{channel_id}/messages/{message_id}/answer",
    operation_id="channel.message.answer",
    response_model=ChannelMessageResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def answer_question(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    body: ChannelAnswerRequest,
    caller: CallerDep,
    ctx: PodContextDep,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> ChannelMessageResponse:
    """Answer what this line asks the reader: a question, an approval (which
    needs a `decision`), a form. Only the person it waits on may -- and an
    approval, or an agent's paused question, only by their own hand: an agent
    calling with a token delegated by them is refused, as its tool is.

    No session is held across the answer: what asked takes it in its own -- a
    run resumed, a form submitted -- and may call out to do so.
    """
    async with uow_factory() as uow:
        channel = await visible(uow, caller, channel_id)
        await _line_in(uow, channel, message_id)
    try:
        await answer(
            uow_factory,
            message_id=message_id,
            by=Principal.member(caller.user_id),
            given=body.answer,
            through_agent=ctx.actor_type is ActorType.DELEGATED_USER_WORKLOAD,
        )
    except AnswerRefused as refused:
        raise _refused(refused) from refused
    async with uow_factory() as uow:
        return await _one(
            uow, caller, channel, await _line_in(uow, channel, message_id)
        )


# -- deliveries ----------------------------------------------------------------------


@router.get(
    "/{channel_id}/messages/{message_id}/deliveries",
    operation_id="channel.delivery.list",
    response_model=DeliveriesResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def list_deliveries(
    pod_id: UUID, channel_id: UUID, message_id: UUID, uow: UoWDep, caller: CallerDep
) -> DeliveriesResponse:
    """Where a line went outside Lemma, and what became of each send. A push
    to a member is shown only to that member."""
    channel = await visible(uow, caller, channel_id)
    line = await _line_in(uow, channel, message_id)
    deliveries = [
        delivery
        for delivery in await DeliveryRepository(uow.session).of_messages([line.id])
        if delivery.recipient_user_id in (None, caller.user_id)
    ]
    names = await Names(uow).of(
        pod_id=caller.pod_id,
        people=[
            Principal.member(delivery.recipient_user_id)
            for delivery in deliveries
            if delivery.recipient_user_id
        ],
    )
    return DeliveriesResponse(
        items=[delivery_response(delivery, names=names) for delivery in deliveries]
    )


@router.post(
    "/{channel_id}/messages/{message_id}/deliveries/{delivery_id}/retry",
    operation_id="channel.delivery.retry",
    response_model=DeliveryResponse,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def retry_delivery(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    delivery_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
) -> DeliveryResponse:
    """Try a failed or given-up send again, now. A send to a place: for who
    wrote the line, or may change the channel -- anybody else who reads the
    line is refused. A push to a member: theirs alone, as only they see it.
    It is one more try: a send that fails again on its last is given up."""
    channel = await visible(uow, caller, channel_id)
    line = await _line_in(uow, channel, message_id)
    deliveries = DeliveryRepository(uow.session)
    delivery = await deliveries.get(delivery_id)
    if delivery is None or delivery.message_id != line.id:
        raise missing("Delivery")
    if delivery.recipient_user_id is not None:
        if delivery.recipient_user_id != caller.user_id:
            raise missing("Delivery")
    else:
        if channel.kind is ChannelKind.CONTACT:
            require_contact_message(caller)
        wrote_it = (
            line.sender.kind is SenderKind.MEMBER
            and line.sender.user_id == caller.user_id
        )
        if not wrote_it and not await may_manage(uow, caller, channel):
            raise forbidden(
                "Only who wrote the line, or who may change the channel, "
                "can send it again"
            )
    retried = await deliveries.retry(delivery.id)
    if retried is None:
        raise conflict("Only a send that failed can be tried again")
    await facts.emit(
        uow,
        ChannelDeliveryDue(
            pod_id=channel.pod_id, delivery_id=retried.id, target=retried.target_key
        ),
    )
    await uow.commit()
    return delivery_response(retried, names={})
