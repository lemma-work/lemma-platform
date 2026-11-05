"""A live socket ends with its session: announced, or found gone once its
access token has expired."""

from __future__ import annotations

from uuid import uuid4

import pytest
from supertokens_python.exceptions import GeneralError

from app.core.live.connection import LiveConnection
from app.core.live.protocol import CLOSE_UNAUTHENTICATED
from app.core.live.sessions import (
    SESSION_RECHECK_SECONDS,
    LiveSession,
    OpenSockets,
    SessionWatch,
)

pytestmark = pytest.mark.unit


async def _send(_text: str) -> None:
    return None


def _socket(user_id) -> LiveConnection:
    return LiveConnection(
        pod_id=uuid4(), user_id=user_id, send_text=_send, queue_bytes=1 << 20
    )


async def test_an_announced_session_closes_only_its_own_sockets() -> None:
    user, other = uuid4(), uuid4()
    sockets = OpenSockets(listen=False)
    mine = LiveSession(user_id=user, handle="h-mine")
    second = LiveSession(user_id=user, handle="h-second")
    theirs = LiveSession(user_id=other, handle="h-theirs")
    opened = {session: _socket(session.user_id) for session in (mine, second, theirs)}
    for session, connection in opened.items():
        sockets.hold(connection, session)

    sockets._dispatch('{"handles": ["h-mine"], "user_id": null}')
    assert opened[mine].close_code == CLOSE_UNAUTHENTICATED
    assert not opened[second].closed and not opened[theirs].closed

    # Every session of the user: a password reset.
    sockets.ended(user_id=user)
    assert opened[second].close_code == CLOSE_UNAUTHENTICATED
    assert not opened[theirs].closed
    sockets.release(opened[theirs], theirs)
    assert sockets.ended(handles=["h-theirs"]) == 0


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def test_a_session_is_looked_up_only_after_its_token_expired() -> None:
    clock = _Clock(100.0)
    lookups: list[str] = []
    alive = {"h": True}

    async def lookup(handle: str) -> bool:
        lookups.append(handle)
        return alive[handle]

    watch = SessionWatch(
        LiveSession(user_id=uuid4(), handle="h", access_expires_at=200.0),
        lookup=lookup,
        clock=clock,
    )
    assert await watch.still_valid() and lookups == []
    clock.now = 201.0
    assert await watch.still_valid() and lookups == ["h"]
    # Alive: not asked again until the recheck interval has passed.
    assert await watch.still_valid() and lookups == ["h"]
    alive["h"] = False
    clock.now += SESSION_RECHECK_SECONDS
    assert not await watch.still_valid()


async def test_a_lookup_that_fails_keeps_the_socket() -> None:
    async def lookup(_handle: str) -> bool:
        raise GeneralError("the session core is away")

    watch = SessionWatch(
        LiveSession(user_id=uuid4(), handle="h", access_expires_at=0.0),
        lookup=lookup,
        clock=_Clock(10.0),
    )
    assert await watch.still_valid()
