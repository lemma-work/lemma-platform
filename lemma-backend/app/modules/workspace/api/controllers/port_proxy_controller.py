"""Reverse proxy for signed access to a port inside a sandbox.

Authorisation is the token in the path and nothing else, which is why this
router carries no user dependency: the URL is handed to a browser that has no
Lemma session, and the signature already names the exact sandbox, port, and
expiry it authorises.

Because the token is the whole credential, inbound headers that could be
mistaken for a *different* credential are dropped rather than forwarded -- a
sandbox must never see the caller's Lemma cookies or API key.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.modules.workspace.providers.base import ProviderGone, ProviderInstance
from app.modules.workspace.services.port_access import (
    PortAccessInvalid,
    PortAccessSigner,
)

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
    {"content-length", "connection", "keep-alive", "transfer-encoding", "upgrade"}
)


@router.api_route(
    "/{token}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def proxy_sandbox_port(token: str, path: str, request: Request) -> Response:
    key = settings.workspace_runtime_credential_key
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

    upstream = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(60.0))
    try:
        proxied = await upstream.request(
            request.method,
            f"/{path}",
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

    return StreamingResponse(
        body(),
        status_code=proxied.status_code,
        headers={
            name: value
            for name, value in proxied.headers.items()
            if name.lower() not in _STRIPPED_RESPONSE_HEADERS
        },
    )
