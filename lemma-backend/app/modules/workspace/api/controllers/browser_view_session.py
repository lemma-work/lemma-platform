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

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import WebSocket
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)

from app.core.authorization.delegation import DelegationClaims
from app.core.authorization.function_run import is_function_run_token
from app.core.log.log import get_logger

if TYPE_CHECKING:
    from app.core.security import PrincipalLookups

logger = get_logger(__name__)

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
    if is_function_run_token(token):
        # A person-less run reaches its pod's data, never a person's terminal
        # or browser.
        raise PermissionError("a function run may not open a machine socket")
    session = await get_session_without_request_response(
        token, anti_csrf_check=False, session_required=True
    )
    if session is None:
        # `session_required=True` is documented to raise rather than return
        # None, but the signature says otherwise and this is the one place a
        # wrong answer would be an unauthenticated socket that got accepted.
        raise PermissionError("the session could not be read")
    user_id = session.get_user_id()
    await remember_principal(websocket, user_id, session.get_access_token_payload())
    return user_id


@dataclass(frozen=True, slots=True)
class SocketPrincipal:
    """Who a socket's session acts as: the person, or a workload for them.

    Kept on ``websocket.state`` by :func:`resolve_user_id` because sockets are
    exempt from ``verify_auth``. A delegated token's claims went unread here
    once, so a schedule's agent token opened its person's ttys as the person.
    """

    user_id: UUID
    delegation_claims: DelegationClaims | None


async def remember_principal(
    websocket: WebSocket,
    user_id: str,
    payload: Mapping[str, object] | None,
    *,
    lookups: PrincipalLookups | None = None,
) -> None:
    """Resolve the token's delegation exactly as HTTP does, or refuse it."""
    from app.core.security import PrincipalRefused, resolve_delegated_principal

    parsed = UUID(user_id)
    try:
        claims = await resolve_delegated_principal(
            payload or {}, user_id=parsed, lookups=lookups
        )
    except PrincipalRefused as refused:
        raise PermissionError(refused.code) from refused
    websocket.state.socket_principal = SocketPrincipal(parsed, claims)


def socket_principal(websocket: WebSocket, user_id: UUID) -> SocketPrincipal | None:
    """The principal :func:`resolve_user_id` resolved for this socket, when it
    is the one ``user_id`` names; None otherwise, which callers refuse."""
    found = getattr(websocket.state, "socket_principal", None)
    if isinstance(found, SocketPrincipal) and found.user_id == user_id:
        return found
    return None


async def session_user_id(
    websocket: WebSocket, resolve: UserIdResolver, *, socket: str
) -> str | None:
    """Whose session this is, or None when it cannot be read.

    Broad because the session library raises several unrelated types for the
    same fact -- expired, malformed, revoked -- and every socket answers all
    of them with the same close code. Logged with the traceback, and the
    ``socket`` it was for, so a genuine failure in that library is not read
    as somebody's token having expired. One place, so each socket that needs
    a person does not grow its own copy of the catch.
    """
    try:
        return await resolve(websocket)
    except Exception:
        logger.warning(
            "workspace.socket.session_unreadable.degraded",
            socket=socket,
            exc_info=True,
        )
        return None


def user_id_resolver() -> UserIdResolver:
    """The resolver the socket route uses, as something that can be supplied."""
    return resolve_user_id


__all__ = [
    "SocketPrincipal",
    "UserIdResolver",
    "remember_principal",
    "resolve_user_id",
    "socket_principal",
    "session_user_id",
    "user_id_resolver",
]
