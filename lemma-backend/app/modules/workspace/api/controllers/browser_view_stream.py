"""The browser view socket's WebRTC branch, and the names viewers go by.

Its own module so the socket controller stays one route long. Setting up WebRTC
has a part that can refuse -- TURN credentials, which a deployment may not
have -- and a part that carries the signalling; the controller calls the first
before it starts carrying, so a refusal arrives as a close code the pane reads
rather than as a socket that opened onto nothing. The socket is already
accepted by then (the controller says why), and `refuse` closes an accepted
socket with its code just the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import WebSocket

from app.core.log.log import get_logger

from app.modules.workspace.services.browser_stream_turn import IceServer
from app.modules.workspace.services.browser_view_rtc import (
    CLOSE_USE_VNC,
    RtcUnavailable,
    RtcViewer,
    ice_for,
    serve,
)
from app.modules.workspace.api.controllers.browser_view_hangup import refuse
from app.modules.workspace.api.controllers.browser_view_session import viewer_name
from app.modules.workspace.services.browser_view_service import (
    MODE_CONTROL,
    BrowserViewService,
    ViewUpstream,
)
from app.modules.workspace.services.ws_bridge import bridge, connect_upstream

logger = get_logger(__name__)


@dataclass(frozen=True)
class PreparedRtc:
    target: ViewUpstream
    viewer: RtcViewer
    machine_ice: list[IceServer]
    browser_ice: list[IceServer]


async def prepare_rtc(
    target: ViewUpstream,
    *,
    user_id: UUID,
    mode: str,
    viewer: str | None,
    may_control: bool,
) -> PreparedRtc | None:
    """Everything a WebRTC view needs before its signalling is carried.

    `None`, logged, when there is no relay to be had: not a fault, and the
    caller turns it into `CLOSE_USE_VNC`, on which the pane switches to VNC.

    `may_control` is the viewer's own access to the machine, decided by the
    resolver; the streamer -- not the pane -- is what enforces it.
    """
    rtc_viewer = RtcViewer(
        sub=viewer_name(user_id, viewer) or str(user_id),
        provider_id=target.provider_id,
        mode=MODE_CONTROL if mode == MODE_CONTROL else "view",
        may_control=may_control,
    )
    try:
        machine_ice, browser_ice = await ice_for(rtc_viewer)
    except RtcUnavailable as exc:
        logger.warning(
            "workspace.browser_view.rtc_unavailable.degraded", reason=str(exc)
        )
        return None
    return PreparedRtc(target, rtc_viewer, machine_ice, browser_ice)


async def serve_rtc_view(websocket: WebSocket, prepared: PreparedRtc) -> int | None:
    """Carry the signalling; the relay's 44xx close code, if it gave one."""
    return await serve(
        websocket,
        upstream_url=prepared.target.url,
        headers=prepared.target.headers,
        viewer=prepared.viewer,
        machine_ice=prepared.machine_ice,
        browser_ice=prepared.browser_ice,
    )


async def setup_rtc(
    websocket: WebSocket,
    service: BrowserViewService,
    target: ViewUpstream,
    *,
    transport: str,
    user_id: str,
    mode: str,
    viewer: str | None,
    may_control: bool,
) -> tuple[bool, PreparedRtc | None]:
    """WebRTC's setup, before anything is carried: whether it was refused.

    A refusal is `CLOSE_USE_VNC`, not a fault: the pane switches to VNC on it.
    `may_control` is the viewer's own access to the machine -- a watcher of a
    pod machine is never handed a control ticket, whatever its pane asks.
    """
    if transport != "rtc":
        return False, None
    rtc = await prepare_rtc(
        target,
        user_id=UUID(user_id),
        mode=mode,
        viewer=viewer,
        may_control=may_control,
    )
    if rtc is None:
        await refuse(websocket, CLOSE_USE_VNC)
        await service.close()
        return True, None
    return False, rtc


async def carry(
    websocket: WebSocket, target: ViewUpstream, rtc: PreparedRtc | None
) -> int | None:
    """Carry the socket until either end stops; the relay's close code.

    VNC frames through the API, or WebRTC's signalling only -- the picture
    then goes over the peer connection and never through here.
    """
    if rtc is not None:
        return await serve_rtc_view(websocket, rtc)
    async with await connect_upstream(target.url, headers=target.headers) as upstream:
        await bridge(websocket, upstream, name="workspace.browser_view")
    return getattr(upstream, "close_code", None)


__all__ = [
    "CLOSE_USE_VNC",
    "PreparedRtc",
    "prepare_rtc",
    "carry",
    "serve_rtc_view",
    "setup_rtc",
]
