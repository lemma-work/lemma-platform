"""Operator CLI for inspecting and replaying durable event delivery."""

from __future__ import annotations

import argparse
import asyncio
import time
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.session import close_engine, get_session_maker
from app.core.infrastructure.events.gap_replay import (
    replay_window,
    requeue_outbox_window,
)
from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.events.outbox import replay_outbox_event
from app.core.log.log import get_logger


logger = get_logger(__name__)


async def _list_events(*, dead_only: bool, limit: int) -> None:
    session_maker = get_session_maker()
    async with session_maker() as session:
        order = (
            (DomainEventOutbox.dead_lettered_at.desc(), DomainEventOutbox.id.desc())
            if dead_only
            else (DomainEventOutbox.occurred_at.desc(), DomainEventOutbox.id.desc())
        )
        stmt = select(DomainEventOutbox).order_by(*order).limit(limit)
        if dead_only:
            stmt = stmt.where(DomainEventOutbox.dead_lettered_at.is_not(None))
        for row in (await session.scalars(stmt)).all():
            print(
                f"{row.id} {row.event_type} stream={row.stream} attempts={row.attempts} "
                f"published={row.published_at is not None} dead={row.dead_lettered_at is not None}"
            )


async def _replay_window(stream: str, after_ms: int, until_ms: int) -> None:
    """Re-queue every outbox row for ``stream`` published in the window.

    The same selection the stream guard's automatic replay makes, without its
    pacing: an operator running this has decided the consumer can take it.
    """
    lower, upper = replay_window(after_ms, until_ms, int(time.time() * 1000))
    session_maker = get_session_maker()
    total = 0
    while True:
        count = await requeue_outbox_window(
            session_maker, stream=stream, lower=lower, upper=upper, limit=1_000
        )
        total += count
        if count == 0:
            break
    print(f"re-queued {total} events on {stream}")


async def _run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--dead-only", action="store_true")
    list_parser.add_argument("--limit", type=int, default=100)
    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("event_id", type=UUID)
    window_parser = subparsers.add_parser(
        "replay-window",
        help=(
            "Re-publish one stream's events from a window a trim removed, as "
            "logged by redis.stream.unread_trimmed (after_ms / until_ms)."
        ),
    )
    window_parser.add_argument("stream")
    window_parser.add_argument("after_ms", type=int)
    window_parser.add_argument("until_ms", type=int)
    args = parser.parse_args()

    try:
        if args.command == "list":
            await _list_events(dead_only=args.dead_only, limit=args.limit)
        elif args.command == "replay-window":
            await _replay_window(args.stream, args.after_ms, args.until_ms)
        else:
            replayed = await replay_outbox_event(get_session_maker(), args.event_id)
            if not replayed:
                raise SystemExit(f"Event {args.event_id} not found")
            logger.debug(
                "infrastructure.admin.outbox_event_replay_requested.observed",
                event_id=str(args.event_id),
            )
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(_run())
