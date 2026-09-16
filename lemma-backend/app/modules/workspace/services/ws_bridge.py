"""Carrying a WebSocket between a person's browser and something in a sandbox.

One implementation, used by both the port proxy and the browser view. They had
the same forty lines twice, and the copies had already drifted: one bounded its
frames and the other did not.

Two tasks rather than one loop, because a stream that is only read when the
other side speaks is not a stream. A browser view sends frames continuously
while the viewer sends nothing at all, so interleaving the reads would stall the
picture behind an input that never comes.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping

from fastapi import WebSocket, WebSocketDisconnect

from app.core.request_context import create_inherited_task

#: A frame larger than this is not a screencast frame or a keystroke, it is
#: something wrong. `max_size=None` -- which is what the port proxy carried --
#: means one frame from a process the agent controls is buffered whole in the
#: API's memory, so a sandbox could make the API fall over from inside.
MAX_FRAME_BYTES = 8 * 1024 * 1024

#: Closed because the far end went away, rather than because anyone decided to.
CLOSE_UPSTREAM_GONE = 1011


async def connect_upstream(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    subprotocols: tuple[str, ...] | None = None,
):
    """Open the sandbox-side socket, carrying whatever the fabric's door needs.

    The headers come from `reach_port`, so this stays ignorant of which fabric
    it is on: E2B wants a traffic token, a preview proxy wants its own, Docker
    wants nothing.

    Imported here rather than at module scope: the library is only ever needed
    once a socket is actually being opened, and naming it at the top puts it in
    the import graph of every process that merely registers a route.
    """
    import websockets

    return websockets.connect(
        url,
        additional_headers=dict(headers or {}),
        subprotocols=list(subprotocols) if subprotocols else None,
        max_size=MAX_FRAME_BYTES,
        # A dead peer that never closes leaves a task pinned to a socket that
        # will never speak again; the ping is what notices.
        ping_interval=20,
        ping_timeout=20,
        open_timeout=30,
    )


async def bridge(client: WebSocket, upstream, *, name: str = "workspace") -> None:
    """Copy frames both ways until either end stops, then take both down.

    The cleanup is the part worth reading. Cancelling the pending task is not
    enough on its own -- it has to be awaited, or the socket it holds is closed
    by garbage collection at a time nothing controls, which under load shows up
    as sockets that outlive their request.
    """

    async def to_upstream() -> None:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                return
            if (text := message.get("text")) is not None:
                await upstream.send(text)
            elif (data := message.get("bytes")) is not None:
                await upstream.send(data)

    import websockets

    async def to_client() -> None:
        async for frame in upstream:
            if isinstance(frame, str):
                await client.send_text(frame)
            else:
                await client.send_bytes(frame)

    # Inherited, not detached: these two carry frames for the connection that
    # spawned them and die with it, so they belong to its operation.
    tasks = [
        create_inherited_task(to_upstream(), name=f"{name}.to_upstream"),
        create_inherited_task(to_client(), name=f"{name}.to_client"),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            with contextlib.suppress(
                WebSocketDisconnect,
                websockets.exceptions.ConnectionClosed,
                asyncio.CancelledError,
            ):
                task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def origin_is_allowed(origin: str | None, *, allowed: tuple[str, ...]) -> bool:
    """Whether a WebSocket handshake came from somewhere we serve.

    Browsers do not apply the same-origin policy to WebSockets, and they do send
    cookies on the handshake -- so without this check any page anyone visits can
    open a socket to this API as the signed-in person and, on the browser view,
    watch their screen and type into it. The header cannot be forged by page
    script, which is what makes checking it worth anything.

    A missing Origin is allowed: it is what a non-browser client sends, and the
    CLI and the tests are non-browser clients. A *present* one has to match.
    """
    if origin is None:
        return True
    normalized = origin.rstrip("/").lower()
    return any(normalized == candidate.rstrip("/").lower() for candidate in allowed)
