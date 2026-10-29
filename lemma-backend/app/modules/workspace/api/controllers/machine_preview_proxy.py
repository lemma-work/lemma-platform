"""Serving a preview: ``p-<label>.<MACHINE_PREVIEW_DOMAIN>`` onto a machine's port.

`MachinePreviewHostMiddleware` rewrites a preview host's requests -- HTTP and
WebSocket both, because a dev server's hot reload is a socket -- onto
``/public/machine-previews/<label>/<path>``, and marks them as having come
through it. The handlers refuse anything unmarked: the same path asked for on
the API's own host must not be a way around the preview's origin.

What a preview is forwarded, and what not. The preview's own cookies go
through -- a dev server with a login needs them. The preview's access cookie
does not, and nothing that could be mistaken for a Lemma credential does:
Lemma's session cookies live on the API's host and never reach this one, and
`authorization` and `x-api-key` are dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, Response, WebSocket, status
from fastapi.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.log.log import get_logger
from app.modules.workspace.api.controllers.port_proxy_controller import (
    _forward,
    _frame_ancestors,
    get_port_proxy_client,
)
from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderGone,
    ProviderInstance,
    ProviderRejected,
    SandboxEndpoint,
    require_capability,
)
from app.modules.workspace.services.machine_previews import (
    AUTH_PATH,
    COOKIE_NAME,
    COOKIE_SECONDS,
    MachinePreviews,
    Preview,
    cookie_is_valid,
    label_from_host,
    mint_cookie,
    open_url,
    ticket_is_valid,
)
from app.modules.workspace.services.ws_bridge import bridge, connect_upstream
from sandbox_runtime.errors import SandboxCapabilityUnsupported, SandboxError

logger = get_logger(__name__)

PREFIX = "/public/machine-previews"
_MARKER = b"x-lemma-preview-host"
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

router = APIRouter(tags=["Machine Previews"])


class MachinePreviewHostMiddleware:
    """Route a preview host's requests to the preview proxy, both protocols."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = [(k, v) for k, v in scope["headers"] if k.lower() != _MARKER]
        scope["headers"] = headers
        host = next(
            (v.decode("latin-1") for k, v in headers if k.lower() == b"host"), ""
        )
        label = label_from_host(host)
        if label is None:
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        new_path = f"{PREFIX}/{label}{path}"
        scope["path"] = new_path
        scope["raw_path"] = new_path.encode("utf-8")
        scope["headers"] = [*headers, (_MARKER, b"1")]
        await self.app(scope, receive, send)


def _ws_error() -> type[Exception]:
    import websockets

    return websockets.exceptions.WebSocketException


def _came_through_host(headers) -> bool:
    return headers.get(_MARKER.decode()) == "1"


def _preview_cookie(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except CookieError:
        # A malformed cookie header carries no access cookie.
        return None
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else None


def _forwarded_cookie(cookie_header: str | None) -> str | None:
    """The preview's own cookies, without its access cookie."""
    if not cookie_header:
        return None
    kept = [
        part.strip()
        for part in cookie_header.split(";")
        if part.strip() and not part.strip().startswith(f"{COOKIE_NAME}=")
    ]
    return "; ".join(kept) or None


_STRIPPED = frozenset(
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
        _MARKER.decode(),
    }
)


def _target(base: str, path: str) -> httpx.URL:
    """The visitor's path under the endpoint's own.

    Appended, not substituted: an endpoint can carry a path of its own -- a
    Docker machine's port is reached through the runtime at `/ports/<port>` --
    and replacing it sent every request to the runtime's root instead.
    """
    url = httpx.URL(base)
    prefix = url.path.rstrip("/")
    return url.copy_with(path=f"{prefix}/" + quote(path.lstrip("/"), safe="/"))


def _upstream(headers, fabric) -> dict[str, str]:
    forwarded = {k: v for k, v in headers.items() if k.lower() not in _STRIPPED}
    if cookie := _forwarded_cookie(headers.get("cookie")):
        forwarded["cookie"] = cookie
    return {**forwarded, **fabric}


async def _live(label: str) -> Preview | None:
    from app.core.api.dependencies import get_uow_factory

    preview = await MachinePreviews(get_uow_factory()).by_label(label)
    return preview if preview is not None and preview.live else None


