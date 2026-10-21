"""Lines, read for runs and agent tools: one line, a member's view of the
pod's channels, and what a run is shown of a channel around a line.

A member's view always goes through the visibility rule (``Viewer``): an
agent working for a member sees exactly the channels that member could open.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.access import Viewer, may_see
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Source,
)
from app.modules.channels.infrastructure.line_answers import LineAnswers
from app.modules.channels.infrastructure.line_search import LineSearch, SearchFilter
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.pod.contracts.members import pod_member_holds

#: The most channels a member's agent is shown at once.
MAX_CHANNELS = 100

__all__ = [
    "channels_for",
    "last_heard_line",
    "line_by_id",
    "member_background",
    "page_lines",
    "search_lines",
    "thread_root_at",
    "viewer_of",
    "visible_channel",
]


async def viewer_of(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID
) -> Viewer:
    """A member as the visibility rule sees them."""
    return Viewer(
        user_id=user_id,
        pod_id=pod_id,
        sees_contacts=await pod_member_holds(uow, pod_id, user_id, "contact.read"),
    )


async def visible_channel(
    uow: SqlAlchemyUnitOfWork, *, viewer: Viewer, channel_id: UUID
) -> Channel | None:
    """The channel, when ``viewer`` may see it."""
    channel = await ChannelRepository(uow.session).get(channel_id)
    if channel is None:
        return None
    is_member = (
        await MemberRepository(uow.session).of_user(channel.id, viewer.user_id)
        is not None
    )
    return channel if may_see(channel, viewer, is_member=is_member) else None


async def line_by_id(
    uow: SqlAlchemyUnitOfWork, message_id: UUID
) -> ChannelMessage | None:
    return await LineRepository(uow.session).get(message_id)


async def last_heard_line(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> ChannelMessage | None:
    """The latest line a person said in this conversation -- in Lemma or on a
    platform -- which is where its answer belongs."""
    return await LineAnswers(uow.session).last_heard(conversation_id)


async def thread_root_at(
    uow: SqlAlchemyUnitOfWork,
    *,
    source_id: UUID,
    external_thread_id: str | None,
    external_message_id: str | None = None,
) -> UUID | None:
    """The line a platform thread hangs from, so its replies are a thread here."""
    if not external_thread_id or external_thread_id == external_message_id:
        return None
    return await LineRepository(uow.session).at(source_id, external_thread_id)


async def member_background(
    uow: SqlAlchemyUnitOfWork, *, line: ChannelMessage, limit: int
) -> list[ChannelMessage]:
    """The lines a member's run answering ``line`` is shown of the channel
    around it: the thread it is in, or the channel's recent lines. Oldest
    first, without ``line`` itself or a deleted line.

    Chosen, not yet read: what of them the member's run may see -- a
    contact's words marked as said outside the pod, nothing an answer made
    with another member's access said -- is decided as the run reads them
    (``contracts.backgrounds``).
    """
    lines = LineRepository(uow.session)
    root_id = line.thread_root_id
    if root_id is not None:
        root = await lines.get(root_id)
        around = ([root] if root else []) + await lines.thread(root_id, limit=limit)
    else:
        around = await lines.page(line.channel_id, limit=limit)
    return [each for each in around if each.id != line.id and not each.is_deleted][
        -limit:
    ]


async def channels_for(
    uow: SqlAlchemyUnitOfWork,
    *,
    viewer: Viewer,
    kind: ChannelKind | None = None,
    joined_only: bool = False,
) -> list[tuple[Channel, list[Source]]]:
    """The channels ``viewer`` sees, most recently active first, with their places."""
    channels = await ChannelRepository(uow.session).list_for(
        viewer, kind=kind, joined_only=joined_only, limit=MAX_CHANNELS
    )
    sources = await SourceRepository(uow.session).of_channels(
        [channel.id for channel in channels]
    )
    return [(channel, sources.get(channel.id, [])) for channel in channels]


async def page_lines(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    limit: int,
    before_seq: int | None = None,
    after_seq: int | None = None,
) -> list[ChannelMessage]:
    """A channel's lines outside threads, oldest first."""
    return await LineRepository(uow.session).page(
        channel_id, limit=limit, before_seq=before_seq, after_seq=after_seq
    )


async def search_lines(
    uow: SqlAlchemyUnitOfWork,
    *,
    viewer: Viewer,
    words: str,
    limit: int,
    channel_id: UUID | None = None,
    before: tuple[datetime, UUID] | None = None,
) -> list[ChannelMessage]:
    """Lines matching every word in channels ``viewer`` sees and reads in
    full, newest first."""
    hits = await LineSearch(uow.session).search(
        viewer,
        query=words,
        limit=limit,
        within=SearchFilter(channel_id=channel_id),
        before=before,
    )
    return [hit.message for hit in hits]
