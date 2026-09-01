"""Finding out how an MCP server wants to be authorized, and registering with it.

An MCP server that needs authorization answers an unauthenticated request with
``401`` and a ``WWW-Authenticate`` header naming its protected-resource
metadata. From there the client reads which authorization server guards it, asks
that server for its endpoints, and -- because there is no client to configure in
advance for a server nobody knew about until a tenant typed its URL -- registers
one dynamically.

That is the whole reason this exists. Every other OAuth connector in the catalog
has a client id sitting in the environment, put there by whoever added the
connector. A tenant-configured MCP server has none: the deployment has never
heard of it. Dynamic registration is what makes "paste a URL and sign in"
possible at all, and it is what the MCP specification settled on, so most
authorizing servers now speak it.

Nothing here is MCP-specific except where the probe starts. It is RFC 9728
(protected resource metadata), RFC 8414 (authorization server metadata) and
RFC 7591 (dynamic client registration), which is why a server like Phoenix that
implements the MCP profile is reachable by a client that only implements the
RFCs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.log.log import get_logger
from app.core.net.url_guard import UnsafeUrlError, assert_safe_url

logger = get_logger(__name__)

_DISCOVERY_TIMEOUT_SECONDS = 15.0
# `Bearer realm="…", resource_metadata="https://…"`
_RESOURCE_METADATA_RE = re.compile(r'resource_metadata\s*=\s*"([^"]+)"', re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class McpAuthorizationServer:
    """Where to send someone, and who to say we are when they come back."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    # RFC 8707: which resource the token is for. An authorization server
    # guarding several MCP servers issues a token scoped to one of them, and
    # omitting this gets a token the resource will refuse.
    resource: str
    scopes: tuple[str, ...] = ()


class McpAuthorizationUnavailable(Exception):
    """The server does not describe an authorization server we can use."""


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    await assert_safe_url(url)
    response = await client.get(url, headers={"Accept": "application/json"})
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _resource_metadata_urls(server_url: str, challenge: str | None) -> list[str]:
    """Where the protected-resource document might be, best first.

    The header is authoritative when present. The well-known paths are the
    spec's fallback, and the path-suffixed form matters for a host serving
    several resources -- Phoenix answers on
    ``/.well-known/oauth-protected-resource/mcp``, not the bare path.
    """
    urls: list[str] = []
    if challenge:
        found = _RESOURCE_METADATA_RE.search(challenge)
        if found:
            urls.append(found.group(1))
    parts = urlsplit(server_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    if path:
        urls.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    urls.append(f"{origin}/.well-known/oauth-protected-resource")
    return list(dict.fromkeys(urls))


def _authorization_metadata_urls(issuer: str) -> list[str]:
    """RFC 8414 inserts the well-known segment before the issuer's path; OpenID
    appends it. Servers in the wild do both, so try both."""
    parts = urlsplit(issuer)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    return list(
        dict.fromkeys(
            [
                f"{origin}/.well-known/oauth-authorization-server{path}",
                f"{origin}/.well-known/openid-configuration{path}",
                urljoin(issuer.rstrip("/") + "/", ".well-known/openid-configuration"),
            ]
        )
    )


async def discover_authorization_server(
    server_url: str, *, client: httpx.AsyncClient | None = None
) -> McpAuthorizationServer | None:
    """Ask an MCP server how it wants to be authorized.

    Returns ``None`` when the server needs no authorization, or describes none
    we can follow -- a static bearer token is still a valid way to reach it, so
    that is not an error.
    """
    await assert_safe_url(server_url)
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=_DISCOVERY_TIMEOUT_SECONDS, follow_redirects=True
    )
    try:
        challenge = await _challenge_for(client, server_url)
        for url in _resource_metadata_urls(server_url, challenge):
            metadata = await _get_json(client, url)
            if not metadata:
                continue
            issuers = metadata.get("authorization_servers") or []
            if not issuers:
                continue
            return await _authorization_server(
                client,
                issuer=str(issuers[0]),
                resource=str(metadata.get("resource") or server_url),
            )
        return None
    except UnsafeUrlError:
        # A server pointing its metadata at a private address is the SSRF this
        # guard exists for, and following it is the whole attack.
        raise
    except httpx.HTTPError, OSError:
        return None
    finally:
        if owns_client:
            await client.aclose()


async def _challenge_for(client: httpx.AsyncClient, server_url: str) -> str | None:
    """The `WWW-Authenticate` an unauthenticated call comes back with."""
    try:
        response = await client.post(
            server_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "lemma", "version": "1"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
    except httpx.HTTPError, OSError:
        return None
    if response.status_code != 401:
        return None
    return response.headers.get("www-authenticate")


async def _authorization_server(
    client: httpx.AsyncClient, *, issuer: str, resource: str
) -> McpAuthorizationServer | None:
    for url in _authorization_metadata_urls(issuer):
        metadata = await _get_json(client, url)
        if not metadata:
            continue
        authorization_endpoint = metadata.get("authorization_endpoint")
        token_endpoint = metadata.get("token_endpoint")
        if not authorization_endpoint or not token_endpoint:
            continue
        return McpAuthorizationServer(
            issuer=str(metadata.get("issuer") or issuer),
            authorization_endpoint=str(authorization_endpoint),
            token_endpoint=str(token_endpoint),
            registration_endpoint=(
                str(metadata["registration_endpoint"])
                if metadata.get("registration_endpoint")
                else None
            ),
            resource=resource,
            scopes=tuple(str(s) for s in (metadata.get("scopes_supported") or ())),
        )
    return None


async def register_client(
    server: McpAuthorizationServer,
    *,
    redirect_uri: str,
    client_name: str = "Lemma",
    client: httpx.AsyncClient | None = None,
) -> tuple[str, str | None]:
    """Register one OAuth client for this install, and return its credentials.

    Once per install, not once per person: the client identifies Lemma to that
    server, and every account connected through the install consents to the
    same one.

    A server may answer with no ``client_secret`` -- that is a public client,
    and PKCE is what makes the exchange safe rather than the secret.
    """
    if not server.registration_endpoint:
        raise McpAuthorizationUnavailable(
            "The server's authorization server does not accept client registration, "
            "so there is no client to sign in with. Connect it with a token instead."
        )
    await assert_safe_url(server.registration_endpoint)
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=_DISCOVERY_TIMEOUT_SECONDS, follow_redirects=True
    )
    try:
        response = await client.post(
            server.registration_endpoint,
            json={
                "client_name": client_name,
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            headers={"Content-Type": "application/json"},
        )
    finally:
        if owns_client:
            await client.aclose()
    if response.status_code not in (200, 201):
        raise McpAuthorizationUnavailable(
            f"Client registration was refused with HTTP {response.status_code}."
        )
    payload = response.json()
    client_id = payload.get("client_id")
    if not client_id:
        raise McpAuthorizationUnavailable("Client registration returned no client_id.")
    return str(client_id), (
        str(payload["client_secret"]) if payload.get("client_secret") else None
    )
