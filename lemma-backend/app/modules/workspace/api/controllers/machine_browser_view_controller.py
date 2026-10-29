"""Watching, and driving, the browser on a machine.

`/machines/{ref}/browser`: what a pane should render without starting
anything, a few small controls, and one socket that carries frames out and
input in.

The socket is served from the API's own origin rather than from a per-sandbox
hostname, which is what makes it work in the desktop app: a WKWebView holds the
session cookie for one host, and a view on a different `*.localhost` subdomain
gets no session at all. It also means the token can travel in the query string
the way every other browser WebSocket in this codebase does, because browsers
cannot set headers on a handshake.

Who reaches it is the machine resolver's to decide, like every other machine
route: the ref names the machine and the session says who is asking. Driving
needs use of the machine; watching needs to see it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uuid import UUID

import asyncio
import contextlib
import httpx
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, WebSocket, status
from pydantic import BaseModel, Field
from app.modules.workspace.api.controllers.machine_socket_auth import (
    allowed_origin_pattern,
    allowed_origins,
    ViewMachineResolver,
    view_machine_resolver,
)
from app.modules.workspace.api.controllers.machine_dependencies import (
    SeeMachine,
    UseMachine,
    acting_user,
)
from app.modules.workspace.api.controllers.machines_controller import machines_enabled
from app.modules.workspace.domain.machine_access import MachineAccess
from app.core.app_pin import app_host_slug
from app.core.request_context import create_inherited_task

from app.core.log.log import get_logger
from app.modules.workspace.services.browser_relay_client import (
    BrowserRelayNotServed,
    BrowserRelayUnavailable,
)
from app.modules.workspace.api.controllers.browser_view_session import (
    VIEWER_PATTERN,
    UserIdResolver,
    session_user_id,
    user_id_resolver,
    viewer_name,
)
from app.modules.workspace.providers.base import ProviderGone
from app.modules.workspace.api.controllers.browser_view_hangup import (
    collect as _collect,
    hang_up as _hang_up,
    refuse as _refuse,
)
from app.modules.workspace.api.controllers.browser_view_watchers import (
    watch_begun,
    watch_ended,
)
from app.modules.workspace.services.browser_view_service import (
    MODE_CONTROL,
    MODE_VIEW,
    BrowserViewService,
)
from app.modules.workspace.services.ws_bridge import (
    origin_is_allowed,
    origin_refusal_hint,
)
from sandbox_runtime.errors import SandboxCapabilityUnsupported, SandboxError
from sandbox_runtime.errors import SandboxUnavailable

if TYPE_CHECKING:
    from app.modules.workspace.api.controllers.browser_view_stream import (
        PreparedRtc,
    )
    from app.modules.workspace.services.browser_view_service import ViewUpstream

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


router = APIRouter(
    prefix="/machines/{ref}/browser",
    tags=["Machine Browser"],
    dependencies=[Depends(machines_enabled)],
)

#: Why a socket closed, in numbers a client can branch on. The 44xx range is
#: private to applications; each of these maps to a sentence in the pane, and
#: the reason they are distinct is that the remedies differ -- signing in again,
#: waking a computer, and replacing an image are not the same instruction.
CLOSE_UNAUTHENTICATED = 4401
CLOSE_ORIGIN_REFUSED = 4403
CLOSE_NO_BROWSER = 4409
CLOSE_UNSUPPORTED = 4422
CLOSE_RELAY_ABSENT = 4426
#: The computer is still starting or briefly unreachable. Worth retrying.
CLOSE_SANDBOX_UNAVAILABLE = 4503


class BrowserStatusResponse(BaseModel):
    """What the pane can say without waking anything.

    `asleep` the computer is paused or was never started; `stopped` it is up but
    the browser is not (the resting state after five idle minutes); `running` a
    browser is there now; `unavailable` the relay did not answer, which on an
    older image is permanent until it is replaced; `unsupported` this fabric
    cannot reach a port at all.
    """

    state: str
    detail: str | None = None
    #: The rest is present once a browser runs; see `browser_stream_status`.
    transport: str | None = None
    density: int | None = None
    size: str | None = None
    presets: dict[str, str] | None = None
    lease: str | None = None
    watching: list[dict[str, str]] = Field(default_factory=list)
    agent_driving: bool = False


def _engine_error() -> type[Exception]:
    """See `browser_view_service._engine_error` -- same reason, same cost."""
    from app.modules.workspace.providers.docker_engine import DockerEngineError

    return DockerEngineError


def browser_view_service() -> BrowserViewService:
    """The service this controller drives. Injected for the same reason."""
    return BrowserViewService()


@router.get(
    "",
    response_model=BrowserStatusResponse,
    operation_id="machine.browser.status",
    summary="Whether a machine's browser can be watched",
)
async def browser_status(
    request: Request,
    resolved: SeeMachine,
    service: Annotated[BrowserViewService, Depends(browser_view_service)],
) -> BrowserStatusResponse:
    try:
        found = await service.status(
            resolved.machine.id, viewer=acting_user(request, resolved)
        )
    finally:
        await service.close()
    return BrowserStatusResponse.model_validate({"state": "unavailable", **found})


class CurrentPageUrlResponse(BaseModel):
    url: str | None = None


@router.get(
    "/current-page-url",
    response_model=CurrentPageUrlResponse,
    operation_id="machine.browser.current_page_url",
    summary="What page a sign-in's browser is actually showing",
)
async def current_page_url(
    resolved: UseMachine,
    service: Annotated[BrowserViewService, Depends(browser_view_service)],
    origin: str = Query(),
) -> CurrentPageUrlResponse:
    """Polled by the sign-in page while its VNC pane is open.

    VNC is pixels, not events -- it carries no navigation signal the way the
    JSON stream this replaced did with its `url` message on every
    navigation. This is what the anti-phishing host display on
    `sign-in-to-site/[conversationId]/[toolCallId]/page.tsx` reads instead,
    so a person mid-SSO-redirect still sees which site they are actually on.
    """
    try:
        url = await service.current_page_url(resolved.machine.id, origin=origin)
    finally:
        await service.close()
    return CurrentPageUrlResponse(url=url)


class DisplaySizeRequest(BaseModel):
    """The size the pane wants its picture to be, in CSS pixels.

    Bounded here because these numbers come from a browser window and decide
    how much memory a framebuffer takes. The sandbox clamps again against the
    framebuffer it actually allocated, which is the limit that cannot be
    argued with.
    """

    width: int = Field(ge=320, le=4096)
    height: int = Field(ge=240, le=4096)
    #: The pane asking, as named on its view socket (the resize lease).
    viewer: str | None = Field(default=None, pattern=VIEWER_PATTERN)


class DisplaySizeResponse(BaseModel):
    #: What the display ended up as. Not always what was asked for, and the
    #: pane is told so rather than left to assume.
    size: str | None = None
    #: Why it did not change, in a sentence for the pane: somebody else
    #: holds the size, the screen is being recorded.
    reason: str | None = None


@router.post(
    ":display",
    response_model=DisplaySizeResponse,
    operation_id="machine.browser.display",
    summary="Set the size of a machine's browser display",
)
async def resize_display(
    http: Request,
    resolved: UseMachine,
    service: Annotated[BrowserViewService, Depends(browser_view_service)],
    request: DisplaySizeRequest,
) -> DisplaySizeResponse:
    """Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.
    """
    # Every branch below logs. Answering the viewer with "no size" is right --
    # they keep a working picture either way -- but answering *silently* meant
    # a display that never resized looked exactly like one that had nothing to
    # resize, and the pane letterboxed a 1920x1200 screen for days with no
    # trace anywhere of why. A degraded path still has to say it degraded.
    try:
        size = await service.resize_display(
            resolved.machine.id,
            width=request.width,
            height=request.height,
            viewer=viewer_name(acting_user(http, resolved), request.viewer),
        )
    except SandboxCapabilityUnsupported:
        logger.warning(
            "workspace.browser_view.resize_unsupported.degraded",
            width=request.width,
            height=request.height,
        )
        return DisplaySizeResponse()
    except BrowserRelayUnavailable as exc:
        logger.warning(
            "workspace.browser_view.resize_no_relay.degraded",
            width=request.width,
            height=request.height,
            error_type=type(exc).__name__,
        )
        return DisplaySizeResponse(reason=str(exc) or None)
    except (OSError, httpx.HTTPError, _engine_error()) as exc:
        logger.warning(
            "workspace.browser_view.resize_failed.degraded",
            error_type=type(exc).__name__,
        )
        return DisplaySizeResponse()
    finally:
        await service.close()
    if not size:
        # The relay answered and still changed nothing, which is its own
        # outcome and not the same as any failure above.
        logger.warning(
            "workspace.browser_view.resize_had_no_effect.degraded",
            width=request.width,
            height=request.height,
        )
    return DisplaySizeResponse(size=size or None)


#: How often to say the sandbox is still wanted. Comfortably inside the
#: shortest idle window anyone runs, and cheap: one row update.
_KEEP_AWAKE_SECONDS = 60.0


async def _keep_awake(service: BrowserViewService, user_id: UUID) -> None:
    """Tell the idle sweep this person is still here, until the socket closes."""
    while True:
        await asyncio.sleep(_KEEP_AWAKE_SECONDS)
        await service.keep_awake(user_id)


def _speaks(mode: str, transport: str) -> bool:
    """A mode and a transport this socket knows: the VNC picture, or
    WebRTC's signalling (`browser_view_stream`)."""
    return mode in (MODE_VIEW, MODE_CONTROL) and transport in ("vnc", "rtc")


