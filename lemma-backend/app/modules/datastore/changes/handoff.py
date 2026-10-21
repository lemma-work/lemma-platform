"""From a watched write's wake row to ``changes.deliver`` items in the main database.

The two databases cannot share a transaction, so the handoff is ordered to make
every crash repeat a step rather than lose one: read the wake rows, commit the
deliver items (coalesced per subscriber by their dedup key), and only then
delete exactly the rows that were read. A write that commits in between leaves
a row of its own for the next pass, so a wake is never swallowed.

Each worker runs one handoff loop. Two loops reading the same rows enqueue the
same coalesced items, so they need no lock. The loop wakes on ``NOTIFY`` from a
direct, non-pooled connection to the datastore database (a pooled one is
recycled and silently drops its ``LISTEN``) and polls as a fallback.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.infrastructure.db.notify_listener import NotifyWakeListener
from app.core.infrastructure.work import WorkWriter
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident
from app.core.request_context import create_background_task
from app.modules.datastore.changes.schema import PENDING_CHANNEL, PENDING_TABLE
from app.modules.datastore.changes.subscribers import SubscriberTable
from app.modules.datastore.changes.work import request_delivery
from app.modules.datastore.config import datastore_settings

logger = get_logger(__name__)

#: Wake rows taken per pass.
HANDOFF_BATCH = 1000

type SessionMaker = Callable[[], AsyncSession]


class ChangeHandoff:
    def __init__(self, engine: AsyncEngine, main_sessions: SessionMaker) -> None:
        self._engine = engine
        self._main_sessions = main_sessions
        self._incident = DependencyIncident("datastore.changes.handoff", logger=logger)

    async def pass_once(self) -> int:
        """Hand off one batch of wake rows; how many rows it consumed."""
        async with self._engine.connect() as conn:
            if not await conn.scalar(
                text(f"SELECT to_regclass('{PENDING_TABLE}') IS NOT NULL")
            ):
                return 0
            rows = (
                await conn.execute(
                    text(
                        f"SELECT id, pod_id FROM {PENDING_TABLE} ORDER BY id LIMIT :limit"
                    ),
                    {"limit": HANDOFF_BATCH},
                )
            ).all()
        if not rows:
            return 0
        pods = sorted({UUID(str(row[1])) for row in rows})
        wanted: list[tuple[UUID, str, float]] = []
        for pod_id in pods:
            for subscriber in await SubscriberTable(pod_id, engine=self._engine).all():
                wanted.append(
                    (pod_id, subscriber.subscriber, subscriber.delay.total_seconds())
                )
        async with self._main_sessions() as session:
            work = WorkWriter(session)
            for pod_id, key, delay in wanted:
                await request_delivery(
                    work,
                    pod_id,
                    key,
                    delay=None if delay <= 0 else timedelta(seconds=delay),
                )
            await session.commit()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(f"DELETE FROM {PENDING_TABLE} WHERE id = ANY(:ids)"),
                {"ids": [int(row[0]) for row in rows]},
            )
        return len(rows)

    async def drain(self) -> int:
        total = 0
        while True:
            taken = await self.pass_once()
            total += taken
            if taken < HANDOFF_BATCH:
                return total

    async def run(self, wake: asyncio.Event) -> None:
        poll = datastore_settings.datastore_change_handoff_poll_seconds
        while True:
            try:
                await asyncio.wait_for(wake.wait(), timeout=poll)
            except TimeoutError:
                pass
            wake.clear()
            try:
                await self.drain()
            except (SQLAlchemyError, OSError) as exc:
                # The rows stay; the next pass hands them off.
                self._incident.record_failure(error_type=type(exc).__name__)
            else:
                self._incident.record_success()


@asynccontextmanager
async def change_handoff_lifespan(
    engine: AsyncEngine, main_sessions: SessionMaker, *, database_url: str
) -> AsyncIterator[ChangeHandoff]:
    handoff = ChangeHandoff(engine, main_sessions)
    listener = NotifyWakeListener(
        database_url, label="change-pending", channel=PENDING_CHANNEL
    )
    listen = create_background_task(listener.run(), name="change-pending-listener")
    loop = create_background_task(handoff.run(listener.wake), name="change-handoff")
    try:
        yield handoff
    finally:
        for task in (loop, listen):
            task.cancel()
        for task in (loop, listen):
            try:
                await task
            except asyncio.CancelledError:
                pass


__all__ = ["HANDOFF_BATCH", "ChangeHandoff", "change_handoff_lifespan"]
