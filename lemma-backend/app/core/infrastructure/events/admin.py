"""Operator CLI for inspecting and replaying durable event delivery."""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.session import close_engine, get_session_maker
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


async def _run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--dead-only", action="store_true")
    list_parser.add_argument("--limit", type=int, default=100)
    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("event_id", type=UUID)
    args = parser.parse_args()

    try:
        if args.command == "list":
            await _list_events(dead_only=args.dead_only, limit=args.limit)
        else:
            replayed = await replay_outbox_event(get_session_maker(), args.event_id)
            if not replayed:
                raise SystemExit(f"Event {args.event_id} not found")
            logger.info("Outbox event replay requested", event_id=str(args.event_id), audit=True)
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(_run())
