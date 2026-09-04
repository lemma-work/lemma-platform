"""Reverse proxy for signed access to a port inside a sandbox.

Authorisation is the token in the path and nothing else, which is why this
router carries no user dependency: the URL is handed to a browser that has no
Lemma session, and the signature already names the exact sandbox, port, and
expiry it authorises.

Because the token is the whole credential, inbound headers that could be
mistaken for a *different* credential are dropped rather than forwarded -- a
sandbox must never see the caller's Lemma cookies or API key.

Both halves of HTTP are here. The request half buffers, which is right for the
small documents a sandbox app serves. The **WebSocket** half does not exist for
convenience: a live view of the agent's browser is a frame stream, and a proxy
that can only answer a request cannot carry one. It is a separate route because
an upgrade is a separate protocol, not a method.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import asyncio
import contextlib

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket, status
from fastapi.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

from app.core.config import settings
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers.base import ProviderGone, ProviderInstance
from app.modules.workspace.services.port_access import (
    PortAccessInvalid,
    PortAccessSigner,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/workspace-ports", tags=["Workspace"])

# Never forwarded upstream. `host` would break virtual hosting inside the
# sandbox; the rest are credentials for Lemma, not for the sandbox.
_STRIPPED_REQUEST_HEADERS = frozenset(
    {
        "host",
        "cookie",
        "authorization",
        "x-api-key",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
    }
)
_STRIPPED_RESPONSE_HEADERS = frozenset(
    {
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        # Replaced below rather than forwarded. Whatever the sandbox says about
        # who may frame it is a claim by the thing being proxied, and the answer
        # belongs to us.
        "content-security-policy",
        "x-frame-options",
    }
)


def _frame_ancestors() -> str:
    """Who may put a proxied sandbox page in a frame.

    The signed URL is a bearer token in a link, and a link leaks: pasted into a
    chat, caught by an unfurl bot, left in a history. `frame-ancestors` is what
    stops a leaked one being framed by somebody else's page and driven from
    there, which matters most for the takeover view — the one place a person is
    invited to type a password into a proxied frame.
    """
    origins = {settings.frontend_url.rstrip("/"), settings.api_url.rstrip("/")}
    return " ".join(sorted(origin for origin in origins if origin))


async def _resolve_target(token: str) -> str | None:
    """The sandbox's base URL for a signed grant, or None when it does not hold.

    Returns rather than raises because the two halves report a refusal
    differently — an HTTP status on one side, a close code on the other — and
    the decision itself is the same on both.
    """
    key = workspace_settings.runtime_credential_key
    if not key:
        return None
    try:
        grant = PortAccessSigner(key=key.encode()).verify(token)
    except PortAccessInvalid:
        return None

    from app.modules.workspace.services.sandbox_composition import get_sandbox_service

    service = get_sandbox_service()
    deadline_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    try:
        handle = await service.ensure(grant.sandbox_id)
        return await service._provider.port_base_url(
            ProviderInstance(
                provider_id=handle.provider_id, name=handle.provider_id, running=True
            ),
            port=grant.port,
            deadline_at=deadline_at,
        )
    except ProviderGone:
        return None


@router.websocket("/{token}")
@router.websocket("/{token}/{path:path}")
async def proxy_sandbox_websocket(
    websocket: WebSocket, token: str, path: str = ""
) -> None:
    """Carry a WebSocket to the same signed port the HTTP half serves.

    A live browser view is a frame stream, so this is what makes one possible at
    all. The same rule applies as on the request half: the token is the whole
    credential, and nothing that could be mistaken for a Lemma credential is
    forwarded — which here means the handshake is opened with headers of our
    own rather than the caller's.
    """
    target = await _resolve_target(token)
    if target is None:
        # Refused before accepting, so a caller without a valid grant never gets
        # an open socket. Expired and forged are indistinguishable, as on the
        # request half.
        await websocket.close(code=1008)
        return

    upstream_url = (
        httpx.URL(target)
        .copy_with(path="/" + quote(path.lstrip("/"), safe="/"))
        .copy_with(scheme="wss" if httpx.URL(target).scheme == "https" else "ws")
    )
    query = websocket.url.query
    upstream_target = f"{upstream_url}{'?' + query if query else ''}"

    await websocket.accept(
        subprotocol=websocket.headers.get("sec-websocket-protocol") or None
    )
    try:
        async with websockets.connect(
            upstream_target,
            open_timeout=15,
            # The sandbox is on the other side of a proxy that may idle it out;
            # a keepalive is what tells us the far end went away rather than
            # waiting forever on a socket nobody will write to again.
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        ) as upstream:
            await _pump(websocket, upstream)
    except OSError, websockets.exceptions.WebSocketException, asyncio.TimeoutError:
        logger.warning(
            "workspace.port_proxy.upstream_websocket.degraded", exc_info=True
        )
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1011)


async def _pump(client: WebSocket, upstream) -> None:
    """Copy frames both ways until either end stops.

    Two tasks rather than one loop, because a stream that is only read when the
    other side speaks is not a stream: a browser view sends frames continuously
    while the viewer sends nothing at all, and interleaving the two reads would
    stall it behind an input that never comes.
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

    async def to_client() -> None:
        async for frame in upstream:
            if isinstance(frame, str):
                await client.send_text(frame)
            else:
                await client.send_bytes(frame)

    # Inherited, not detached: these two carry frames for the connection that
    # spawned them and die with it, so they belong to its operation.
    tasks = [
        create_inherited_task(to_upstream(), name="workspace.port_proxy.to_upstream"),
        create_inherited_task(to_client(), name="workspace.port_proxy.to_client"),
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


# Two paths, one handler. The grant's own URL ends at the token with a trailing
# slash and no path at all — `/{token}/{path:path}` does not match that, so the
# very URL this proxy hands out 404'd while every deeper path worked.
@router.api_route(
    "/{token}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
@router.api_route(
    "/{token}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def proxy_sandbox_port(token: str, request: Request, path: str = "") -> Response:
    key = workspace_settings.runtime_credential_key
    if not key:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    try:
        grant = PortAccessSigner(key=key.encode()).verify(token)
    except PortAccessInvalid:
        # Expired and forged are deliberately indistinguishable to the caller.
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    from app.modules.workspace.services.sandbox_composition import get_sandbox_service

    service = get_sandbox_service()
    deadline_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    try:
        handle = await service.ensure(grant.sandbox_id)
        base_url = await service._provider.port_base_url(
            ProviderInstance(
                provider_id=handle.provider_id, name=handle.provider_id, running=True
            ),
            port=grant.port,
            deadline_at=deadline_at,
        )
    except ProviderGone:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    # `path` is caller-controlled, so the target is built from the trusted base
    # rather than handed to base_url merging. Merging would have been safe by
    # accident -- the leading slash stops it parsing as absolute -- but only by
    # accident, and it silently ate a segment when a path began with "//",
    # reading the first one as an authority. Setting the path component alone
    # makes the host un-influenceable by construction.
    target = httpx.URL(base_url).copy_with(path="/" + quote(path.lstrip("/"), safe="/"))

    upstream = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
    try:
        proxied = await upstream.request(
            request.method,
            target,
            params=request.query_params,
            headers={
                name: value
                for name, value in request.headers.items()
                if name.lower() not in _STRIPPED_REQUEST_HEADERS
            },
            content=await request.body(),
        )
    except httpx.HTTPError:
        await upstream.aclose()
        return Response(status_code=status.HTTP_502_BAD_GATEWAY)

    async def body():
        try:
            yield proxied.content
        finally:
            await upstream.aclose()

    headers = {
        name: value
        for name, value in proxied.headers.items()
        if name.lower() not in _STRIPPED_RESPONSE_HEADERS
    }
    headers["content-security-policy"] = f"frame-ancestors {_frame_ancestors()}"
    return StreamingResponse(
        body(),
        status_code=proxied.status_code,
        headers=headers,
    )
