"""`/ports/{port}/...`: a port on this machine's loopback, through the runtime.

A preview turns a port inside a machine into a URL. E2B reaches any port by
host name, and a Docker machine on a private network by its address -- but a
Docker machine on a laptop has neither: only the ports its container
published are reachable, and every one of those is the runtime's own. Measured
on a local stack: a preview of a dev server on 8123 answered "Nothing is
answering on that port" for ever, and one on 8080 reached this runtime
instead of the server the person started. So the runtime, which is already
published and already authenticated, carries the request the last hop.

Behind the runtime credential like every other route here, and two rules of
its own:

- **The credential stops here.** The request arrives carrying the runtime's
  token, and the server it is forwarded to is whatever a person or an agent
  ran -- so the header is removed before anything is sent on.
- **The machine's own doors are not previews.** The runtime, the browser
  relay, the dashboard, VNC and the streamer are refused by port, and so is
  Chrome's DevTools port, read from wherever Chrome recorded it. DevTools
  takes a `localhost` request at its word, and a preview of it would be the
  person's signed-in browser handed to whoever holds the link.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from sandbox_runtime.paths import BROWSER_PROFILE_ROOT
from sandbox_runtime.tasks import create_inherited_task

#: Ports that belong to the machine rather than to anything run on it: the
#: workspace runtime, the agent-browser dashboard, the browser relay, VNC and
#: its WebSocket bridge, and the WebRTC streamer.
RESERVED_PORTS = frozenset({8080, 4848, 4850, 5900, 5901, 4860})

#: The header the runtime's own clients authenticate with. Never forwarded.
_RUNTIME_TOKEN_HEADER = "x-lemma-runtime-token"

#: Not forwarded either way: they describe this hop, not the request.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
        _RUNTIME_TOKEN_HEADER,
    }
)

_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def devtools_ports(roots: Iterable[Path] | None = None) -> set[int]:
    """Every port a Chrome on this machine has recorded for DevTools.

    Each profile under ~/.lemma/browser, and agent-browser's scratch profiles
    in /tmp, which is where it puts a browser it was not given a profile for.
    """
    files: list[Path] = []
    for root in roots or (Path(BROWSER_PROFILE_ROOT), Path("/tmp")):
        with suppress(OSError):
            files.extend(root.glob("*/DevToolsActivePort"))
    ports: set[int] = set()
    for path in files:
        with suppress(OSError, IndexError, ValueError):
            ports.add(int(path.read_text().splitlines()[0].strip()))
    return ports


def refusal(port: int) -> str | None:
    """Why this port may not be reached, or None when it may."""
    if not 1 <= port <= 65535:
        return "not a port"
    if port in RESERVED_PORTS:
        return "that port is the machine's own"
    if port in devtools_ports():
        return "that port is the browser's DevTools"
    return None


def _forwarded(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(name, value) for name, value in headers if name.lower() not in _HOP_BY_HOP]


def create_router(client: httpx.AsyncClient | None = None) -> APIRouter:
    """The relay's routes; the app adds its own authentication in front."""
    router = APIRouter()
    shared = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))

    @router.api_route("/ports/{port}", methods=_METHODS)
    @router.api_route("/ports/{port}/{path:path}", methods=_METHODS)
    async def relay(port: int, request: Request, path: str = "") -> Response:
        refused = refusal(port)
        if refused is not None:
            return Response(refused, status_code=403, media_type="text/plain")
        upstream = shared.build_request(
            request.method,
            f"http://127.0.0.1:{port}/{path}",
            params=request.query_params,
            headers=[
                *_forwarded(request.headers.items()),
                ("host", f"localhost:{port}"),
            ],
            content=request.stream(),
        )
        try:
            answer = await shared.send(upstream, stream=True)
        except httpx.ConnectError:
            return Response(
                "Nothing is answering on that port right now.",
                status_code=502,
                media_type="text/plain",
            )
        return StreamingResponse(
            answer.aiter_raw(),
            status_code=answer.status_code,
            headers=dict(_forwarded(answer.headers.multi_items())),
            background=BackgroundTask(answer.aclose),
        )

    @router.websocket("/ports/{port}")
    @router.websocket("/ports/{port}/{path:path}")
    async def relay_socket(websocket: WebSocket, port: int, path: str = "") -> None:
        if refusal(port) is not None:
            await websocket.close(code=1008)
            return
        query = websocket.url.query
        target = f"ws://127.0.0.1:{port}/{path}" + (f"?{query}" if query else "")
        protocols = [
            item.strip()
            for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if item.strip()
        ]
        try:
            upstream = await websockets.connect(
                target,
                subprotocols=protocols or None,
                additional_headers={"Host": f"localhost:{port}"},
                max_size=None,
            )
        except OSError, websockets.InvalidHandshake:
            await websocket.close(code=1011)
            return
        await websocket.accept(subprotocol=upstream.subprotocol)
        await _bridge(websocket, upstream)

    return router


async def _bridge(websocket: WebSocket, upstream) -> None:
    """Both directions until either end stops."""

    async def to_upstream() -> None:
        with suppress(WebSocketDisconnect):
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    await upstream.send(message["bytes"])
                elif message.get("text") is not None:
                    await upstream.send(message["text"])

    async def to_client() -> None:
        with suppress(websockets.ConnectionClosed):
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

    tasks = [
        create_inherited_task(to_upstream(), name="port-relay-to-upstream"),
        create_inherited_task(to_client(), name="port-relay-to-client"),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.close()
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()
