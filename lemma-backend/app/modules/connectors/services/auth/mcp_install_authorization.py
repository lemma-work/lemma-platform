"""Deciding, once per MCP install, how people will sign in to it.

An MCP server is pointed at by URL, and until somebody types that URL the
deployment has never heard of it -- so there is no client id in the environment
the way there is for every catalogued OAuth connector. The server itself has to
be asked: which authorization server guards you, and may we register a client?

That question is asked once, when the install is created, and the answer is
stored on the install. Registering per person would make one OAuth client per
user of a shared install, which is neither what the protocol intends nor what an
administrator seeing a list of clients would expect.

When the server wants no authorization, or describes none we can follow, this
returns the config untouched and the install stays a paste-a-token one. That is
still the right answer for the many servers that only accept an API key.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.log.log import get_logger
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.services.auth.mcp_oauth import (
    McpAuthorizationUnavailable,
    discover_authorization_server,
    register_client,
)

logger = get_logger(__name__)

# The key an install's discovered authorization lives under. Its presence is
# what makes the install an OAuth one; absence means a static token.
MCP_OAUTH_CONFIG_KEY = "oauth"


async def negotiate_mcp_authorization(
    *,
    kind: str,
    config: dict[str, Any] | None,
    redirect_uri: str,
) -> dict[str, Any] | None:
    """Return the install config, with discovered authorization added if any."""
    if kind != ConnectorKind.MCP.value:
        return config
    server_url = (config or {}).get("server_url")
    if not isinstance(server_url, str) or not server_url:
        return config
    if (config or {}).get(MCP_OAUTH_CONFIG_KEY):
        # Already negotiated, or supplied deliberately. Re-registering on every
        # edit would leave a trail of abandoned clients on the tenant's server.
        return config
    if (config or {}).get("bearer_token"):
        # A token was given, so the person has already chosen how to
        # authenticate and does not need to be sent to a consent screen.
        return config

    # `discover_authorization_server` already answers None for a server that
    # cannot be reached or does not describe one, and lets `UnsafeUrlError`
    # through on purpose -- a metadata document pointing at a private address is
    # the SSRF this guard exists for, and swallowing it here would follow it.
    server = await discover_authorization_server(server_url)
    if server is None:
        return config

    try:
        client_id, client_secret = await register_client(
            server, redirect_uri=redirect_uri
        )
    except (McpAuthorizationUnavailable, httpx.HTTPError, OSError, ValueError) as exc:
        # Never fatal. An install that could not register is still usable with a
        # token, and failing the create would deny the tenant the path that does
        # work.
        logger.info(
            "connectors.mcp_oauth.registration_skipped",
            error_type=type(exc).__name__,
        )
        return config

    logger.info(
        "connectors.mcp_oauth.registered",
        issuer=server.issuer,
    )
    return {
        **(config or {}),
        MCP_OAUTH_CONFIG_KEY: {
            "issuer": server.issuer,
            "authorization_endpoint": server.authorization_endpoint,
            "token_endpoint": server.token_endpoint,
            "resource": server.resource,
            "scopes": list(server.scopes),
            "client_id": client_id,
            "client_secret": client_secret,
        },
    }
