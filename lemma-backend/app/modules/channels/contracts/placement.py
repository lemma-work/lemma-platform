"""Where an answer goes, for whoever posts it (``domain/placement``).

Every answer a run gives -- in Lemma or at a platform place -- is placed here:
its thread, the line it answers, and the place it is sent to. A heard line's
own thread is found here too, so a Slack or Teams thread is the same thread
whether a line was heard in it or answers into it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts.lines import thread_root_at
from app.modules.channels.contracts.places import ReplyAddress
from app.modules.channels.domain.entities import ChannelMessage, Source
from app.modules.channels.domain.placement import (
    THREADED,
    Placement,
    answer_in_lemma,
    answer_to_heard,
    keeps_threads,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import SourceRepository


def threads(source: Source) -> bool:
    """Whether ``source`` keeps threads (``domain/placement.THREADED``)."""
    return keeps_threads(kind=source.kind, platform=source.platform)


async def answer_placement(
    uow: SqlAlchemyUnitOfWork, line: ChannelMessage, *, source: Source | None = None
) -> Placement:
    """Where an answer to ``line`` goes. ``source``: the line's own place,
    when the caller holds it already."""
    sources = SourceRepository(uow.session)
    if line.source_id is not None:
        heard_at = source or await sources.get(line.source_id)
        threaded = heard_at is not None and threads(heard_at)
        return answer_to_heard(
            line,
            threads=threaded,
            # The line its platform thread hangs from -- the line itself, when
            # it opened the thread the answer goes into.
            thread_root_id=(
                await thread_root_at(
                    uow,
                    source_id=line.source_id,
                    external_thread_id=line.external_thread_id,
                )
                if threaded and line.thread_root_id is None
                else None
            ),
        )
    channel = await ChannelRepository(uow.session).get(line.channel_id)
    carried = await sources.get(line.place_id) if line.place_id is not None else None
    return answer_in_lemma(
        channel.kind if channel is not None else None,
        line,
        place_threads=carried is not None and threads(carried),
    )


async def placement_at(uow: SqlAlchemyUnitOfWork, address: ReplyAddress) -> Placement:
    """Where an answer goes that answers no line in particular -- a run woken
    by somebody else's answers, or asked in the conversation itself -- at the
    place its conversation is answered: in the thread of what it last heard
    there, where the place keeps threads."""
    source = address.source
    thread_id = (address.ref or {}).get("external_thread_id")
    root = (
        await thread_root_at(
            uow,
            source_id=source.id,
            external_thread_id=thread_id if isinstance(thread_id, str) else None,
        )
        if threads(source)
        else None
    )
    return Placement(thread_root_id=root, reply_to_id=None, target_source_id=source.id)


async def heard_thread_root(
    uow: SqlAlchemyUnitOfWork,
    source: Source,
    *,
    external_thread_id: str | None,
    external_message_id: str | None,
) -> UUID | None:
    """The line a heard message's platform thread hangs from, where its place
    keeps threads; None for a message that opens a thread, or is no reply."""
    if not threads(source):
        return None
    return await thread_root_at(
        uow,
        source_id=source.id,
        external_thread_id=external_thread_id,
        external_message_id=external_message_id,
    )


__all__ = [
    "THREADED",
    "Placement",
    "answer_placement",
    "heard_thread_root",
    "placement_at",
    "threads",
]
