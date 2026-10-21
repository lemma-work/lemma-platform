"""Live sockets end with the session that opened them.

A socket is authenticated once, at its handshake; nothing on it is a request
that would present the session again. So a session that ends -- signed out,
revoked (a password reset, an account deactivated), or simply expired -- has
to reach the sockets it opened:

* **Revoked.** Whatever revokes sessions announces it (``announce_ended``) on
  one Redis channel, ``live:sessions:ended``. Each API process listens once
  (``OpenSockets``) and closes every socket it holds for those sessions with
  ``4401``. A missed announcement is caught by the next check below.
* **Expired, or revoked unannounced.** Once the socket's access token has
  expired, the session is looked up every ``SESSION_RECHECK_SECONDS``; a
  session that is gone closes it with ``4401`` too. A session that lives on
  keeps its socket: a client reconnecting hourly to present a fresh token
  would lose its streams for no reason.

``4401`` asks the client to refresh its session and reconnect, which a signed
out client cannot do.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from uuid import UUID

from redis.exceptions import RedisError
from supertokens_python.exceptions import SuperTokensError

from app.core.config import settings
from app.core.domain.realtime import RealtimeSlowConsumerError
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.infrastructure.redis.client import get_redis
from app.core.live.connection import LiveConnection
from app.core.live.protocol import CLOSE_UNAUTHENTICATED
from app.core.log.log import get_logger
from app.core.request_context import create_background_task

logger = get_logger(__name__)

ENDED_CHANNEL = "live:sessions:ended"
#: After its access token expires, how often a socket's session is looked up.
SESSION_RECHECK_SECONDS = 300.0
_ENDED_REASON = "Your session ended. Sign in again."


@dataclass(frozen=True, slots=True)
class LiveSession:
    """Who opened a socket, and the session they opened it with."""

    user_id: UUID
    #: The SuperTokens session handle; ``None`` when the token named none.
    handle: str | None = None
    #: When the handshake's access token expires (epoch seconds), if known.
    access_expires_at: float | None = None


#: Whether a session (by handle) still exists.
type SessionLookup = Callable[[str], Awaitable[bool]]


async def _session_exists(handle: str) -> bool:
    from supertokens_python.recipe.session.asyncio import get_session_information

    return await get_session_information(handle) is not None


class SessionWatch:
    """When to look a socket's session up again, and the lookup."""

    def __init__(
        self,
        session: LiveSession,
        *,
        lookup: SessionLookup = _session_exists,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session = session
        self._lookup = lookup
        self._clock = clock
        self._next = session.access_expires_at

    async def still_valid(self) -> bool:
        """False once the session is known to be gone. A lookup that fails
        keeps the socket: it is tried again next time, and a revocation that
        was announced has closed the socket already."""
        handle = self._session.handle
        if handle is None or self._next is None or self._clock() < self._next:
            return True
        self._next = self._clock() + SESSION_RECHECK_SECONDS
        try:
            return await self._lookup(handle)
        except (SuperTokensError, OSError) as exc:
            logger.warning(
                "live.session_check_unavailable.degraded",
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return True


def end_for_session(connection: LiveConnection) -> None:
    connection.close_with(CLOSE_UNAUTHENTICATED, _ENDED_REASON)


class OpenSockets:
    """This process's open sockets by session, and the listener that ends
    them when their session is announced ended."""

    def __init__(self, *, listen: bool = True) -> None:
        #: Whether to hear announcements on Redis; off for a test of the index.
        self._listens = listen
        self._by_handle: dict[str, set[LiveConnection]] = {}
        self._by_user: dict[UUID, set[LiveConnection]] = {}
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def hold(self, connection: LiveConnection, session: LiveSession) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # A test that started a fresh loop starts fresh state.
            self._by_handle, self._by_user, self._task = {}, {}, None
            self._loop = loop
        if session.handle is not None:
            self._by_handle.setdefault(session.handle, set()).add(connection)
        self._by_user.setdefault(session.user_id, set()).add(connection)
        if self._listens and (self._task is None or self._task.done()):
            self._task = create_background_task(
                self._listen(), name="live-sessions-ended"
            )

    def release(self, connection: LiveConnection, session: LiveSession) -> None:
        if session.handle is not None:
            _discard(self._by_handle, session.handle, connection)
        _discard(self._by_user, session.user_id, connection)

    def ended(self, *, handles: Iterable[str] = (), user_id: UUID | None = None) -> int:
        """Close the sockets of these sessions (every session of ``user_id``)."""
        doomed: set[LiveConnection] = set()
        for handle in handles:
            doomed |= self._by_handle.get(handle, set())
        if user_id is not None:
            doomed |= self._by_user.get(user_id, set())
        for connection in doomed:
            end_for_session(connection)
        return len(doomed)

    def _dispatch(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except ValueError, TypeError:
            return
        if not isinstance(message, dict):
            return
        handles = message.get("handles")
        user: UUID | None = None
        with contextlib.suppress(ValueError, TypeError):
            user = UUID(str(message["user_id"])) if message.get("user_id") else None
        self.ended(
            handles=[str(h) for h in handles] if isinstance(handles, list) else (),
            user_id=user,
        )

    async def _listen(self) -> None:
        backoff = 0.25
        while True:
            try:
                service = await get_channel_service()
                async with service.subscribe([ENDED_CHANNEL]) as messages:
                    backoff = 0.25
                    async for message in messages:
                        self._dispatch(message)
            except asyncio.CancelledError:
                raise
            except RealtimeSlowConsumerError:
                continue
            # RuntimeError: the process-wide Pub/Sub service stopped under us.
            except (RedisError, OSError, RuntimeError) as exc:
                # Sockets whose session ends meanwhile are closed by their
                # own session check once their token has expired.
                logger.warning(
                    "live.sessions_listener_lost.degraded",
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)


def _discard[K](
    table: dict[K, set[LiveConnection]], key: K, connection: LiveConnection
) -> None:
    held = table.get(key)
    if held is not None:
        held.discard(connection)
        if not held:
            table.pop(key, None)


_open = OpenSockets()


def open_sockets() -> OpenSockets:
    return _open


def _ended_payload(handles: Iterable[str], user_id: UUID | str | None) -> str:
    # A few session handles: encoded in place, off any large payload's path.
    return json.dumps(
        {
            "handles": sorted(set(handles)),
            "user_id": str(user_id) if user_id is not None else None,
        }
    )


async def announce_ended(
    *, handles: Iterable[str] = (), user_id: UUID | str | None = None
) -> None:
    """Tell every API process these sessions ended (every session of
    ``user_id``): their live sockets close with ``4401``. Best effort: a socket
    that misses it is closed by its own session check."""
    payload = _ended_payload(handles, user_id)
    try:
        await get_redis(url=settings.redis_url).publish(ENDED_CHANNEL, payload)
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.session_end_unannounced.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )


__all__ = [
    "ENDED_CHANNEL",
    "LiveSession",
    "OpenSockets",
    "SESSION_RECHECK_SECONDS",
    "SessionLookup",
    "SessionWatch",
    "announce_ended",
    "end_for_session",
    "open_sockets",
]
