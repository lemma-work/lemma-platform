"""One person watching, and possibly driving, one page.

The wire protocol is deliberately not CDP. A viewer gets four message types and
can send four, and this process turns them into the handful of CDP calls they
correspond to. The alternative -- relaying CDP with an allowlist -- was tried,
and the problem with it is that the allowlist is the security boundary: every
method Chrome adds is admitted or refused by a list somebody has to maintain,
and getting it wrong once means handing a page `Runtime.evaluate`. Here a viewer
cannot express anything that is not on this page's screen or keyboard.

Backpressure is latest-wins. Chrome sends a frame and waits for
`Page.screencastFrameAck` before sending another, so an unacknowledged frame
stops the stream. Acknowledging immediately would let frames pile into a slow
viewer's socket; acknowledging only on the viewer's ack would stall the picture
behind one dropped message. So: hold at most one unacknowledged frame, and when
a newer one arrives, acknowledge the old one and drop it. A phone on a slow link
gets fewer frames rather than stale ones.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json

from sandbox_runtime.tasks import create_inherited_task

from .chrome import CdpConnection

#: What a viewer may ask for. `view` sends no input at all -- the relay refuses
#: it rather than trusting the client not to offer the affordance.
VIEW = "view"
CONTROL = "control"

#: JPEG rather than PNG: a screencast is a video, and the difference over a
#: mobile link is the difference between usable and not.
_FRAME_FORMAT = "jpeg"
_FRAME_QUALITY = 60
_MAX_DIMENSION = 1600

#: Every input method a viewer may cause, mapped to the CDP call it becomes.
#: A closed set, so "what can a viewer do" is answered by reading this rather
#: than by reasoning about what a domain prefix admits.
_INPUT_METHODS = {
    "mouse": "Input.dispatchMouseEvent",
    "key": "Input.dispatchKeyEvent",
    "text": "Input.insertText",
    "wheel": "Input.dispatchMouseEvent",
}


class ScreencastSession:
    """Pumps frames out and input in, for the life of one viewer's socket."""

    def __init__(self, cdp: CdpConnection, *, mode: str) -> None:
        self._cdp = cdp
        self._mode = mode
        self._unacked: int | None = None

    @property
    def controllable(self) -> bool:
        return self._mode == CONTROL

    async def start(self) -> None:
        await self._cdp.call("Page.enable")
        await self._cdp.call(
            "Page.startScreencast",
            {
                "format": _FRAME_FORMAT,
                "quality": _FRAME_QUALITY,
                "maxWidth": _MAX_DIMENSION,
                "maxHeight": _MAX_DIMENSION,
            },
        )

    async def stop(self) -> None:
        with suppress(Exception):
            await self._cdp.call("Page.stopScreencast")

    async def handle_cdp_event(self, raw: str) -> dict | None:
        """Turn one CDP event into a viewer message, or None to ignore it.

        Only two events matter to a viewer: a frame to paint, and the page
        having gone somewhere else, which is worth showing because it is how
        they know a sign-in took.
        """
        try:
            message = json.loads(raw)
        except ValueError:
            return None
        method = message.get("method")
        params = message.get("params") or {}

        if method == "Page.screencastFrame":
            session_id = params.get("sessionId")
            metadata = params.get("metadata") or {}
            # Latest wins: acknowledge whatever was outstanding so Chrome keeps
            # sending, and let the newer frame supersede it.
            if self._unacked is not None and self._unacked != session_id:
                await self._ack(self._unacked)
            self._unacked = session_id
            return {
                "t": "frame",
                "seq": session_id,
                "data": params.get("data", ""),
                "w": metadata.get("deviceWidth"),
                "h": metadata.get("deviceHeight"),
                "offsetTop": metadata.get("offsetTop", 0),
                "scale": metadata.get("pageScaleFactor", 1),
            }

        if method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            # Only the top frame: an ad iframe navigating is not the person's
            # sign-in completing, and showing it as one would be a lie about
            # which site they are looking at.
            if frame.get("parentId"):
                return None
            return {"t": "navigated", "url": frame.get("url", "")}

        return None

    async def _ack(self, session_id: int | None) -> None:
        if session_id is None:
            return
        with suppress(Exception):
            await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})

    async def handle_viewer_message(self, raw: str) -> dict | None:
        """Apply one message from the viewer. Returns a reply, or None.

        Anything unrecognised is refused in words rather than dropped: a client
        waiting on a reply that never comes looks like a hang, and the person
        seeing it cannot tell that from a broken browser.
        """
        try:
            message = json.loads(raw)
        except ValueError:
            return {"t": "error", "code": "unreadable", "message": "not JSON"}

        kind = message.get("t")

        if kind == "ack":
            # The viewer painted it. Acknowledge only if it is still the frame
            # we are holding -- a late ack for a superseded frame would let two
            # frames be in flight.
            if self._unacked is not None and message.get("seq") == self._unacked:
                await self._ack(self._unacked)
                self._unacked = None
            return None

        if kind == "ping":
            return {"t": "pong"}

        if kind == "input":
            if not self.controllable:
                return {
                    "t": "error",
                    "code": "read_only",
                    "message": "this view is watching, not driving",
                }
            return await self._dispatch_input(message)

        if kind == "viewport":
            # Chrome decides the frame size from the page; a viewer's own size
            # only changes how it is drawn on their end. Accepted and ignored so
            # a client can send it without getting an error back.
            return None

        return {
            "t": "error",
            "code": "unknown_message",
            "message": f"{kind!r} is not something a viewer can send",
        }

    async def _dispatch_input(self, message: dict) -> dict | None:
        event = message.get("event")
        if not isinstance(event, dict):
            return {
                "t": "error",
                "code": "malformed_input",
                "message": "input needs an event object",
            }
        kind = str(event.get("kind", ""))
        method = _INPUT_METHODS.get(kind)
        if method is None:
            return {
                "t": "error",
                "code": "unknown_input",
                "message": f"{kind!r} is not an input this view accepts",
            }
        params = {k: v for k, v in event.items() if k != "kind"}
        with suppress(Exception):
            await self._cdp.send(method, params)
        return None


async def pump(
    cdp_socket,
    session: ScreencastSession,
    *,
    send_json,
    receive_text,
) -> None:
    """Copy frames out and input in until either side stops.

    Two tasks rather than one loop: a screencast pushes frames continuously
    while a viewer sends nothing for long stretches, so interleaving the reads
    would stall the picture behind an input that never comes.

    The viewer's socket is not a parameter: everything this needs from it is
    already `send_json` and `receive_text`, and taking the socket as well meant
    holding a thing it never used.
    """

    async def to_viewer() -> None:
        async for raw in cdp_socket:
            if isinstance(raw, bytes):
                continue
            outgoing = await session.handle_cdp_event(raw)
            if outgoing is not None:
                await send_json(outgoing)

    async def to_browser() -> None:
        while True:
            raw = await receive_text()
            if raw is None:
                return
            reply = await session.handle_viewer_message(raw)
            if reply is not None:
                await send_json(reply)

    # Inherited: both halves are this viewer's own work, so they keep the
    # context the socket was accepted in rather than starting a fresh one.
    tasks = [create_inherited_task(to_viewer()), create_inherited_task(to_browser())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            with suppress(asyncio.CancelledError, Exception):
                task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
