"""Carrying one viewer's socket to `agent-browser`'s own stream server.

This replaces a hand-rolled CDP screencast: `Page.startScreencast`, a frame/ack
protocol we invented, and a translation from four message kinds into
`Input.dispatch*`. `agent-browser` ships all of that, session-scoped, and does
more with it -- touch events, a per-client frame-rate cap, a choice of push or
ack pacing, and latest-first frame dropping.

**Why proxying this is safe when proxying CDP was not.** The relay was written
with a note saying the alternative -- relaying CDP behind an allowlist -- had
been tried and rejected, because "the allowlist is the security boundary: every
method Chrome adds is admitted or refused by a list somebody has to maintain,
and getting it wrong once means handing a page `Runtime.evaluate`". That
objection does not apply here. The stream server's whole inbound vocabulary is

    input_mouse  input_keyboard  input_touch  config  ack
    screencast_start  screencast_stop

and there is nothing in it that evaluates script, reads a cookie, or names a CDP
method. The protocol is narrow by construction rather than by a list, which is
the property the original design wanted and could not get from CDP.

So this is a pass-through with exactly one rule of its own: **a viewer watching
may not type.** That is not something the stream server knows about -- it takes
input from whoever connects -- so `view` mode is enforced here, at the only
point that knows which mode was asked for.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging

from sandbox_runtime.tasks import create_inherited_task

_log = logging.getLogger(__name__)

#: What a viewer may ask for. `view` sends no input at all.
VIEW = "view"
CONTROL = "control"

#: Everything the stream server accepts that moves the mouse, presses a key or
#: touches the screen. Named as a set rather than matched on a prefix, so a
#: message type added upstream is refused until somebody has looked at it.
_INPUT_MESSAGES = frozenset({"input_mouse", "input_keyboard", "input_touch"})

#: What a viewer may send in either mode: pace themselves, acknowledge a frame,
#: and start or stop the explicit screencast. None of these touch the page.
_ALLOWED_ALWAYS = frozenset({"config", "ack", "screencast_start", "screencast_stop"})


def viewer_message_allowed(raw: str, *, mode: str) -> tuple[bool, dict | None]:
    """Whether this message may reach the stream, and a refusal if not.

    Unparseable messages are dropped rather than forwarded. Anything outside the
    known vocabulary is refused *in words*: a client waiting on a reply that
    never comes looks like a hang, and the person seeing it cannot tell that
    from a browser that has stopped.
    """
    try:
        message = json.loads(raw)
    except ValueError:
        return False, {"type": "error", "code": "unreadable", "message": "not JSON"}
    if not isinstance(message, dict):
        return False, {
            "type": "error",
            "code": "unreadable",
            "message": "not an object",
        }

    kind = str(message.get("type") or "")
    if kind in _INPUT_MESSAGES:
        if mode != CONTROL:
            return False, {
                "type": "error",
                "code": "read_only",
                "message": "this view is watching, not driving",
            }
        return True, None
    if kind in _ALLOWED_ALWAYS:
        return True, None
    return False, {
        "type": "error",
        "code": "unknown_message",
        "message": f"{kind!r} is not something a viewer can send",
    }


async def pump(stream_socket, *, mode: str, send_text, receive_text) -> None:
    """Copy frames out and input in until either side stops.

    Two tasks rather than one loop: the stream pushes frames continuously while
    a viewer sends nothing for long stretches, so interleaving the reads would
    stall the picture behind an input that never comes.

    Frames go out untouched. They are the stream server's own JSON and the
    client is written against it, so parsing and re-encoding each one would cost
    a decode per frame to change nothing.
    """

    async def to_viewer() -> None:
        async for raw in stream_socket:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            await send_text(raw)

    async def to_stream() -> None:
        while True:
            raw = await receive_text()
            if raw is None:
                return
            allowed, refusal = viewer_message_allowed(raw, mode=mode)
            if allowed:
                await stream_socket.send(raw)
            elif refusal is not None:
                await send_text(json.dumps(refusal))

    tasks = [create_inherited_task(to_viewer()), create_inherited_task(to_stream())]
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


__all__ = ["CONTROL", "VIEW", "pump", "viewer_message_allowed"]
