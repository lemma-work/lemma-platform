"""What the pod MCP endpoint needs from `mcp_access`.

Operations, not storage: check a bearer token for a pod, say how to challenge a
caller that has none, and count a request against its grant. Nothing here can
issue a token.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.mcp_access.config import mcp_access_settings
from app.modules.mcp_access.domain.entities import ALL_SCOPES, McpPrincipal, Scope
from app.modules.mcp_access.domain.resources import (
    MCP_MOUNT_PATH,
    protected_resource_metadata_url,
)
from app.modules.mcp_access.domain.tokens import looks_like
from app.modules.mcp_access.domain.entities import TokenKind
from app.modules.mcp_access.services.wiring import (
    access_token_verifier,
    issuer,
    rate_limiter,
)

__all__ = [
    "MCP_MOUNT_PATH",
    "McpPrincipal",
    "Scope",
    "bearer_challenge",
    "is_mcp_access_token",
    "mcp_access_enabled",
    "request_retry_after",
    "verify_mcp_access_token",
]


def mcp_access_enabled() -> bool:
    return mcp_access_settings.mcp_access_enabled


def is_mcp_access_token(token: str) -> bool:
    return looks_like(token, TokenKind.ACCESS)


async def verify_mcp_access_token(token: str, *, pod_id: UUID) -> McpPrincipal | None:
    return await access_token_verifier().verify(token, pod_id=pod_id)


def bearer_challenge(
    pod_id: UUID,
    *,
    error: str | None = None,
    scopes: tuple[Scope, ...] = ALL_SCOPES,
) -> str:
    """A ``WWW-Authenticate`` value that sends a client to sign in.

    ``resource_metadata`` is what makes Claude start its sign-in at all: it
    begins OAuth only on a 401 that names the metadata document. ``scope`` is
    what the client asks for, so it names everything the pod's tools need
    (MCP authorization, "Scope Selection Strategy").
    """
    parts = [
        f'resource_metadata="{protected_resource_metadata_url(issuer(), pod_id)}"',
        f'scope="{" ".join(scope.value for scope in scopes)}"',
    ]
    if error:
        parts.insert(0, f'error="{error}"')
    return "Bearer " + ", ".join(parts)


async def request_retry_after(principal: McpPrincipal) -> int | None:
    """Seconds to wait if this grant is over its request budget."""
    return await rate_limiter().retry_after(
        f"grant:{principal.grant_id}",
        limit=mcp_access_settings.mcp_access_requests_per_minute,
        window_seconds=60,
    )