async def _refused_origin(websocket: WebSocket, origins: tuple[str, ...]) -> bool:
    """Whether the handshake came from a page that may not open this socket;
    if so it has been refused."""
    sent = websocket.headers.get("origin")
    # Read here, not taken as a handler parameter: it is deployment config, and
    # a parameter reads to static analysis as something the caller supplied.
    origin_pattern = allowed_origin_pattern()
    if not origin_is_allowed(sent, allowed=origins, pattern=origin_pattern):
        # Browsers do not apply same-origin to WebSockets but do send cookies,
        # so without this any page could open this socket as the signed-in
        # person and both watch their screen and type into it.
        #
        # The hint is parsed and scrubbed, never the raw header -- but it is
        # *there*, which it was not. A refusal that logged nothing cost a
        # person their sign-in and left no way to tell why from the record.
        logger.warning(
            "workspace.browser_view.origin_refused.denied",
            origin_hint=origin_refusal_hint(sent, allowed=origins),
        )
        await _refuse(websocket, CLOSE_ORIGIN_REFUSED)
        return True
    if app_host_slug(websocket) is not None:
        # An app host is an allowed CORS origin, but this is the person's own
        # sandbox -- their saved sign-ins -- outside any pod, and an app's page
        # acts for its viewer only in its own pod (``core/app_pin``). Refused
        # without looking the app up: no app host has any business here.
        logger.warning("workspace.browser_view.app_origin_refused.denied")
        await _refuse(websocket, CLOSE_ORIGIN_REFUSED)
        return True
    return False


