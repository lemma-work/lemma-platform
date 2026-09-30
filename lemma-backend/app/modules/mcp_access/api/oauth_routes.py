"""The public OAuth surface: discovery documents and the four endpoints.

Every route here is unauthenticated by design -- a client arrives with nothing
-- and `app/core/security.py` lists each by path. The handlers are the MCP
SDK's; what is Lemma's is the metadata they are described by, which is written
out in full rather than built by the SDK because three of its fields decide
whether Claude and ChatGPT use a client metadata document or fall back to
registering, and the SDK leaves all three out.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter
from fastmcp.server.auth.auth import TokenHandler
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.settings import ClientRegistrationOptions
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.modules.identity.contracts.client_address import client_ip
from app.modules.mcp_access.config import mcp_access_settings
from app.modules.mcp_access.domain.entities import ALL_SCOPES
from app.modules.mcp_access.domain.resources import (
    MCP_MOUNT_PATH,
    api_path_prefix,
    PROTECTED_RESOURCE_METADATA_PATH,
    pod_resource_url,
)
from app.modules.mcp_access.infrastructure.rate_limit import RateLimiter
from app.modules.mcp_access.services.client_auth import DigestClientAuthenticator
from app.modules.mcp_access.services.wiring import (
    authorization_server,
    client_directory,
    issuer,
    rate_limiter,
)

OAUTH_PREFIX = "/oauth"
AUTHORIZATION_SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server"
OPENID_CONFIGURATION_PATH = "/.well-known/openid-configuration"

_AUTH_METHODS = [
    "none",
    "private_key_jwt",
    "client_secret_post",
    "client_secret_basic",
]
_METADATA_CACHE = {"Cache-Control": "public, max-age=300"}

Handler = Callable[[Request], Awaitable[Response]]


def authorization_server_metadata(issuer_url: str) -> dict[str, object]:
    endpoint = f"{issuer_url}{OAUTH_PREFIX}"
    return {
        "issuer": issuer_url,
        "authorization_endpoint": f"{endpoint}/authorize",
        "token_endpoint": f"{endpoint}/token",
        "registration_endpoint": f"{endpoint}/register",
        "revocation_endpoint": f"{endpoint}/revoke",
        "scopes_supported": [scope.value for scope in ALL_SCOPES],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        # "none" is what makes Claude use a metadata document at all: it
        # requires both this and the flag below before it will.
        "token_endpoint_auth_methods_supported": _AUTH_METHODS,
        "revocation_endpoint_auth_methods_supported": _AUTH_METHODS,
        # For `private_key_jwt`, which ChatGPT's metadata document declares.
        "token_endpoint_auth_signing_alg_values_supported": ["RS256", "ES256"],
        # Spec-following clients refuse a server that omits this (MCP
        # authorization, "Authorization Code Protection").
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        # RFC 9207: every redirect back carries `iss`. ChatGPT picks its
        # issuer-checking redirect URI when this is advertised.
        "authorization_response_iss_parameter_supported": True,
    }


def protected_resource_metadata(issuer_url: str, pod_id: UUID) -> dict[str, object]:
    """RFC 9728 for one pod. No name: the document is public, and a pod's name
    is not something to hand to anyone who can guess its id."""
    return {
        "resource": pod_resource_url(issuer_url, pod_id),
        "authorization_servers": [issuer_url],
        "scopes_supported": [scope.value for scope in ALL_SCOPES],
        "bearer_methods_supported": ["header"],
        "resource_name": "Lemma",
    }


async def _as_metadata(_: Request) -> Response:
    return JSONResponse(
        authorization_server_metadata(issuer()), headers=_METADATA_CACHE
    )


async def _resource_metadata(request: Request) -> Response:
    try:
        pod_id = UUID(str(request.path_params["pod_id"]))
    except ValueError:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(
        protected_resource_metadata(issuer(), pod_id), headers=_METADATA_CACHE
    )


async def _by_address(request: Request) -> str:
    return client_ip(request.scope)


async def _by_client_and_address(request: Request) -> str:
    """Per client as well as per address. Hosted clients call from a handful
    of shared addresses on behalf of all their users, so an address alone
    would make every Claude or ChatGPT user share one budget.

    The form is read here and cached on the request, so the handler reads the
    same parsed body.
    """
    form = await request.form()
    client_id = form.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        header = request.headers.get("Authorization", "")
        client_id = header[:80] if header.startswith("Basic ") else ""
    return f"{client_ip(request.scope)}:{client_id[:512]}"


def _limited(
    handler: Handler,
    *,
    name: str,
    key: Callable[[Request], Awaitable[str]],
    limit: Callable[[], int],
    window: int,
    address_limit: Callable[[], int] | None = None,
    limiter: Callable[[], RateLimiter] = rate_limiter,
) -> Handler:
    """``handler`` behind a rate limit on ``key``, and -- when the key is one
    the caller partly chooses -- a ceiling on the source address as well.

    `_by_client_and_address` takes the ``client_id`` from the request, so a
    caller who varies it gets a fresh bucket every time; and a new URL-shaped
    ``client_id`` costs an outbound fetch of its metadata document. The address
    ceiling is what bounds that. It is checked first, and sized for a hosted
    client's shared addresses rather than for one browser.
    """
    buckets: list[tuple[Callable[[Request], Awaitable[str]], Callable[[], int]]] = []
    if address_limit is not None:
        buckets.append((_by_address_only, address_limit))
    buckets.append((key, limit))

    async def endpoint(request: Request) -> Response:
        for bucket_key, bucket_limit in buckets:
            wait = await limiter().retry_after(
                f"{name}:{await bucket_key(request)}",
                limit=bucket_limit(),
                window_seconds=window,
            )
            if wait is not None:
                return JSONResponse(
                    {"error": "slow_down", "error_description": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": str(wait)},
                )
        return await handler(request)

    return endpoint


async def _by_address_only(request: Request) -> str:
    # Its own namespace, so it never shares a bucket with a `_by_address` key.
    return f"address:{client_ip(request.scope)}"


def oauth_router() -> APIRouter:
    provider = authorization_server()
    authenticator = DigestClientAuthenticator(
        client_directory(),
        assertion_audiences=(f"{issuer()}{OAUTH_PREFIX}/token", issuer()),
    )
    token = TokenHandler(provider=provider, client_authenticator=authenticator)
    registration = RegistrationHandler(
        provider,
        options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[scope.value for scope in ALL_SCOPES],
            default_scopes=[scope.value for scope in ALL_SCOPES],
        ),
    )
    revocation = RevocationHandler(provider, authenticator)
    authorize = AuthorizationHandler(provider)

    router = APIRouter()
    well_known_as = [AUTHORIZATION_SERVER_METADATA_PATH, OPENID_CONFIGURATION_PATH]
    issuer_path = issuer().partition("://")[2].partition("/")[2]
    if issuer_path:
        # RFC 8414 §3.1: an issuer with a path is discovered with the path
        # appended after the well-known segment.
        well_known_as += [f"{path}/{issuer_path}" for path in list(well_known_as)]
    for path in well_known_as:
        router.add_route(path, _as_metadata, methods=["GET"], include_in_schema=False)
    # Where RFC 9728 puts it, prefix included; and the prefix-less form, which
    # is what arrives when a proxy strips the prefix before forwarding.
    for prefix in sorted({api_path_prefix(issuer()), ""}):
        router.add_route(
            f"{PROTECTED_RESOURCE_METADATA_PATH}{prefix}{MCP_MOUNT_PATH}/{{pod_id}}",
            _resource_metadata,
            methods=["GET"],
            include_in_schema=False,
        )
    router.add_route(
        f"{OAUTH_PREFIX}/authorize",
        _limited(
            authorize.handle,
            name="authorize",
            key=_by_address,
            limit=lambda: mcp_access_settings.mcp_access_authorize_requests_per_minute,
            window=60,
        ),
        methods=["GET", "POST"],
        include_in_schema=False,
    )
    router.add_route(
        f"{OAUTH_PREFIX}/token",
        _limited(
            token.handle,
            name="token",
            key=_by_client_and_address,
            limit=lambda: mcp_access_settings.mcp_access_token_requests_per_minute,
            window=60,
            address_limit=lambda: (
                mcp_access_settings.mcp_access_token_requests_per_address_per_minute
            ),
        ),
        methods=["POST"],
        include_in_schema=False,
    )
    router.add_route(
        f"{OAUTH_PREFIX}/register",
        _limited(
            registration.handle,
            name="register",
            key=_by_address,
            limit=lambda: mcp_access_settings.mcp_access_registrations_per_hour,
            window=3600,
        ),
        methods=["POST"],
        include_in_schema=False,
    )
    router.add_route(
        f"{OAUTH_PREFIX}/revoke",
        _limited(
            revocation.handle,
            name="revoke",
            key=_by_client_and_address,
            limit=lambda: mcp_access_settings.mcp_access_token_requests_per_minute,
            window=60,
            address_limit=lambda: (
                mcp_access_settings.mcp_access_token_requests_per_address_per_minute
            ),
        ),
        methods=["POST"],
        include_in_schema=False,
    )
    return router
