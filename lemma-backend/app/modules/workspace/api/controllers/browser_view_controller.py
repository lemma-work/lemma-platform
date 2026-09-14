"""Watching, and driving, the browser inside your own sandbox.

Two routes: one that says what a pane should render without starting anything,
and one socket that carries frames out and input in.

The socket is served from the API's own origin rather than from a per-sandbox
hostname, which is what makes it work in the desktop app: a WKWebView holds the
session cookie for one host, and a view on a different `*.localhost` subdomain
gets no session at all. It also means the token can travel in the query string
the way every other browser WebSocket in this codebase does, because browsers
cannot set headers on a handshake.

Only the owner ever reaches it. A workspace sandbox is keyed by user id, so
there is no identifier in this request that could name somebody else's -- the
session decides whose browser this is.
"""

from __future__ import annotations

from uuid import UUID

import asyncio
import contextlib
import httpx
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, status
from pydantic import BaseModel
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)

from app.core.api.dependencies import CurrentUser
from app.core.request_context import create_inherited_task
from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.workspace.services.browser_relay_client import (
    BrowserRelayUnavailable,
)
from app.modules.workspace.services.browser_view_service import (
    MODE_CONTROL,
    MODE_VIEW,
    BrowserViewService,
)
from app.modules.workspace.services.ws_bridge import (
    bridge,
    connect_upstream,
    origin_is_allowed,
)
from sandbox_runtime.errors import SandboxCapabilityUnsupported

logger = get_logger(__name__)


def _ws_error() -> type[Exception]:
    """The socket library's failure type, named where it is caught.

    At module scope it would sit in the import graph of every process that
    registers these routes, for the sake of an `except` clause.
    """
    import websockets

    return websockets.exceptions.WebSocketException


def _ws_closed() -> type[Exception]:
    """Likewise, for the ordinary close."""
    import websockets

    return websockets.exceptions.ConnectionClosed


router = APIRouter(prefix="/workspace/browser", tags=["Workspace Apps"])

#: Path the security layer allowlists for this socket, kept beside the route so
#: the two cannot drift.
BROWSER_VIEW_WS_PATH = "/workspace/browser/view"

#: Why a socket closed, in numbers a client can branch on. The 44xx range is
#: private to applications; each of these maps to a sentence in the pane, and
#: the reason they are distinct is that the remedies differ -- signing in again,
#: waking a computer, and replacing an image are not the same instruction.
CLOSE_UNAUTHENTICATED = 4401
CLOSE_ORIGIN_REFUSED = 4403
CLOSE_NO_BROWSER = 4409
CLOSE_UNSUPPORTED = 4422
CLOSE_RELAY_ABSENT = 4426


class BrowserStatusResponse(BaseModel):
    """What the pane can say without waking anything.

    `asleep` the computer is paused or was never started; `stopped` it is up but
    the browser is not (the resting state after two idle minutes); `running` a
    browser is there now; `unavailable` the relay did not answer, which on an
    older image is permanent until it is replaced; `unsupported` this fabric
    cannot reach a port at all.
    """

    state: str
    detail: str | None = None


def allowed_origins() -> tuple[str, ...]:
    """Where a browser may legitimately open this socket from.

    A dependency rather than a module function so a test can supply its own,
    which is injection rather than reaching into the module under test and
    replacing part of it.
    """
    candidates = (
        settings.frontend_url,
        settings.api_url,
        getattr(settings, "auth_frontend_url", None),
    )
    return tuple(str(c) for c in candidates if c)


def _engine_error() -> type[Exception]:
    """See `browser_view_service._engine_error` -- same reason, same cost."""
    from app.modules.workspace.providers.docker_engine import DockerEngineError

    return DockerEngineError


def browser_view_service() -> BrowserViewService:
    """The service this controller drives. Injected for the same reason."""
    return BrowserViewService()


@router.get(
    "/status",
    response_model=BrowserStatusResponse,
    operation_id="workspace.browser.status",
    summary="Whether the workspace browser can be watched",
)
async def browser_status(
    user: CurrentUser,
    service: Annotated[BrowserViewService, Depends(browser_view_service)],
) -> BrowserStatusResponse:
    try:
        found = await service.status(user.id)
    finally:
        await service.close()
    return BrowserStatusResponse(
        state=found.get("state", "unavailable"), detail=found.get("detail")
    )


async def _resolve_user_id(websocket: WebSocket):
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


#: How often to say the sandbox is still wanted. Comfortably inside the
#: shortest idle window anyone runs, and cheap: one row update.
_KEEP_AWAKE_SECONDS = 60.0


async def _keep_awake(service: BrowserViewService, user_id: UUID) -> None:
    """Tell the idle sweep this person is still here, until the socket closes."""
    while True:
        await asyncio.sleep(_KEEP_AWAKE_SECONDS)
        await service.keep_awake(user_id)


def _session_for(conversation: str | None, origin: str | None) -> str | None:
    """Which session this viewer is joining.

    `None` means "the relay decides", which it does from the origin -- the
    sign-in case. Raises `ValueError` for a conversation id that is not one,
    rather than falling back to somebody else's browser.
    """
    from app.modules.workspace.domain.browser_context import agent_session

    if conversation:
        return agent_session(UUID(conversation))
    del origin  # the relay names the login session from it
    return None


