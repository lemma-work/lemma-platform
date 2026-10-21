"""LISTEN work_<class> on one dedicated connection, waking each class's dispatcher.

A hint, never a delivery: PostgreSQL drops notifications nobody is listening
for and never replays them, so the dispatcher's poll stays. The connection is
direct, not pooled -- a pooled one is recycled and silently loses its LISTEN.
On every (re)connect each dispatcher is woken once, because work committed
while the listener was down notified nobody.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Mapping

import asyncpg

from app.core.infrastructure.db.direct_connection import asyncpg_connect_kwargs
from app.core.infrastructure.work.types import WorkClass
from app.core.infrastructure.work.writer import wake_channel
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)

_CONNECT_TIMEOUT_SECONDS = 10
_HEALTH_INTERVAL_SECONDS = 30.0


class WorkWakeListener:
    def __init__(
        self, database_url: str, wakers: Mapping[WorkClass, Callable[[], None]]
    ) -> None:
        self._connect_kwargs = asyncpg_connect_kwargs(
            database_url, application_name="lemma-work-listener"
        )
        self._by_channel = {wake_channel(cls): wake for cls, wake in wakers.items()}
        self._incident = DependencyIncident("work.wake_listener", logger=logger)
        self.notifications = 0

    def _on_notify(
        self, _connection: object, _pid: int, channel: str, _payload: str
    ) -> None:
        self.notifications += 1
        wake = self._by_channel.get(channel)
        if wake is not None:
            wake()

    def _wake_all(self) -> None:
        for wake in self._by_channel.values():
            wake()

    async def run(self) -> None:
        failures = 0
        while True:
            connection: asyncpg.Connection | None = None
            try:
                opened: asyncpg.Connection = await asyncpg.connect(
                    **self._connect_kwargs, timeout=_CONNECT_TIMEOUT_SECONDS
                )
                connection = opened
                await self._serve(opened)
                failures = 0
            except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
                failures += 1
                self._incident.record_failure(error_type=type(exc).__name__)
                await asyncio.sleep(
                    min(30.0, 2 ** min(failures - 1, 5)) * random.uniform(0.75, 1.25)
                )
            finally:
                if connection is not None and not connection.is_closed():
                    connection.terminate()

    async def _serve(self, connection: asyncpg.Connection) -> None:
        terminated = asyncio.Event()
        connection.add_termination_listener(lambda _conn: terminated.set())
        for channel in self._by_channel:
            await connection.add_listener(channel, self._on_notify)
        self._wake_all()
        self._incident.record_success()
        logger.info("work.listener.connected", channels=len(self._by_channel))
        while not terminated.is_set():
            try:
                await asyncio.wait_for(
                    terminated.wait(), timeout=_HEALTH_INTERVAL_SECONDS
                )
            except TimeoutError:
                # A silently dead socket never fires the termination listener.
                await connection.execute("SELECT 1")
