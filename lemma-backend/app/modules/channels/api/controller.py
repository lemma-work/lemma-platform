"""A pod's channels, for its members.

Reading a channel takes what reading the pod's conversations takes: a channel
is where people talk with the pod, and its lines are as private as what was
said. A member sees their own direct channel and every contact's and group's.
Stepping in to answer a contact by hand takes what writing to a contact takes.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.channels.api.schemas import (
    ChannelDetailResponse,
    ChannelListResponse,
    ChannelMessagesResponse,
    ChannelResponse,
    channel_response,
    detail_response,
    message_response,
)
from app.modules.channels.domain.entities import Channel, ChannelKind
from app.modules.channels.infrastructure.repository import (
    MAX_LINES,
    ChannelRepository,
)

router = APIRouter(prefix="/pods/{pod_id}/channels", tags=["Channels"])

_ALL_KINDS = (ChannelKind.DIRECT, ChannelKind.CONTACT, ChannelKind.GROUP)


def _cursor(before: str | None) -> tuple[datetime, UUID] | None:
    """A ``time|id`` cursor this API handed out, or a refusal."""
    if before is None:
        return None
    at, _, row_id = before.partition("|")
    try:
        return datetime.fromisoformat(at), UUID(row_id)
    except ValueError:
        raise _bad_cursor() from None


def _bad_cursor() -> HTTPException:
    return HTTPException(
        status.HTTP_400_BAD_REQUEST, detail="That cursor is not one we gave"
    )


async def _visible(
    channels: ChannelRepository, *, pod_id: UUID, channel_id: UUID, user_id: UUID
) -> Channel:
    """The channel, if the pod has it and this member may read it.

    Another member's direct channel answers as missing, exactly as a channel
    that does not exist: whether it does is not this member's to learn.
    """
    channel = await channels.get(channel_id)
    if (
        channel is None
        or channel.pod_id != pod_id
        or (channel.kind is ChannelKind.DIRECT and channel.member_user_id != user_id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return channel


@router.get(
    "",
    operation_id="channel.list",
    response_model=ChannelListResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def list_channels(
    pod_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    kind: ChannelKind | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_LINES),
    before: str | None = Query(default=None, max_length=100),
) -> ChannelListResponse:
    """The pod's channels this member sees, most recently active first."""
    channels = await ChannelRepository(uow.session).list_for_member(
        pod_id=pod_id,
        user_id=user.id,
        kinds=(kind,) if kind else _ALL_KINDS,
        before=_cursor(before),
        limit=limit,
    )
    last = channels[-1] if len(channels) == limit else None
    return ChannelListResponse(
        items=[channel_response(channel) for channel in channels],
        next_before=(
            f"{(last.last_message_at or last.created_at).isoformat()}|{last.id}"
            if last
            else None
        ),
    )


@router.get(
    "/{channel_id}",
    operation_id="channel.get",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def get_channel(
    pod_id: UUID, channel_id: UUID, user: CurrentUser, uow: UoWDep
) -> ChannelDetailResponse:
    """One channel, with the places it is fed from and the people in it."""
    channels = ChannelRepository(uow.session)
    channel = await _visible(
        channels, pod_id=pod_id, channel_id=channel_id, user_id=user.id
    )
    return detail_response(
        channel,
        await channels.sources_of(channel.id),
        await channels.participants(channel.id),
    )


@router.get(
    "/{channel_id}/messages",
    operation_id="channel.message.list",
    response_model=ChannelMessagesResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def list_channel_messages(
    pod_id: UUID,
    channel_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    limit: int = Query(default=50, ge=1, le=MAX_LINES),
    before: str | None = Query(default=None, max_length=100),
) -> ChannelMessagesResponse:
    """The channel's timeline: who said what, when and where, oldest first."""
    channels = ChannelRepository(uow.session)
    channel = await _visible(
        channels, pod_id=pod_id, channel_id=channel_id, user_id=user.id
    )
    lines = await channels.recent(channel.id, limit=limit, before=_cursor(before))
    first = lines[0] if len(lines) == limit else None
    return ChannelMessagesResponse(
        items=[message_response(line) for line in lines],
        next_before=f"{first.created_at.isoformat()}|{first.id}" if first else None,
    )


@router.post(
    "/{channel_id}/read",
    operation_id="channel.mark_read",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def mark_channel_read(
    pod_id: UUID, channel_id: UUID, user: CurrentUser, uow: UoWDep
) -> None:
    """Everything in the channel up to now has been read by this member."""
    channels = ChannelRepository(uow.session)
    channel = await _visible(
        channels, pod_id=pod_id, channel_id=channel_id, user_id=user.id
    )
    await channels.mark_read(channel=channel, user_id=user.id)
    await uow.commit()


@router.put(
    "/{channel_id}/handler",
    operation_id="channel.handler.take",
    response_model=ChannelResponse,
    dependencies=[require_action(Permissions.CONTACT_MESSAGE)],
)
async def take_channel(
    pod_id: UUID, channel_id: UUID, user: CurrentUser, uow: UoWDep
) -> ChannelResponse:
    """Answer a contact by hand: their agent stays quiet until it is handed back."""
    return await _set_handler(uow, pod_id, channel_id, user.id, handler=user.id)


@router.delete(
    "/{channel_id}/handler",
    operation_id="channel.handler.release",
    response_model=ChannelResponse,
    dependencies=[require_action(Permissions.CONTACT_MESSAGE)],
)
async def release_channel(
    pod_id: UUID, channel_id: UUID, user: CurrentUser, uow: UoWDep
) -> ChannelResponse:
    """Hand the contact back to the channel's agents."""
    return await _set_handler(uow, pod_id, channel_id, user.id, handler=None)


async def _set_handler(
    uow: UoWDep,
    pod_id: UUID,
    channel_id: UUID,
    user_id: UUID,
    *,
    handler: UUID | None,
) -> ChannelResponse:
    channels = ChannelRepository(uow.session)
    channel = await _visible(
        channels, pod_id=pod_id, channel_id=channel_id, user_id=user_id
    )
    if channel.kind is not ChannelKind.CONTACT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Only a contact's channel is answered by hand",
        )
    await channels.set_handler(channel.id, handler)
    await uow.commit()
    updated = await channels.get(channel.id)
    if updated is None:  # pragma: no cover - read in this request
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return channel_response(updated)
