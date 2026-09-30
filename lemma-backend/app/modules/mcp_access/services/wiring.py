"""One instance of each service, built on first use.

Lazily, because the pieces hold a Redis client and a metadata-document cache,
and building them at import would open a connection pool in every process that
merely imports the contracts -- including the worker, which serves none of this.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.security import account_may_sign_in
from app.modules.pod.contracts.liveness import pod_is_live
from app.modules.mcp_access.infrastructure.ephemeral import EphemeralStore
from app.modules.mcp_access.infrastructure.rate_limit import RateLimiter
from app.modules.mcp_access.services.authorization_server import (
    LemmaAuthorizationServer,
)
from app.modules.mcp_access.services.clients import ClientDirectory
from app.modules.mcp_access.services.consent import ConsentService
from app.modules.mcp_access.services.verification import AccessTokenVerifier


def issuer() -> str:
    """The authorization server's identifier: the API's own URL.

    RFC 8414 §3.3 has clients compare it byte for byte with what they built the
    discovery URL from, so it is normalized once, here, and every place that
    prints it -- metadata, the resource's ``authorization_servers``, the ``iss``
    on a redirect -- reads it from here.
    """
    parts = urlsplit(settings.api_url.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


@lru_cache(maxsize=1)
def _uow_factory() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def _pod_is_live(pod_id: UUID) -> bool:
    return await pod_is_live(_uow_factory(), pod_id)


@lru_cache(maxsize=1)
def client_directory() -> ClientDirectory:
    return ClientDirectory(_uow_factory(), ephemeral=_ephemeral())


@lru_cache(maxsize=1)
def _ephemeral() -> EphemeralStore:
    return EphemeralStore()


@lru_cache(maxsize=1)
def authorization_server() -> LemmaAuthorizationServer:
    return LemmaAuthorizationServer(
        uow_factory=_uow_factory(),
        clients=client_directory(),
        ephemeral=_ephemeral(),
        api_url=issuer(),
        auth_frontend_url=settings.auth_frontend_url,
        account_may_sign_in=account_may_sign_in,
        pod_is_live=_pod_is_live,
    )


@lru_cache(maxsize=1)
def consent_service() -> ConsentService:
    return ConsentService(
        uow_factory=_uow_factory(),
        clients=client_directory(),
        ephemeral=_ephemeral(),
        issuer=issuer(),
    )


@lru_cache(maxsize=1)
def access_token_verifier() -> AccessTokenVerifier:
    return AccessTokenVerifier(
        uow_factory=_uow_factory(),
        api_url=issuer(),
        account_may_sign_in=account_may_sign_in,
        pod_is_live=_pod_is_live,
    )


@lru_cache(maxsize=1)
def rate_limiter() -> RateLimiter:
    return RateLimiter()