@router.websocket("/view")
async def browser_view(
    websocket: WebSocket,
    ref: str,
    service: Annotated[BrowserViewService, Depends(browser_view_service)],
    origins: Annotated[tuple[str, ...], Depends(allowed_origins)],
    resolve_user_id: Annotated[UserIdResolver, Depends(user_id_resolver)],
    find_machine: Annotated[ViewMachineResolver, Depends(view_machine_resolver)],
    mode: str = Query(default=MODE_VIEW),
    origin: str | None = Query(default=None),
    conversation: UUID | None = Query(default=None),
    transport: str = Query(
        default="vnc", description="`vnc` carries the picture; `rtc` WebRTC signalling."
    ),
    viewer: str | None = Query(default=None, pattern=VIEWER_PATTERN),
) -> None:
    """One person, watching or driving their own browser, over VNC.

    Every refusal goes through `_refuse`, which accepts the socket before
    closing it. That is the opposite of what it should be, and is the only way
    a browser is ever told which refusal happened -- see `_refuse`.
    Once the caller and the machine are known, the socket is accepted
    outright, before the slow part -- see the comment there.

    `origin`, when given, means a sign-in: it steers the browser to that site
    before attaching, in a session named for it. `conversation`, when given
    and `origin` is not, names the conversation whose own agent browser this
    watches or drives -- `run_browser_script` puts every agent browser
    command in its own session and profile, named for the conversation, so
    without this a plain watch/drive resolved to the *shared* default session
    instead and found nothing the agent had touched. Neither given shows
    whatever this person's shared sandbox already has open -- VNC is the
    whole shared display, not a session-scoped tab, so there is nothing else
    here to name.
    """
    if await _refused_origin(websocket, origins):
        return

    user_id = await session_user_id(
        websocket,
        resolve_user_id,
        socket="machine_browser_view",
    )
    if user_id is None:
        await _refuse(websocket, CLOSE_UNAUTHENTICATED)
        return

    if not _speaks(mode, transport):
        await _refuse(websocket, CLOSE_ORIGIN_REFUSED)
        return

    resolved = await find_machine(
        websocket,
        ref,
        UUID(user_id),
        need=MachineAccess.USE if mode == MODE_CONTROL else MachineAccess.STATUS,
        refuse=_refuse,
    )
    if resolved is None:
        return
    machine_id = resolved.machine.id

    # Accepted now, before anything slow, and that is for the *other* sockets
    # on this page. A browser lets one WebSocket per host be CONNECTING at a
    # time (RFC 6455 section 4.1), and everything below -- starting the
    # browser, WebRTC's setup -- can take a cold start's worth of seconds. Held
    # in the handshake that long, this socket queued the terminal's stream and
    # the pod's live socket behind it: a Terminal tab stuck on "Starting a
    # shell..." beside a screen saying "Connecting to the screen...". Every
    # refusal from here on goes through `_refuse`, which closes an accepted
    # socket with its code just as well, so nothing is lost by answering
    # early -- the pane still hears which refusal it was.
    await websocket.accept()

    try:
        target = await service.open_vnc_session(
            machine_id,
            mode=mode,
            origin=origin,
            conversation_id=conversation,
            viewer=viewer_name(UUID(user_id), viewer),
            transport=transport,
        )
    except SandboxCapabilityUnsupported:
        logger.warning("workspace.browser_view.unsupported.denied")
        await _refuse(websocket, CLOSE_UNSUPPORTED)
        await service.close()
        return
    except BrowserRelayNotServed as exc:
        # Before its parent below, because the pane branches on which of the
        # two it was. A relay that did not answer may answer on the next
        # attempt, so `CLOSE_NO_BROWSER` is retried; a port the fabric does not
        # publish will not open however many times anybody asks, so this is one
        # of the codes the pane stops on. Untyped, this arrived as a
        # `ProviderRejected` nothing caught, and an unhandled exception in a
        # socket handler reaches the pane as an ordinary drop -- which it
        # retried, for ever, against a port that was never going to open.
        logger.warning(
            "workspace.browser_view.relay_not_served.degraded", reason=str(exc)
        )
        await _refuse(websocket, CLOSE_RELAY_ABSENT)
        await service.close()
        return
    except BrowserRelayUnavailable as exc:
        # With the reason. It said only that starting failed, so a browser
        # stuck on "Connecting..." meant reproducing this code path by hand
        # inside a sandbox to find out why -- and the exception had the
        # sentence all along ("the browser relay answered 502"). The neighbour
        # below already carried its `error_type`; this one carried nothing.
        logger.warning(
            "workspace.browser_view.browser_start_failed.degraded",
            reason=str(exc),
        )
        await _refuse(websocket, CLOSE_NO_BROWSER)
        await service.close()
        return
    except (SandboxUnavailable, ProviderGone) as exc:
        # Still starting (an image downloading, a port not published yet), or
        # its container went away under us and the next attempt makes another:
        # the pane waits and retries instead of reporting "the connection
        # dropped". Uncaught, a gone container reached the pane as a bare 500.
        logger.warning(
            "workspace.browser_view.sandbox_unavailable.degraded", reason=str(exc)
        )
        await _refuse(websocket, CLOSE_SANDBOX_UNAVAILABLE)
        await service.close()
        return
    except SandboxError as exc:
        # Refused for good; retrying changes nothing.
        logger.warning(
            "workspace.browser_view.sandbox_refused.degraded",
            error_type=type(exc).__name__,
            reason=str(exc),
        )
        await _refuse(websocket, CLOSE_RELAY_ABSENT)
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

    # Not at module scope: WebRTC's setup -- TURN, tickets -- loads only for
    # the sockets that use this route at all.
    from app.modules.workspace.api.controllers.browser_view_stream import (
        setup_rtc,
    )

    refused, rtc = await setup_rtc(
        websocket,
        service,
        target,
        transport=transport,
        user_id=user_id,
        mode=mode,
        viewer=viewer,
        may_control=resolved.access >= MachineAccess.USE,
    )
    if refused:
        return

    await _watch(websocket, service, target, rtc, machine_id)


