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

#: The client's three RFB handshake steps, in order, by their fixed length:
#: the ProtocolVersion reply (`"RFB 003.008\n"`, 12 bytes), the chosen
#: security type (1 byte -- this relay's upstream only ever offers None), and
#: ClientInit's shared-flag (1 byte). None of these carry a message-type byte
#: the way every later client-to-server message does -- the RFB message
#: vocabulary this module otherwise validates against does not exist yet at
#: this point in the connection -- so `_view_mode_messages` cannot recognise
#: them and, before this was handled specially, silently dropped every one of
#: them: a view-mode viewer's handshake reply vanished into the filter and
#: the connection hung forever waiting for a server response to a client
#: message the server never received.
#:
#: Checked by length rather than left unfiltered by mode, so a would-be
#: smuggler cannot pad a handshake step with a trailing `KeyEvent` or
#: `PointerEvent` and have it pass for free: a frame with the wrong length
#: at this stage is refused outright, same as an unrecognised message type
#: is once the handshake is behind it.
_HANDSHAKE_STEP_LENGTHS = (12, 1, 1)

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


#: The client-to-server messages that are a person doing something, as
#: opposed to a person looking. Separate from `_VIEW_SAFE_TYPES` because the
#: question is different: that set decides what a watcher may *send*, this one
#: decides when a driver has actually taken the wheel.
_KEY_EVENT = 4
_POINTER_EVENT = 5
_CLIENT_CUT_TEXT = 6


def _carries_real_input(frame: bytes) -> bool:
    """Whether this frame is somebody acting, rather than the viewer idling.

    Bare pointer *motion* deliberately does not count. noVNC sends a
    `PointerEvent` for every mouse move across the canvas, so counting those
    would mean reading the page with the cursor over it took the wheel off the
    agent -- which is the whole failure this distinction exists to avoid. A
    button-mask of zero is the cursor passing through; anything else is a
    click or a drag.

    Unrecognised bytes count as input. This decides whether to *hold back* the
    agent, so the safe answer when the frame cannot be read is that somebody
    may be typing.
    """
    offset = 0
    while offset < len(frame):
        kind = frame[offset]
        if kind == _KEY_EVENT or kind == _CLIENT_CUT_TEXT:
            return True
        if kind == _POINTER_EVENT:
            # type(1) + button-mask(1) + x(2) + y(2)
            if len(frame) - offset < 6:
                return True
            if frame[offset + 1] != 0:
                return True
            offset += 6
            continue
        length = _view_safe_message_length(frame[offset:])
        if length is None:
            return True
        offset += length
    return False


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


async def pump_binary(
    upstream, *, mode: str, send_bytes, receive_bytes, on_input=None
) -> None:
    """Copy frames out and filtered input in, until either side stops.

    Two tasks rather than one loop: the display pushes updates continuously
    while a viewer sends nothing for long stretches, so interleaving the reads
    would stall the picture behind an input that never comes.

    The client's first three messages -- its RFB handshake -- are passed by
    length rather than through `_view_mode_messages`, in both modes: see
    `_HANDSHAKE_STEP_LENGTHS`.

    `on_input` is called, in control mode only, each time a frame carries a
    person actually doing something -- see `_carries_real_input`. It is how
    the driving lease is taken by *use* rather than by connection: a panel
    somebody has open but is not touching must not stop the agent working.
    """

    async def to_viewer() -> None:
        async for raw in upstream:
            await send_bytes(raw if isinstance(raw, bytes) else raw.encode())

    async def to_upstream() -> None:
        handshake_step = 0
        while True:
            raw = await receive_bytes()
            if raw is None:
                return
            if handshake_step < len(_HANDSHAKE_STEP_LENGTHS):
                if len(raw) != _HANDSHAKE_STEP_LENGTHS[handshake_step]:
                    return
                handshake_step += 1
                await upstream.send(raw)
                continue
            if mode != CONTROL and _view_mode_messages(raw) is None:
                continue
            if on_input is not None and mode == CONTROL and _carries_real_input(raw):
                on_input()
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
