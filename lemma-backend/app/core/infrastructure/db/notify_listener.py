"""A PostgreSQL LISTEN that sets an ``asyncio.Event`` on every notification.

One dedicated, direct connection (a pooled one is recycled and silently loses
its LISTEN) per channel. A wake is a hint, never a delivery: PostgreSQL drops
notifications nobody is listening for and never replays them, so whoever waits
on ``wake`` keeps a fallback poll. On every (re)connect the event is set once,
because work committed while the listener was down notified nobody. Used by
the record change log's handoff (``datastore/changes/handoff.py``).
"""

from __future__ import annotations

import asyncio
import random

import asyncpg

from app.core.infrastructure.db.direct_connection import asyncpg_connect_kwargs
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)

_CONNECT_TIMEOUT_SECONDS = 10
#: A silently dead socket fires no termination listener; this probes it.
_HEALTH_INTERVAL_SECONDS = 30.0


class NotifyWakeListener:
    """Hold one dedicated connection and set ``wake`` on every notification."""

    def __init__(
        self,
        database_url: str,
        *,
        label: str,
        channel: str,
    ) -> None:
        self._database_url = database_url
        self._label = label
        self._channel = channel
        self._connect_kwargs = asyncpg_connect_kwargs(
            database_url, application_name=f"lemma-{label}-wake-listener"
        )
        #: Set by a notification, by a (re)connect, and cleared by the consumer.
        self.wake = asyncio.Event()
        self._incident = DependencyIncident(
            f"notify.wake_listener.{label}", logger=logger
        )
        self._notifications = 0

    @property
    def notification_count(self) -> int:
        """Wakes received since start. Distinguishes 'quiet' from 'not working'."""
        return self._notifications

    def _on_notify(self, _connection, _pid, _channel, _payload) -> None:
        self._notifications += 1
        self.wake.set()

    async def run(self) -> None:
        failures = 0
        while True:
            connection = None
            try:
                connection = await asyncpg.connect(
                    **self._connect_kwargs,
                    timeout=_CONNECT_TIMEOUT_SECONDS,
                )
                await self._serve(connection)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - long-lived process boundary
                failures += 1
                self._incident.record_failure(error_type=type(exc).__name__)
                delay = min(30.0, 2 ** min(failures - 1, 5)) * random.uniform(
                    0.75, 1.25
                )
                await asyncio.sleep(delay)
            finally:
                if connection is not None:
                    try:
                        await connection.close(timeout=5)
                    except OSError, asyncpg.PostgresError, asyncpg.InterfaceError:
                        # A listener that cannot close is a listener that is
                        # already gone; reconnecting is the response either way.
                        logger.warning(
                            "infrastructure.notify_wake.close_failed.degraded",
                            label=self._label,
                            exc_info=True,
                        )

    async def _serve(self, connection: asyncpg.Connection) -> None:
        terminated = asyncio.Event()
        connection.add_termination_listener(lambda _conn: terminated.set())
        await connection.add_listener(self._channel, self._on_notify)
        # Anything committed while this listener was down notified nobody, and
        # PostgreSQL will not replay it. Force one full pass rather than
        # trusting that the gap was empty.
        self.wake.set()
        self._incident.record_success()
        logger.debug(
            "infrastructure.notify_wake.listener_connected.observed",
            label=self._label,
        )
        health_interval = _HEALTH_INTERVAL_SECONDS
        while not terminated.is_set():
            try:
                await asyncio.wait_for(terminated.wait(), timeout=health_interval)
            except TimeoutError:
                # A silently dead socket does not fire the termination listener.
                # This is the cheapest thing that proves the connection can
                # still carry a notification.
                await connection.execute("SELECT 1")