async def _refuse(websocket: WebSocket, code: int) -> None:
    """Close with a code the person's browser will actually receive.

    A close sent *before* `accept()` is not a close. ASGI turns it into a
    rejected handshake -- uvicorn answers HTTP 403 -- and a rejected handshake
    reaches page script as `code: 1006`, the anonymous "abnormal closure" a
    browser reports when it never had a connection at all. The close code is
    part of the WebSocket close *frame*, and there is no frame until the socket
    has been accepted.

    So every one of the five codes below arrived at the pane as the same 1006,
    and two things followed from it. The person was told "The connection
    dropped. Reconnecting." no matter what had really happened -- including
    "the browser is not running", which is the ordinary resting state of an
    idle workspace and not a failure at all. And the view retried, for ever,
    because 1006 is the code it is right to retry: the client has explicit
    logic to stop on a refusal that will never become an acceptance, and that
    logic could never fire.

    Accepting a socket in order to close it is backwards, and the reason it is
    correct anyway is that nothing is sent between the two. A caller refused
    here gets an open event, a close frame carrying the reason, and no bytes.
    """
    # Suppressed rather than checked: the client may have gone between the
    # handshake and here, and a refusal that fails to be delivered must not
    # become a traceback in its own right.
    with contextlib.suppress(RuntimeError):
        await websocket.accept()
    with contextlib.suppress(RuntimeError):
        await websocket.close(code=code)


@router.websocket("/view")
async def browser_view(
    websocket: WebSocket,
    service: Annotated[BrowserViewService, Depends(browser_view_service)],
    origins: Annotated[tuple[str, ...], Depends(allowed_origins)],
    mode: str = Query(default=MODE_VIEW),
    origin: str | None = Query(default=None),
    conversation: str | None = Query(default=None),
) -> None:
    """One person, watching or driving their own browser.

    Every refusal goes through `_refuse`, which accepts the socket before
    closing it. That is the opposite of what it should be, and is the only way
    a browser is ever told which refusal happened -- see `_refuse`.

    A view joins a session, it never names a new one. `conversation` joins the
    agent's, which is per conversation so two of them do not share cookies;
    `origin` alone means a sign-in, which lives in a session named for the site.
    Neither is trusted as a session name -- both are turned into one here, and
    the relay's reply says which was actually used.
    """
    if not origin_is_allowed(websocket.headers.get("origin"), allowed=origins):
        # Browsers do not apply same-origin to WebSockets but do send cookies,
        # so without this any page could open this socket as the signed-in
        # person and both watch their screen and type into it.
        logger.warning("workspace.browser_view.origin_refused.denied")
        await _refuse(websocket, CLOSE_ORIGIN_REFUSED)
        return

    try:
        user_id = await _resolve_user_id(websocket)
    except Exception:
        # Broad because the session library raises several unrelated types for
        # the same fact -- expired, malformed, revoked -- and the answer to all
        # of them is the same close code. Logged with the traceback so a
        # genuine failure in that library is not read as somebody's token
        # having expired.
        logger.warning(
            "workspace.browser_view.session_unreadable.degraded", exc_info=True
        )
        await _refuse(websocket, CLOSE_UNAUTHENTICATED)
        return

    if mode not in (MODE_VIEW, MODE_CONTROL):
        await _refuse(websocket, CLOSE_ORIGIN_REFUSED)
        return

    try:
        session = _session_for(conversation, origin)
    except ValueError:
        logger.warning("workspace.browser_view.unreadable_conversation.denied")
        await _refuse(websocket, CLOSE_ORIGIN_REFUSED)
        return

    try:
        upstream_url, headers = await service.open_session(
            UUID(user_id), mode=mode, origin=origin, session=session
        )
    except SandboxCapabilityUnsupported:
        logger.warning("workspace.browser_view.unsupported.denied")
        await _refuse(websocket, CLOSE_UNSUPPORTED)
        await service.close()
        return
    except BrowserRelayUnavailable:
        logger.warning("workspace.browser_view.browser_start_failed.degraded")
        await _refuse(websocket, CLOSE_NO_BROWSER)
        await service.close()
        return
    except (OSError, httpx.HTTPError, _engine_error()) as exc:
        # An image built before the relay existed, or a sandbox that went away
        # between resolving it and reaching it. Named rather than broad: the
        # remedy is "restart this computer", and anything else reaching here is
        # a bug that should surface as one.
        logger.warning(
            "workspace.browser_view.relay_absent.degraded",
            error_type=type(exc).__name__,
        )
        await _refuse(websocket, CLOSE_RELAY_ABSENT)
        await service.close()
        return

    await websocket.accept()
    # Held awake for as long as somebody is looking. The idle sweep measures
    # from the last time a caller asked for the sandbox, and watching is not a
    # tool call -- so a person reading a page, or working through a sign-in,
    # counted as idle and had their computer stopped underneath them. Releasing
    # runs quiesce, which deletes the browser profile, so what a slow sign-in
    # lost was the sign-in.
    awake = create_inherited_task(
        _keep_awake(service, UUID(user_id)), name="workspace.browser_view.keep_awake"
    )
    try:
        async with await connect_upstream(upstream_url, headers=headers) as upstream:
            await bridge(websocket, upstream, name="workspace.browser_view")
    except (OSError, _ws_error()) as exc:
        # The sandbox side dropped. Not a bug on this side, and the person is
        # told the connection dropped rather than that something failed.
        logger.warning(
            "workspace.browser_view.upstream.degraded", error_type=type(exc).__name__
        )
        del exc
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except RuntimeError:
            # Already closed by the disconnect that brought us here.
            pass
    finally:
        awake.cancel()
        # Awaited, not merely cancelled: a cancelled task is not finished until
        # it has been collected, and leaving it uncollected is how a task
        # outlives the request that started it.
        with contextlib.suppress(Exception):
            await awake
        await service.close()


__all__ = ["BROWSER_VIEW_WS_PATH", "router"]
