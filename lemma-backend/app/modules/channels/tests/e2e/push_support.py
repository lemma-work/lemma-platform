"""Running the pushes a test's lines cause, and reading what they became.

A line's pushes are worked out after it is committed (``channels.pushes``),
so a test that posts and then reads its pushes runs that work in between --
that work and nothing else, so no platform is called.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.channels.contracts import Delivery
from app.modules.channels.contracts.deliveries import deliveries_of
from app.modules.channels.events.push_work import register_push_work
from app.modules.channels.infrastructure.models import ChannelPushPendingModel


async def run_pushes() -> int:
    """Fold every posted line into its push windows, and send the windows
    that are due; how many items that was."""
    registry = WorkRegistry()
    register_push_work(registry)
    return await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )


async def pushes_of(message_id: str | UUID) -> dict[str, Delivery]:
    """The pushes a line leads, by member, once the pushes have run."""
    await run_pushes()
    async with create_uow_from_session_maker(async_session_maker) as uow:
        rows = await deliveries_of(uow, [UUID(str(message_id))])
    return {str(row.recipient_user_id): row for row in rows if row.recipient_user_id}


async def pushed(message_id: str | UUID) -> set[str]:
    return set(await pushes_of(message_id))


async def waiting(channel_id: str | UUID, user_id: str | UUID) -> int:
    """How many lines of the channel wait to be pushed to the member."""
    async with async_session_maker() as session:
        lines = await session.scalar(
            select(ChannelPushPendingModel.lines).where(
                ChannelPushPendingModel.channel_id == UUID(str(channel_id)),
                ChannelPushPendingModel.recipient_user_id == UUID(str(user_id)),
            )
        )
    return int(lines or 0)


async def window_ends_now(channel_id: str | UUID, user_id: str | UUID) -> None:
    """The member's push window of the channel has run out: what waits in it
    is due, and so is the work that sends it."""
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE channel_push_pending SET next_attempt_at = now() "
                "WHERE channel_id = :channel AND recipient_user_id = :user "
                "AND next_attempt_at IS NOT NULL"
            ),
            {"channel": UUID(str(channel_id)), "user": UUID(str(user_id))},
        )
        await session.execute(
            text(
                "UPDATE work_items SET run_at = now() WHERE kind = 'channels.push_window' "
                "AND state = 'queued'"
            )
        )
        await session.commit()


__all__ = ["pushed", "pushes_of", "run_pushes", "waiting", "window_ends_now"]