async def _endpoint(preview: Preview) -> SandboxEndpoint | None:
    from app.modules.workspace.services.sandbox_composition import get_sandbox_service

    service = get_sandbox_service()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    try:
        handle = await service.ensure(preview.sandbox_id)
        require_capability(service._provider, ProviderCapability.PORT_REACH)
        return await service._provider.reach_port(
            ProviderInstance(
                provider_id=handle.provider_id, name=handle.provider_id, running=True
            ),
            port=preview.port,
            deadline_at=deadline,
        )
    except ProviderGone, ProviderRejected, SandboxCapabilityUnsupported, SandboxError:
        return None


def _authorized(preview: Preview, cookie_header: str | None) -> bool:
    if preview.visibility == "link":
        return True
    token = _preview_cookie(cookie_header)
    return token is not None and cookie_is_valid(token, preview.label)


@router.api_route(PREFIX + "/{label}", methods=_METHODS, include_in_schema=False)
@router.api_route(
    PREFIX + "/{label}/{path:path}", methods=_METHODS, include_in_schema=False
)
async def proxy_preview(label: str, request: Request, path: str = "") -> Response:
    if not _came_through_host(request.headers):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    preview = await _live(label)
    if preview is None:
        return Response(
            "This preview was revoked or has expired.",
            status_code=status.HTTP_404_NOT_FOUND,
            media_type="text/plain",
        )
    if "/" + path == AUTH_PATH:
        return _exchange(preview, request.query_params.get("ticket") or "")
    if not _authorized(preview, request.headers.get("cookie")):
        return RedirectResponse(
            open_url(preview.label), status_code=status.HTTP_302_FOUND
        )
    endpoint = await _endpoint(preview)
    if endpoint is None:
        return Response(
            "Nothing is answering on that port right now.",
            status_code=status.HTTP_502_BAD_GATEWAY,
            media_type="text/plain",
        )
    target = _target(endpoint.url, path)
    headers = _upstream(request.headers, endpoint.headers)
    content = None
    if request.headers.get("content-length") not in (None, "", "0") or (
        "transfer-encoding" in request.headers
    ):
        if length := request.headers.get("content-length"):
            headers["content-length"] = length
        content = request.stream()
    response = await _forward(
        get_port_proxy_client(),
        httpx.Request(
            request.method,
            target,
            params=request.query_params,
            headers=headers,
            content=content,
        ),
    )
    response.headers["content-security-policy"] = (
        f"frame-ancestors 'self' {_frame_ancestors()}"
    )
    return response


def _exchange(preview: Preview, ticket: str) -> Response:
    """A ticket from the API becomes this host's own cookie, then the page."""
    if not ticket_is_valid(ticket, preview.label):
        return Response(
            "That link has expired. Open the preview from Lemma again.",
            status_code=status.HTTP_403_FORBIDDEN,
            media_type="text/plain",
        )
    redirect = RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        COOKIE_NAME,
        mint_cookie(preview.label),
        max_age=COOKIE_SECONDS,
        httponly=True,
        secure=_secure(),
        samesite="lax",
        path="/",
    )
    return redirect


def _secure() -> bool:
    from app.modules.workspace.config import workspace_settings

    return workspace_settings.machine_preview_scheme == "https"


@router.websocket(PREFIX + "/{label}")
@router.websocket(PREFIX + "/{label}/{path:path}")
async def proxy_preview_socket(
    websocket: WebSocket, label: str, path: str = ""
) -> None:
    """Hot reload and anything else a preview opens a socket for."""
    preview = await _live(label) if _came_through_host(websocket.headers) else None
    if preview is None or not _authorized(preview, websocket.headers.get("cookie")):
        await websocket.close(code=1008)
        return
    endpoint = await _endpoint(preview)
    if endpoint is None:
        await websocket.close(code=1011)
        return
    target = _target(endpoint.url, path)
    upstream_url = target.copy_with(scheme="wss" if target.scheme == "https" else "ws")
    query = websocket.url.query
    headers = dict(endpoint.headers)
    if cookie := _forwarded_cookie(websocket.headers.get("cookie")):
        headers["cookie"] = cookie
    await websocket.accept(
        subprotocol=websocket.headers.get("sec-websocket-protocol") or None
    )
    try:
        async with await connect_upstream(
            f"{upstream_url}{'?' + query if query else ''}", headers=headers
        ) as upstream:
            await bridge(websocket, upstream, name="workspace.machine_preview")
    except (OSError, asyncio.TimeoutError, _ws_error()) as exc:
        logger.warning(
            "workspace.machine_preview.upstream_socket.degraded",
            error_type=type(exc).__name__,
        )
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1011)
