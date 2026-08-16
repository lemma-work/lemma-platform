"""PostgreSQL LISTEN/NOTIFY wake-up for the transactional outbox dispatcher.

The dispatcher's idle poll is what sets end-to-end event latency. It backs off
to 5s when idle, and at our volume it is almost always idle, so most events wait
for a poll that is already at its ceiling: measured p50 1.26s and p90 5.02s
across 44,536 production events. Agent dispatch has no fast path at all -- an
``agent.run.started`` event is the only thing that hands a run to the worker --
so that wait is time the user spends staring at nothing.

A ``NOTIFY`` issued inside the same transaction that stages the outbox row lets
the dispatcher wake on arrival instead of on a timer.

The notification is a hint, never a delivery:

* It carries no payload. The outbox row remains the only statement of what
  happened; the wake only says "look now".
* PostgreSQL discards notifications issued while nothing is listening, and does
  not replay them to a listener that reconnects. Verified against 18.4 rather
  than assumed. Every wake lost while this listener is down is lost for good.

So the fallback poll is not optional and is never removed. ``LISTEN`` only makes
it rare for the poll to be the thing that delivers. On every (re)connect the
listener sets the wake once, unconditionally, because the gap it just closed may
have hidden rows that nothing will notify about again.
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.events.config import event_transport_settings
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident
from app.core.request_context import create_background_task


logger = get_logger(__name__)

#: Channel every outbox producer notifies and the dispatcher listens on. A
#: PostgreSQL identifier, and a constant -- never interpolated from input.
OUTBOX_WAKE_CHANNEL = "lemma_outbox_wake"

_DRIVER_PREFIX = re.compile(r"^postgresql\+\w+://")


async def notify_outbox_wake(session: AsyncSession) -> None:
    """Signal the dispatcher, in the caller's transaction.

    Must be called inside the transaction that stages the outbox rows, not
    after it. PostgreSQL delivers a notification at commit or not at all, so
    this cannot wake a dispatcher for a row a rollback erased, and cannot wake
    one before the row it refers to is visible. Both verified against 18.4.

    ``pg_notify`` rather than ``NOTIFY`` so the channel travels as a bound
    parameter instead of being interpolated into SQL.

    Identical notifications within one transaction collapse into a single
    delivery, so a unit of work staging a hundred rows sends one wake.
    """
    await session.execute(
        text("SELECT pg_notify(:channel, '')"), {"channel": OUTBOX_WAKE_CHANNEL}
    )


def asyncpg_connect_kwargs(database_url: str, *, application_name: str) -> dict[str, Any]:
    """Translate a SQLAlchemy URL into raw asyncpg connect arguments.

    The listener cannot use the SQLAlchemy pool. ``pool_recycle`` is 300s, so a
    pooled connection would drop its ``LISTEN`` registration every five minutes
    and go quiet without erroring -- the dispatcher would silently revert to
    fallback-poll latency.
    """
    dsn = _DRIVER_PREFIX.sub("postgresql://", database_url)
    parts = urlsplit(dsn)
    query = parse_qs(parts.query)
    # SQLAlchemy spells this `ssl`; libpq spells it `sslmode`. asyncpg takes
    # either spelling's value as its `ssl` argument, but not as a DSN query
    # parameter, so it has to be lifted out.
    ssl_mode = (query.get("ssl") or query.get("sslmode") or [None])[0]
    kwargs: dict[str, Any] = {
        "dsn": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
        # Named so an operator can tell a listener from a pooled connection in
        # pg_stat_activity, which is the first thing you want during a
        # notification-queue incident.
        "server_settings": {
            "application_name": application_name,
            # The listener never opens a transaction. This is a backstop, not a
            # mechanism: a backend that holds LISTEN open inside an idle
            # transaction is the one failure mode that can stall the shared
            # notification queue and start failing NOTIFY for everyone.
            "idle_in_transaction_session_timeout": "60000",
        },
    }
    if ssl_mode:
        kwargs["ssl"] = ssl_mode
    return kwargs


class OutboxWakeListener:
    """Hold one dedicated connection and set ``wake`` on every notification."""

    def __init__(
        self,
        database_url: str,
        *,
        label: str = "outbox",
        channel: str = OUTBOX_WAKE_CHANNEL,
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
            f"outbox.wake_listener.{label}", logger=logger
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
                    timeout=event_transport_settings.outbox_listen_connect_timeout_seconds,
                )
                await self._serve(connection)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - long-lived process boundary
                failures += 1
                self._incident.record_failure(error_type=type(exc).__name__)
                delay = min(30.0, 2 ** min(failures - 1, 5)) * random.uniform(0.75, 1.25)
                await asyncio.sleep(delay)
            finally:
                if connection is not None:
                    try:
                        await connection.close(timeout=5)
                    except (asyncio.CancelledError, Exception):
                        # A listener that cannot close is a listener that is
                        # already gone; reconnecting is the response either way.
                        pass

    async def _serve(self, connection: asyncpg.Connection) -> None:
        terminated = asyncio.Event()
        connection.add_termination_listener(lambda _conn: terminated.set())
        await connection.add_listener(self._channel, self._on_notify)
        # Anything committed while this listener was down notified nobody, and
        # PostgreSQL will not replay it. Force one full dispatch pass rather
        # than trusting that the gap was empty.
        self.wake.set()
        self._incident.record_success()
        logger.debug(
            "infrastructure.outbox_wake.listener_connected.observed",
            label=self._label,
        )
        health_interval = event_transport_settings.outbox_listen_health_interval_seconds
        while not terminated.is_set():
            try:
                await asyncio.wait_for(terminated.wait(), timeout=health_interval)
            except TimeoutError:
                # A silently dead socket does not fire the termination listener.
                # This is the cheapest thing that proves the connection can
                # still carry a notification.
                await connection.execute("SELECT 1")


@asynccontextmanager
async def outbox_wake_listener_lifespan(
    database_url: str, *, label: str = "outbox"
) -> AsyncIterator[OutboxWakeListener]:
    listener = OutboxWakeListener(database_url, label=label)
    task = create_background_task(listener.run(), name=f"outbox-wake-listener-{label}")
    try:
        yield listener
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
