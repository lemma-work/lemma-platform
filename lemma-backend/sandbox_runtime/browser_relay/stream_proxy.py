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
the protocol, not by Chrome.

So this is a pass-through with exactly one rule of its own: **a viewer
watching may not type.** RFB does not know about that distinction -- it takes
input from whoever connects -- so `view` mode is enforced here, at the only
point that knows which mode was asked for.

**Enforced per RFB message, not per WebSocket frame.** A first version of
this checked only a frame's first byte, on the assumption that noVNC sends
one RFB message per WebSocket frame. That is true of noVNC; it is not
something this relay may trust, because nothing stops a viewer from opening
the socket with a client that is not noVNC at all and packing a legitimate
message's bytes followed by a smuggled `KeyEvent` or `PointerEvent` into one
frame -- RFB is a byte stream to the server on the other end of `upstream`,
which has no notion of WebSocket frame boundaries and reads message after
message regardless of how they arrived. So a frame that passed the
first-byte check still reached the server carrying full keyboard and mouse
control, undoing the check that was supposed to prevent exactly that.
`_view_mode_messages` walks the whole frame instead, message by message, by
the same fixed-size and header-driven length rules the RFB protocol itself
defines for view-safe messages -- and a frame that does not decompose
cleanly into a whole number of them is dropped outright rather than
forwarded in part.
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

#: RFB client-to-server message types, by their first byte.
_SET_PIXEL_FORMAT = 0
_SET_ENCODINGS = 2
_FRAMEBUFFER_UPDATE_REQUEST = 3

#: What a view-mode viewer may still send: the messages that only ask for a
#: picture, never move a mouse or press a key. `ClientCutText` (6) is
#: deliberately absent -- nothing in this product pastes from view mode,
#: `browser-pane.tsx` gates `clipboardPasteFrom` on driving, and admitting an
#: input-carrying message here on the promise that a caller happens not to
#: use it is the allowlist-erosion this module's docstring warns against.
_VIEW_SAFE_TYPES = frozenset(
    {_SET_PIXEL_FORMAT, _SET_ENCODINGS, _FRAMEBUFFER_UPDATE_REQUEST}
)


def _view_safe_message_length(buf: bytes) -> int | None:
    """How many bytes of `buf` are one complete, view-safe RFB message.

    `None` for anything else -- an unrecognised type, a message this mode
    does not admit at all, or a header present but too short to trust yet
    (the caller's job to ask again once more bytes have arrived, not this
    function's to guess). Never more than `len(buf)`, so a caller never reads
    past what was actually received.
    """
    if not buf:
        return None
    kind = buf[0]
    if kind == _SET_PIXEL_FORMAT:
        # type(1) + padding(3) + pixel format(16)
        return 20 if len(buf) >= 20 else None
    if kind == _FRAMEBUFFER_UPDATE_REQUEST:
        # type(1) + incremental(1) + x(2) + y(2) + w(2) + h(2)
        return 10 if len(buf) >= 10 else None
    if kind == _SET_ENCODINGS:
        # type(1) + padding(1) + number-of-encodings(2, big-endian) + 4 bytes each
        if len(buf) < 4:
            return None
        count = int.from_bytes(buf[2:4], "big")
        total = 4 + 4 * count
        return total if len(buf) >= total else None
    return None


def _view_mode_messages(frame: bytes) -> bytes | None:
    """`frame`, if it is exactly a whole number of view-safe RFB messages.

    `None` otherwise -- including a frame that is a view-safe message
    followed by anything else, trailing bytes included. Returning a *prefix*
    instead would forward whatever came first and silently discard the
    remainder, which reads as "mostly worked" for a frame that was in fact
    carrying something this relay does not admit. Dropping the whole frame is
    the only answer that cannot be read as partial success.
    """
    offset = 0
    while offset < len(frame):
        if frame[offset] not in _VIEW_SAFE_TYPES:
            return None
        length = _view_safe_message_length(frame[offset:])
        if length is None:
            return None
        offset += length
    return frame


async def pump_binary(upstream, *, mode: str, send_bytes, receive_bytes) -> None:
    """Copy frames out and filtered input in, until either side stops.

    Two tasks rather than one loop: the display pushes updates continuously
    while a viewer sends nothing for long stretches, so interleaving the reads
    would stall the picture behind an input that never comes.
    """

    async def to_viewer() -> None:
        async for raw in upstream:
            await send_bytes(raw if isinstance(raw, bytes) else raw.encode())

    async def to_upstream() -> None:
        while True:
            raw = await receive_bytes()
            if raw is None:
                return
            if mode != CONTROL and _view_mode_messages(raw) is None:
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
