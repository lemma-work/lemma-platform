"""Carrying one VNC viewer's socket to `websockify`, in front of `x11vnc`.

Two earlier designs lived here in turn: driving CDP's `Page.startScreencast`
and `Input.dispatch*` ourselves, then proxying `agent-browser`'s own
session-scoped JSON/JPEG stream server. Both are gone -- VNC shows the real
Xvfb display Chrome already runs on, so there is no frame protocol to parse
and nothing here to translate a DOM event into.

**Why proxying this is safe when proxying CDP was not.** The relay was written
with a note saying the alternative -- relaying CDP behind an allowlist -- had
been tried and rejected, because "the allowlist is the security boundary:
every method Chrome adds is admitted or refused by a list somebody has to
maintain, and getting it wrong once means handing a page `Runtime.evaluate`".
RFB does not have that problem: its client-to-server vocabulary is fixed by
the protocol, not by Chrome, and every message's type is its first byte --
narrow enough to filter without parsing the rest.

So this is a pass-through with exactly one rule of its own: **a viewer
watching may not type.** RFB does not know about that distinction -- it takes
input from whoever connects -- so `view` mode is enforced here, at the only
point that knows which mode was asked for.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

from sandbox_runtime.tasks import create_inherited_task

_log = logging.getLogger(__name__)

#: What a viewer may ask for. `view` sends no input at all.
VIEW = "view"
CONTROL = "control"

#: The RFB (VNC) message types a client sends that move the mouse or press a
#: key: the first byte of every client-to-server RFB message names its type,
#: and noVNC sends one WebSocket binary frame per message -- so enforcing "a
#: viewer watching may not type" needs no parsing, only a look at one byte.
#: 4 = KeyEvent, 5 = PointerEvent.
_RFB_INPUT_MESSAGE_TYPES = frozenset({4, 5})


async def pump_binary(upstream, *, mode: str, send_bytes, receive_bytes) -> None:
    """Copy frames out and filtered input in, until either side stops.

    Two tasks rather than one loop: the display pushes updates continuously
    while a viewer sends nothing for long stretches, so interleaving the reads
    would stall the picture behind an input that never comes.

    There is no vocabulary to allow-list the way a JSON protocol would have
    one -- RFB is binary and this relay does not otherwise parse it, and
    parsing more of it than this one rule needs would be exactly the
    allowlist-maintenance burden the module docstring above explains proxying
    CDP directly was rejected for. So the rule stays as narrow as the framing
    allows: drop a client frame if its first byte says it moves the mouse or
    presses a key, forward every other frame -- framebuffer requests,
    encodings, and an inbound clipboard update -- untouched.
    """

    async def to_viewer() -> None:
        async for raw in upstream:
            await send_bytes(raw if isinstance(raw, bytes) else raw.encode())

    async def to_upstream() -> None:
        while True:
            raw = await receive_bytes()
            if raw is None:
                return
            if mode != CONTROL and raw and raw[0] in _RFB_INPUT_MESSAGE_TYPES:
                continue
            await upstream.send(raw)

    tasks = [create_inherited_task(to_viewer()), create_inherited_task(to_upstream())]
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


__all__ = ["CONTROL", "VIEW", "pump_binary"]
