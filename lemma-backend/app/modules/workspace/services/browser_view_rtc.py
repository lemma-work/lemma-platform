"""One person's WebRTC view, from the API's side: credentials in, a filter between.

The browser view socket with `transport=rtc` carries no picture. It carries
the setup of a WebRTC connection between the person's browser and the
streamer in their sandbox, through the relay. This module is the API's part
of that, and it does three things nobody else can:

- **Signs the ticket.** The streamer admits a viewer only with a ticket signed
  by the machine's key, naming `view` or `control`. The mode is decided here,
  from what the person may do on this machine, and the browser never sees a
  ticket -- asking to drive is a `take` message, which this turns into a fresh
  control ticket only if the person may.
- **Mints TURN credentials** for both ends: one set for the streamer, sent in
  its `hello`, and one for the browser, sent in `config`. Both are relay-only.
- **Filters the browser's messages** to the five it may send. Anything else is
  dropped, and enough of it ends the session: this socket is a door into the
  sandbox's loopback, and a door's vocabulary is what keeps it one.

What comes back from the streamer is passed to the browser as it is: it is
our own process, and its replies are what the browser's connection needs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import secrets
from typing import Literal

from fastapi import WebSocket

from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services.browser_stream import Mode, mint_ticket
from app.modules.workspace.services.browser_stream_turn import (
    IceServer,
    TurnUnavailable,
    turn_provider,
)
from app.modules.workspace.services.ws_bridge import connect_upstream

logger = get_logger(__name__)

#: Close code: WebRTC is not available here -- no streamer in the image, no
#: TURN configured, or no credentials to be had. The pane switches to VNC.
CLOSE_USE_VNC = 4427

#: Same bound as the relay's (`browser_relay/rtc.py`): an SDP for one video
#: track and two data channels is a few kilobytes.
MAX_SIGNAL_BYTES = 64 * 1024

#: Dropped messages after which the browser is not treated as a browser.
_MAX_DROPPED = 20

ICE_POLICY: Literal["relay"] = "relay"


@dataclass(frozen=True)
class RtcViewer:
    """Who is connecting, to which machine, and what they may do there."""

    #: The viewer's name: the person and their pane, `<user>:<pane>`.
    sub: str
    provider_id: str
    mode: Mode
    may_control: bool


class RtcUnavailable(RuntimeError):
    """WebRTC cannot be set up for this view; the pane should use VNC."""


async def ice_for(viewer: RtcViewer) -> tuple[list[IceServer], list[IceServer]]:
    """TURN credentials for the machine's end and the browser's, minted together.

    Different usernames for the two ends, so a relay's logs can tell a
    machine's allocation from a viewer's. The rest of each username is random
    per session: the relay is a third party, and its logs have no use for which
    person or sandbox was on the other end.
    """
    provider = turn_provider()
    if provider is None:
        raise RtcUnavailable("no TURN relay is configured")
    ttl = workspace_settings.turn_credential_ttl_seconds
    try:
        machine, browser = await asyncio.gather(
            provider.ice_servers(
                label=f"machine-{secrets.token_hex(8)}", ttl_seconds=ttl
            ),
            provider.ice_servers(
                label=f"viewer-{secrets.token_hex(8)}", ttl_seconds=ttl
            ),
        )
    except TurnUnavailable as exc:
        raise RtcUnavailable(str(exc)) from exc
    if not machine or not browser:
        raise RtcUnavailable("the TURN relay gave no usable servers")
    return machine, browser


def from_browser(raw: str, viewer: RtcViewer) -> str | None:
    """What a browser message becomes on its way to the streamer, if anything.

    `None` drops it. The streamer's own vocabulary is wider -- `hello` and
    `control` carry tickets -- and those two are exactly what a browser must
    never be able to send, so they are not on this list.
    """
    if len(raw) > MAX_SIGNAL_BYTES:
        return None
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    kind = message.get("t")
    if kind == "offer" and isinstance(message.get("sdp"), str):
        return json.dumps({"t": "offer", "sdp": message["sdp"]})
    if kind == "candidate":
        return _candidate(message)
    if kind == "take":
        if not viewer.may_control:
            return None
        ticket = mint_ticket(
            provider_id=viewer.provider_id, sub=viewer.sub, mode="control"
        )
        return json.dumps({"t": "control", "ticket": ticket})
    if kind in ("release", "bye"):
        return json.dumps({"t": kind})
    return None


def _candidate(message: dict[str, object]) -> str | None:
    """An ICE candidate, with exactly the fields one has and nothing else."""
    candidate = message.get("candidate")
    mid = message.get("sdpMid")
    index = message.get("sdpMLineIndex")
    if candidate is not None and not isinstance(candidate, str):
        return None
    if mid is not None and not isinstance(mid, str):
        return None
    if index is not None and (not isinstance(index, int) or isinstance(index, bool)):
        return None
    return json.dumps(
        {
            "t": "candidate",
            "candidate": candidate,
            "sdpMid": mid,
            "sdpMLineIndex": index,
        }
    )


def hello_message(viewer: RtcViewer, machine_ice: list[IceServer]) -> str:
    """The streamer's first message: this viewer's ticket and its TURN servers."""
    return json.dumps(
        {
            "t": "hello",
            "ticket": mint_ticket(
                provider_id=viewer.provider_id, sub=viewer.sub, mode=viewer.mode
            ),
            "ice_servers": machine_ice,
            "policy": ICE_POLICY,
        }
    )


def config_message(viewer: RtcViewer, browser_ice: list[IceServer]) -> str:
    """The browser's first message: its TURN servers and what it may do.

    No ticket, ever: see the module docstring.
    """
    return json.dumps(
        {
            "t": "config",
            "ice_servers": browser_ice,
            "policy": ICE_POLICY,
            "mode": viewer.mode,
            "may_control": viewer.may_control,
        }
    )


async def serve(
    websocket: WebSocket,
    *,
    upstream_url: str,
    headers: dict[str, str],
    viewer: RtcViewer,
    machine_ice: list[IceServer],
    browser_ice: list[IceServer],
) -> int | None:
    """Carry one WebRTC view's signalling until either end stops.

    Returns the relay's close code when it refused with one the pane reads
    (44xx), so the caller can pass it on; `None` otherwise.
    """
    async with await connect_upstream(upstream_url, headers=headers) as upstream:
        await upstream.send(hello_message(viewer, machine_ice))
        await websocket.send_text(config_message(viewer, browser_ice))
        await _pump(websocket, upstream, viewer)
        code = getattr(upstream, "close_code", None)
        return code if code and 4400 <= code <= 4499 else None


async def _pump(websocket: WebSocket, upstream, viewer: RtcViewer) -> None:
    dropped = 0

    async def to_streamer() -> None:
        nonlocal dropped
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            forwarded = from_browser(text, viewer) if text is not None else None
            if forwarded is None:
                dropped += 1
                if dropped >= _MAX_DROPPED:
                    logger.warning(
                        "workspace.browser_view.rtc_signalling_refused.denied",
                        dropped=dropped,
                    )
                    return
                continue
            await upstream.send(forwarded)

    async def to_browser() -> None:
        async for frame in upstream:
            if isinstance(frame, str) and len(frame) <= MAX_SIGNAL_BYTES:
                await websocket.send_text(frame)

    tasks = [
        create_inherited_task(to_streamer(), name="workspace.browser_view.rtc.in"),
        create_inherited_task(to_browser(), name="workspace.browser_view.rtc.out"),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "CLOSE_USE_VNC",
    "ICE_POLICY",
    "config_message",
    "hello_message",
    "RtcUnavailable",
    "RtcViewer",
    "from_browser",
    "ice_for",
    "serve",
]