async def _watch(
    websocket: WebSocket,
    service: BrowserViewService,
    target: ViewUpstream,
    rtc: PreparedRtc | None,
    machine_id: UUID,
) -> None:
    """Carry the accepted socket, keeping the machine awake meanwhile."""
    # Held awake for as long as somebody is looking. The idle sweep measures
    # from the last time a caller asked for the sandbox, and watching is not a
    # tool call -- so a person reading a page, or working through a sign-in,
    # counted as idle and had their computer stopped underneath them. Releasing
    # runs quiesce, which deletes the browser profile, so what a slow sign-in
    # lost was the sign-in.
    watcher = machine_id
    watch_begun(watcher)
    awake = create_inherited_task(
        _keep_awake(service, watcher), name="workspace.browser_view.keep_awake"
    )
    try:
        from app.modules.workspace.api.controllers.browser_view_stream import carry

        code = await carry(websocket, target, rtc)
        # The relay refuses with these same 44xx codes, and they mean the same
        # things on both sides of the sandbox wall -- so pass the reason on
        # rather than replacing it with 1011. Told 1011, the pane says "the
        # connection dropped" and reconnects for ever; told 4409 it says the
        # browser is not running, which is the truth and is not a failure.
        if code and 4400 <= code <= 4499:
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=code)
            return
    except (OSError, _ws_error()) as exc:
        # The sandbox side dropped. Not a bug on this side, and the person is
        # told the connection dropped rather than that something failed.
        logger.warning(
            "workspace.browser_view.upstream.degraded", error_type=type(exc).__name__
        )
        del exc
        await _hang_up(websocket, status.WS_1011_INTERNAL_ERROR, doing="failing")
    finally:
        await _collect(awake)
        watch_ended(watcher)
        await service.close()


__all__ = ["router"]
