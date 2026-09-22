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

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import asyncio
import contextlib

import httpx
from fastapi import APIRouter, Request, Response, WebSocket, status
from fastapi.responses import StreamingResponse

from app.core.log.log import get_logger

from app.core.config import settings
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderGone,
    ProviderInstance,
    ProviderRejected,
    SandboxEndpoint,
    require_capability,
)
from sandbox_runtime.errors import SandboxCapabilityUnsupported
from app.modules.workspace.services.ws_bridge import bridge, connect_upstream
from app.modules.workspace.services.port_access import (
    PortAccessInvalid,
    PortAccessSigner,
)

logger = get_logger(__name__)


def _ws_error() -> type[Exception]:
    """The socket library's failure type, named where it is caught.

    At module scope it would sit in the import graph of every process that
    registers these routes, for the sake of an `except` clause.
    """
    import websockets

    return websockets.exceptions.WebSocketException


def _ws_closed() -> type[Exception]:
    """Likewise, for the ordinary close."""
    import websockets

    return websockets.exceptions.ConnectionClosed


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


def _upstream_headers(
    inbound: "Mapping[str, str]", fabric: "Mapping[str, str]"
) -> dict[str, str]:
    """What the sandbox is sent: the caller's headers, then the fabric's own.

    The fabric's go last and therefore win. On E2B they are the per-sandbox
    traffic token, without which a closed sandbox answers 403 -- and a caller
    holding a signed link must not be able to displace the sandbox's doorkeeper
    by sending a header of the same name.

    A named function rather than a dict literal inside the handler because this
    is the rule, and a rule with a name is one a test can hold without standing
    a double in front of the handler's own collaborators.
    """
    return {
        **{
            name: value
            for name, value in inbound.items()
            if name.lower() not in _STRIPPED_REQUEST_HEADERS
        },
        **fabric,
    }


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


async def _resolve_target(token: str) -> SandboxEndpoint | None:
    """Where a signed grant points, or None when it does not hold.

    Returns the endpoint rather than a bare URL because a fabric's door may need
    a header -- an E2B traffic token, a preview proxy's own -- and a caller that
    only got a string had nowhere to put it.

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
        require_capability(service._provider, ProviderCapability.PORT_REACH)
        return await service._provider.reach_port(
            ProviderInstance(
                provider_id=handle.provider_id, name=handle.provider_id, running=True
            ),
            port=grant.port,
            deadline_at=deadline_at,
        )
    # `ProviderRejected` is the fabric saying this sandbox does not publish
    # that port. `PortAccessSigner` will sign a grant for any port, and Docker
    # and the desktop guest publish only the ports declared when the sandbox
    # was created -- so a grant naming any other one is a refusal to deliver,
    # not an error to raise. Uncaught it left this handler as an unhandled
    # exception, which the WebSocket half reports as neither a close code nor
    # a refusal.
    except ProviderGone, ProviderRejected, SandboxCapabilityUnsupported:
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
    endpoint = await _resolve_target(token)
    if endpoint is None:
        # Refused before accepting, so a caller without a valid grant never gets
        # an open socket. Expired and forged are indistinguishable, as on the
        # request half.
        await websocket.close(code=1008)
        return

    upstream_url = (
        httpx.URL(endpoint.url)
        .copy_with(path="/" + quote(path.lstrip("/"), safe="/"))
        .copy_with(scheme="wss" if httpx.URL(endpoint.url).scheme == "https" else "ws")
    )
    query = websocket.url.query
    upstream_target = f"{upstream_url}{'?' + query if query else ''}"

    await websocket.accept(
        subprotocol=websocket.headers.get("sec-websocket-protocol") or None
    )
    try:
        # Bounded frames, a keepalive, and whatever the fabric's door needs --
        # all decided once in `ws_bridge` so this path and the browser view
        # cannot drift. `max_size=None` here previously meant one frame from a
        # process the agent controls was buffered whole in the API's memory.
        async with await connect_upstream(
            upstream_target, headers=endpoint.headers
        ) as upstream:
            await bridge(websocket, upstream, name="workspace.port_proxy")
    except OSError, _ws_error(), asyncio.TimeoutError:
        logger.warning(
            "workspace.port_proxy.upstream_websocket.degraded", exc_info=True
        )
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1011)


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
        require_capability(service._provider, ProviderCapability.PORT_REACH)
        endpoint = await service._provider.reach_port(
            ProviderInstance(
                provider_id=handle.provider_id, name=handle.provider_id, running=True
            ),
            port=grant.port,
            deadline_at=deadline_at,
        )
        base_url = endpoint.url
    # A port this fabric does not publish is the same answer as a sandbox that
    # is gone: there is nothing at the other end of this grant. See the note on
    # `_resolve_target`, which is the WebSocket half of the same decision.
    except ProviderGone, ProviderRejected:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    except SandboxCapabilityUnsupported:
        return Response(status_code=status.HTTP_409_CONFLICT)

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
            headers=_upstream_headers(request.headers, endpoint.headers),
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
