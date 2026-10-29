"""Carrying one WebRTC viewer's signalling to the streamer.

The picture does not come through here. It goes from the streamer to the
viewer over WebRTC, through TURN, and this process never sees a frame of it.
What comes through is the handful of JSON messages that set that up -- the
offer, the answer, ICE candidates -- and the two that change who is in
control. That is why this is cheap where `/vnc` was not: the relay's share of
a VNC stream was every frame, deflated for the API and inflated again.

What the relay adds is what it adds to `/vnc`: the token check, starting the
streamer the first time somebody looks, keeping the browser awake while they
do, and counting them as a viewer for the resize lease. It does not decide
anything about input. The streamer verifies a ticket the API signed with a key
this process only delivers, so a relay that passed along anything at all could
still not turn a watcher into a driver.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
import tempfile

from fastapi import WebSocket
import websockets

from sandbox_runtime.paths import sandbox_command
from sandbox_runtime.tasks import create_inherited_task

from .chrome import answers_on
from .viewers import CONTROL, VIEW, VIEWERS

STREAM_PORT = int(os.environ.get("LEMMA_BROWSER_STREAM_PORT", "4860"))

#: The image has no streamer: built before it existed. The pane reads this as
#: "use VNC", which every image has.
CLOSE_NO_STREAMER = 4427

#: `start-streamer` says the binary is missing with this exit code, so that
#: "an older image" is told apart from "it failed to start".
_NOT_INSTALLED_EXIT = 3

#: A signalling message is an SDP blob or an ICE candidate. An SDP for one
#: video track and two data channels is a few kilobytes; anything near this is
#: not signalling.
MAX_SIGNAL_BYTES = 64 * 1024

#: Bounded well under the API's handshake timeout, for the reason
#: `chrome._VNC_BRIDGE_TIMEOUT_SECONDS` gives: this runs before `accept()`.
_START_TIMEOUT_SECONDS = 10.0

_log = logging.getLogger(__name__)


async def ensure_streamer() -> str:
    """Start the streamer if it is not up: `ready`, `absent` or `failed`.

    Stderr goes to a file and this waits on the process, not `communicate()`:
    the script leaves a `setsid` child running that inherits any pipe, and the
    pipe would never reach EOF. `chrome.ensure_vnc_bridge` measured exactly
    that as an eight-second stall on every viewer.
    """
    if await answers_on(STREAM_PORT):
        return "ready"
    script = sandbox_command("start-streamer")
    if not Path(script).exists():
        return "absent"
    with tempfile.TemporaryDirectory(prefix="lemma-streamer-") as directory:
        errors = Path(directory) / "stderr"
        try:
            with errors.open("wb") as sink:
                process = await asyncio.create_subprocess_exec(
                    script, stdout=asyncio.subprocess.DEVNULL, stderr=sink
                )
        except OSError as exc:
            _log.warning("could not start the streamer: %r", exc)
            return "failed"
        try:
            await asyncio.wait_for(process.wait(), timeout=_START_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(ProcessLookupError):
                await process.wait()
            _log.warning(
                "the streamer did not answer within %.0fs", _START_TIMEOUT_SECONDS
            )
            return "failed"
        if process.returncode == _NOT_INSTALLED_EXIT:
            return "absent"
        if process.returncode != 0:
            said = errors.read_bytes().decode("utf-8", "replace").strip()
            _log.warning("the streamer did not come up:\n%s", said)
            return "failed"
    return "ready" if await answers_on(STREAM_PORT) else "failed"


def mode_change(raw: str) -> str | None:
    """The mode a streamer message says this viewer now has, if it says one.

    Read off the streamer's replies, never off what the API sent: the
    streamer is the one that verified a ticket, so its `ready` and `mode`
    are the facts, and an API request it refused changes nothing here.
    """
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(message, dict) or message.get("t") not in ("ready", "mode"):
        return None
    mode = message.get("mode")
    return mode if mode in (VIEW, CONTROL) else None


async def pump_signalling(websocket: WebSocket, upstream, *, viewer: str) -> None:
    """Copy text frames both ways until either side stops.

    Binary frames and oversized ones end the session: neither is signalling,
    and passing them on would make this a general-purpose tunnel into a
    loopback port.
    """

    async def to_streamer() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is None or len(text) > MAX_SIGNAL_BYTES:
                return
            await upstream.send(text)

    async def to_viewer() -> None:
        async for raw in upstream:
            if not isinstance(raw, str):
                return
            if (mode := mode_change(raw)) is not None:
                VIEWERS.set_mode(viewer, mode)
            await websocket.send_text(raw)

    tasks = [create_inherited_task(to_streamer()), create_inherited_task(to_viewer())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def connect_streamer():
    """The streamer's signalling socket, on loopback.

    `compression=None`: nothing here is worth deflating, and the stream bench
    measured the relay's deflate as its single largest cost on `/vnc`.
    """
    return websockets.connect(
        f"ws://127.0.0.1:{STREAM_PORT}/signal",
        max_size=MAX_SIGNAL_BYTES,
        compression=None,
        open_timeout=10,
    )


__all__ = [
    "CLOSE_NO_STREAMER",
    "MAX_SIGNAL_BYTES",
    "STREAM_PORT",
    "connect_streamer",
    "ensure_streamer",
    "mode_change",
    "pump_signalling",
]
