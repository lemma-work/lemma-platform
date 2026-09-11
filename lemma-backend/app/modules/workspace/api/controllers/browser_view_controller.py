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

from fastapi import APIRouter, Query, WebSocket, status
from pydantic import BaseModel
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)

from app.core.api.dependencies import CurrentUser
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


@router.get(
    "/status",
    response_model=BrowserStatusResponse,
    operation_id="workspace.browser.status",
    summary="Whether the workspace browser can be watched",
)
async def browser_status(user: CurrentUser) -> BrowserStatusResponse:
    service = BrowserViewService()
    try:
        found = await service.status(user.id)
    finally:
        await service.close()
    return BrowserStatusResponse(state=found["state"], detail=found.get("detail"))


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
    return session.get_user_id()


def _allowed_origins() -> tuple[str, ...]:
    """Where a browser may legitimately open this socket from."""
    candidates = (
        settings.frontend_url,
        settings.api_url,
        getattr(settings, "auth_frontend_url", None),
    )
    return tuple(str(c) for c in candidates if c)


@router.websocket("/view")
async def browser_view(
    websocket: WebSocket,
    mode: str = Query(default=MODE_VIEW),
    origin: str | None = Query(default=None),
) -> None:
    """One person, watching or driving their own browser.

    Refused before `accept()` wherever possible. A socket that is accepted and
    then closed looks to a browser like a connection that dropped, so the person
    is told the wrong thing about why.
    """
    if not origin_is_allowed(
        websocket.headers.get("origin"), allowed=_allowed_origins()
    ):
        # Browsers do not apply same-origin to WebSockets but do send cookies,
        # so without this any page could open this socket as the signed-in
        # person and both watch their screen and type into it.
        logger.warning("workspace.browser_view.origin_refused.denied")
        await websocket.close(code=CLOSE_ORIGIN_REFUSED)
        return

    try:
        user_id = await _resolve_user_id(websocket)
    except Exception:
        await websocket.close(code=CLOSE_UNAUTHENTICATED)
        return

    if mode not in (MODE_VIEW, MODE_CONTROL):
        await websocket.close(code=CLOSE_ORIGIN_REFUSED)
        return

    from uuid import UUID

    service = BrowserViewService()
    try:
        upstream_url, headers = await service.open_session(
            UUID(user_id), mode=mode, origin=origin
        )
    except SandboxCapabilityUnsupported:
        logger.warning("workspace.browser_view.unsupported.denied")
        await websocket.close(code=CLOSE_UNSUPPORTED)
        await service.close()
        return
    except BrowserRelayUnavailable:
        logger.warning("workspace.browser_view.browser_start_failed.degraded")
        await websocket.close(code=CLOSE_NO_BROWSER)
        await service.close()
        return
    except Exception:
        logger.warning("workspace.browser_view.relay_absent.degraded", exc_info=True)
        await websocket.close(code=CLOSE_RELAY_ABSENT)
        await service.close()
        return

    await websocket.accept()
    try:
        async with await connect_upstream(upstream_url, headers=headers) as upstream:
            await bridge(websocket, upstream, name="workspace.browser_view")
    except Exception:
        logger.warning("workspace.browser_view.upstream.degraded", exc_info=True)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except RuntimeError:
            # Already closed by the disconnect that brought us here.
            pass
    finally:
        await service.close()


__all__ = ["BROWSER_VIEW_WS_PATH", "router"]
