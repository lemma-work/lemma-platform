"""Whose session a browser-view handshake carries.

Its own module so the controller stays about the socket: this is the whole of
reading an identity off a WebSocket handshake, and it is the one place where a
wrong answer is an unauthenticated socket that got accepted.

`user_id_resolver` is a factory rather than a dependency on the answer itself,
because the order matters. The origin check runs first and must refuse without
ever reading a session, and a dependency producing the user id would run before
the handler body. Handing the handler the *resolver* keeps that order and still
lets a caller supply one from outside -- which is the difference between
injecting a collaborator and reaching into the controller to replace a function
it calls, the second of which survives exactly the rename it should catch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import WebSocket
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)

UserIdResolver = Callable[[WebSocket], Awaitable[str]]


async def resolve_user_id(websocket: WebSocket) -> str:
    """Whose session this handshake carries.

    Same order as the datastore changes socket: bearer for the CLI and SDK, the
    cookie for a browser on our own origin, then an `access_token` query
    parameter for a browser that cannot attach the cookie. The query parameter
    is not a weakening -- it is the only way a browser can authenticate a
    WebSocket at all, since the API forbids setting headers on a handshake.
    """
    token: str | None = None
    authorization = websocket.headers.get("authorization") or ""
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() == "bearer" and raw.strip():
        token = raw.strip()
    if token is None:
        token = (
            websocket.cookies.get("sAccessToken")
            or websocket.cookies.get("st-access-token")
            or websocket.query_params.get("access_token")
        )
    if not token:
        raise PermissionError("the browser view needs a session")
    session = await get_session_without_request_response(
        token, anti_csrf_check=False, session_required=True
    )
    if session is None:
        # `session_required=True` is documented to raise rather than return
        # None, but the signature says otherwise and this is the one place a
        # wrong answer would be an unauthenticated socket that got accepted.
        raise PermissionError("the session could not be read")
    return session.get_user_id()


def user_id_resolver() -> UserIdResolver:
    """The resolver the socket route uses, as something that can be supplied."""
    return resolve_user_id


__all__ = ["UserIdResolver", "resolve_user_id", "user_id_resolver"]
