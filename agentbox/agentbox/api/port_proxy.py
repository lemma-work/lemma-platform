from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
import websockets

from agentbox.domain import AgentBoxError
from agentbox.port_access import PortAccessService

from .deps import port_access
from .fabric import agentbox_error_response


access_router = APIRouter()
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
    }
)
_PRIVATE_REQUEST_HEADERS = frozenset(
    {"host", "x-api-key", "content-length"}
)


@access_router.api_route(
    "/port-access/{token}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@access_router.api_route(
    "/port-access/{token}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def proxy_http_port(
    request: Request,
    token: str,
    path: str = "",
    service: PortAccessService = Depends(port_access),
):
    deadline = datetime.now(timezone.utc) + timedelta(seconds=20)
    try:
        _claims, target = await service.resolve(token, deadline_at=deadline)
    except AgentBoxError as exc:
        return agentbox_error_response(exc)

    target_url = _upstream_url(target.base_url, path, request.url.query)
    headers = _request_headers(list(request.headers.items()))
    headers.update({item.name: item.value for item in target.headers})
    headers["X-Forwarded-Proto"] = request.url.scheme
    if request.headers.get("host"):
        headers["X-Forwarded-Host"] = request.headers["host"]

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(20, read=None), follow_redirects=False
    )
    upstream_request = client.build_request(
        request.method,
        target_url,
        headers=headers,
        content=request.stream(),
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except Exception:
        await client.aclose()
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "PROVIDER_UNAVAILABLE", "message": "sandbox port is unavailable", "retry": "wait", "retry_after_ms": 500, "context": None}},
        )

    response_headers = {
        name: value
        for name, value in upstream.headers.multi_items()
        if name.lower() not in _HOP_BY_HOP
        and name.lower() != "content-length"
    }
    location = response_headers.get("location")
    if location and location.startswith(target.base_url):
        public_prefix = str(request.base_url).rstrip("/") + f"/port-access/{token}"
        response_headers["location"] = public_prefix + location[len(target.base_url) :]

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


@access_router.websocket("/port-access/{token}")
@access_router.websocket("/port-access/{token}/{path:path}")
async def proxy_websocket_port(
    websocket: WebSocket,
    token: str,
    path: str = "",
    service: PortAccessService = Depends(port_access),
) -> None:
    deadline = datetime.now(timezone.utc) + timedelta(seconds=20)
    try:
        _claims, target = await service.resolve(token, deadline_at=deadline)
    except AgentBoxError:
        await websocket.close(code=1008, reason="invalid or stale port-access grant")
        return

    http_url = _upstream_url(target.base_url, path, websocket.url.query)
    parts = urlsplit(http_url)
    upstream_url = urlunsplit(
        ("wss" if parts.scheme == "https" else "ws", parts.netloc, parts.path, parts.query, "")
    )
    headers = {
        item.name: item.value
        for item in target.headers
    }
    subprotocols = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    origin = f"{parts.scheme}://{parts.netloc}"
    try:
        async with websockets.connect(
            upstream_url,
            origin=origin,
            subprotocols=subprotocols or None,
            additional_headers=headers or None,
            open_timeout=20,
            max_size=None,
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        await upstream.close()
                        return
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            _done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    except Exception:
        try:
            await websocket.close(code=1011, reason="sandbox port proxy failed")
        except RuntimeError:
            pass


def _upstream_url(base_url: str, path: str, query: str) -> str:
    parts = urlsplit(base_url)
    base_path = parts.path.rstrip("/")
    target_path = f"{base_path}/{path}" if path else f"{base_path}/"
    return urlunsplit((parts.scheme, parts.netloc, target_path, query, ""))


def _request_headers(items: list[tuple[str, str]]) -> dict[str, str]:
    return {
        name: value
        for name, value in items
        if name.lower() not in _HOP_BY_HOP
        and name.lower() not in _PRIVATE_REQUEST_HEADERS
    }
