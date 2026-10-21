"""What one line costs the channel's row, and how its live log behaves.

A line takes its number under the channel row's lock, and everything the post
changes on that row -- the head, the change counter, a seat or a case moving
-- is one ``UPDATE channels``: every extra write of that row was another
version of the hottest row of the pod, made under the lock every other writer
of the channel waits on. A reply in a thread is counted beside its root
(``channel_threads``) and logged as the root's change, never by rewriting the
root. What a live subscriber resumes from is the change log, kept for
``revisions.CHANGE_RETENTION``; a cursor from before what is kept is told to
refetch.
"""

from __future__ import annotations

import re
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import speakers_in
from app.modules.channels.infrastructure.change_models import (
    ChannelChangeModel,
    ChannelThreadModel,
)
from app.modules.channels.infrastructure.revisions import prune_changes
from app.modules.channels.services.live.events import changed_heads
from app.modules.channels.tests.e2e.channel_pod import Pod, contact_place, group, say
from app.modules.test_support.e2e.live_socket import live_socket
from app.modules.test_support.e2e.runtime import backend_server as _backend_server
from app.modules.test_support.query_counting import counted_queries

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server


def _writes_of(statements: list[str], table: str) -> list[str]:
    """The statements that update ``table`` -- on their own or in a CTE."""
    pattern = re.compile(rf"\bUPDATE {table}\b")
    return [each for each in statements if pattern.search(" ".join(each.split()))]


async def test_a_member_line_to_a_contact_writes_its_channel_once(
    pod: Pod, db_session: AsyncSession
) -> None:
    """The member writing steps in -- seated, assigned, the case waiting on
    the contact -- and the channel's row is written once for all of it, the
    first line and every one after."""
    source, _ = await contact_place(db_session, pod)
    for text_ in ("On it, Dana", "Shipped today"):
        with counted_queries() as statements:
            await say(pod, source.channel_id, text_, as_user=pod.editor)
        written = _writes_of(statements, "channels")
        assert len(written) == 1, (
            f"{text_!r} wrote its channel {len(written)} times:\n"
            + "\n".join(each[:160] for each in written)
        )


async def test_a_thread_reply_writes_no_other_line(
    pod: Pod, db_session: AsyncSession
) -> None:
    """The root is sent again with its count, from the thread's own row and
    one change logged for it: the root itself is not written."""
    channel = await group(pod, title="Threads")
    root = await say(pod, channel["id"], "release notes?")
    with counted_queries() as statements:
        await say(pod, channel["id"], "drafting", thread_root_id=root["id"])
        await say(pod, channel["id"], "done", thread_root_id=root["id"])
    assert _writes_of(statements, "channel_messages") == []
    assert len(_writes_of(statements, "channels")) == 2

    page = await pod.client().get(f"{pod.base}/{channel['id']}/messages")
    assert page.status_code == 200, page.text
    (shown,) = [line for line in page.json()["items"] if line["id"] == root["id"]]
    assert shown["reply_count"] == 2
    async with async_session_maker() as session:
        thread = await session.get(ChannelThreadModel, UUID(root["id"]))
        root_changes = await session.scalar(
            select(func.count()).where(
                ChannelChangeModel.message_id == UUID(root["id"])
            )
        )
    assert thread is not None and thread.reply_count == 2
    # Written once, and once more for each reply.
    assert root_changes == 3


async def test_the_idle_poll_finds_a_change_by_its_log(
    pod: Pod, db_session: AsyncSession
) -> None:
    """The poll that backs up a lost signal finds the channels with a change
    logged since -- by the database's clock -- and no others."""
    quiet = await group(pod, title="Quiet")
    busy = await group(pod, title="Busy")
    since = await db_session.scalar(select(func.now()))
    await db_session.commit()
    await say(pod, busy["id"], "news")
    async with async_session_maker() as session:
        moved = await changed_heads(SqlAlchemyUnitOfWork(session), UUID(pod.id), since)
    assert str(next(iter(moved))) == busy["id"] and len(moved) == 1
    assert UUID(quiet["id"]) not in moved


async def test_the_log_forgets_only_what_is_old(
    pod: Pod, db_session: AsyncSession
) -> None:
    channel = await group(pod, title="Aging")
    for n in range(3):
        await say(pod, channel["id"], f"line {n}")
    channel_id = UUID(channel["id"])
    async with async_session_maker() as session:
        revs = list(
            await session.scalars(
                select(ChannelChangeModel.rev)
                .where(ChannelChangeModel.channel_id == channel_id)
                .order_by(ChannelChangeModel.rev)
            )
        )
        await session.execute(
            update(ChannelChangeModel)
            .where(
                ChannelChangeModel.channel_id == channel_id,
                ChannelChangeModel.rev < revs[-1],
            )
            .values(at=func.now() - timedelta(days=30))
        )
        await session.commit()
    async with async_session_maker() as session:
        pruned = await prune_changes(session, limit=1000)
        await session.commit()
        kept = list(
            await session.scalars(
                select(ChannelChangeModel.rev).where(
                    ChannelChangeModel.channel_id == channel_id
                )
            )
        )
    assert pruned >= len(revs) - 1
    assert kept == [revs[-1]]


async def test_a_cursor_older_than_the_log_is_told_to_refetch(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Long away")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"])
        said = await say(pod, channel["id"], "before")
        await live.event("c", "line.created", message__id=said["id"])
        cursor = live.cursor("c")
    await say(pod, channel["id"], "while away")
    async with async_session_maker() as session:
        # Everything up to the cursor's next change was pruned.
        await session.execute(
            text(
                "UPDATE channel_changes SET at = now() - interval '30 days' "
                "WHERE channel_id = :channel"
            ),
            {"channel": channel["id"]},
        )
        await prune_changes(session, limit=1000)
        await session.commit()
    await say(pod, channel["id"], "kept")

    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"], since=cursor)
        resync = await live.until("resync", "c")
        assert resync["reason"] == "cursor_expired"


async def test_speakers_are_read_from_their_seats(
    pod: Pod, db_session: AsyncSession
) -> None:
    """Who spoke, newest first, under the name they last spoke under -- one
    read of the seats, none of the lines."""
    channel = await group(pod, title="Speakers")
    await say(pod, channel["id"], "first", as_user=pod.user)
    await say(pod, channel["id"], "second", as_user=pod.editor)
    with counted_queries() as statements:
        async with async_session_maker() as session:
            found = await speakers_in(
                SqlAlchemyUnitOfWork(session), [UUID(channel["id"])]
            )
    assert [each.principal.user_id for each in found][:2] == [
        UUID(pod.editor["id"]),
        UUID(pod.user["id"]),
    ]
    assert not [each for each in statements if "channel_messages" in each]
