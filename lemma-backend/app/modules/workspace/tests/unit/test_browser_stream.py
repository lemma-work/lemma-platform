from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.workspace.api.controllers import browser_stream_controller as c


class _FakeWebSocket:
    def __init__(self, *, cookies=None, headers=None, query=None):
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.query_params = query or {}
        self.closed_with: int | None = None

    async def close(self, code: int = 1000):
        self.closed_with = code


@pytest.mark.asyncio
async def test_the_user_id_is_a_uuid_not_the_string_the_session_returns() -> None:
    """Everything downstream keys a sandbox by this and eventually asks for
    `.hex`. A string got all the way to container naming and failed there, in a
    traceback that said nothing about sessions — and reached the viewer as "the
    connection dropped"."""
    identity = uuid4()

    async def fake_session(token, **kwargs):
        return SimpleNamespace(get_user_id=lambda: str(identity))

    resolved = await c.resolve_user_id(
        _FakeWebSocket(cookies={"sAccessToken": "t"}), read_session=fake_session
    )

    assert resolved == identity
    assert isinstance(resolved, UUID)


@pytest.mark.asyncio
async def test_a_session_subject_that_is_not_a_user_id_is_refused() -> None:
    """Guessing at it would key a sandbox to nothing."""

    async def fake_session(token, **kwargs):
        return SimpleNamespace(get_user_id=lambda: "not-a-uuid")

    assert (
        await c.resolve_user_id(
            _FakeWebSocket(cookies={"sAccessToken": "t"}), read_session=fake_session
        )
        is None
    )


@pytest.mark.asyncio
async def test_no_token_is_no_session() -> None:
    assert await c.resolve_user_id(_FakeWebSocket()) is None


@pytest.mark.asyncio
async def test_a_bearer_header_is_accepted() -> None:
    """The CLI and SDK authenticate this way rather than with a cookie."""
    identity = uuid4()
    seen: dict[str, str] = {}

    async def fake_session(token, **kwargs):
        seen["token"] = token
        return SimpleNamespace(get_user_id=lambda: str(identity))

    resolved = await c.resolve_user_id(
        _FakeWebSocket(headers={"authorization": "Bearer abc123"}),
        read_session=fake_session,
    )

    assert resolved == identity
    assert seen["token"] == "abc123"


@pytest.mark.asyncio
async def test_an_unauthenticated_socket_is_closed_before_it_is_accepted() -> None:
    """Refused before accepting, so nobody holds an open socket onto somebody
    else's browser while we work out who they are."""
    websocket = _FakeWebSocket()

    async def no_user(_websocket):
        return None

    await c.stream_browser(websocket, resolve=no_user)

    assert websocket.closed_with == 4401


def test_the_handshake_is_allowlisted_from_the_session_gate() -> None:
    """The global auth dependency cannot see an upgrade, so a route that
    authenticates its own handshake has to be let through."""
    from app.core.security import EXCLUDED_PATHS

    assert c.BROWSER_STREAM_WS_SUFFIX in EXCLUDED_PATHS
