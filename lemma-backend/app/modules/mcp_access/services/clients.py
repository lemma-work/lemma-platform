"""Who is asking: resolving a ``client_id`` to what it may redirect to.

Two ways in, in the order the MCP authorization spec prefers them:

* **Client ID metadata document** -- the ``client_id`` is an HTTPS URL, and the
  document it serves names the client and its redirect URIs. Claude and ChatGPT
  both use this when the server advertises it. Nothing is issued; the document
  is fetched (SSRF-guarded, cached by its own HTTP headers). The one verified
  fact about such a client is the host serving its document.
* **Dynamic registration** (RFC 7591) -- the client POSTs its metadata and is
  issued an id, and a secret unless it asked for none. The spec now calls this
  a fallback, and clients that predate metadata documents still depend on it.
  Nothing about such a client is verified.

Neither is written to Postgres until a person consents (`persist`): both paths
are reachable without signing in, and a row per request would let anyone grow
the table. Either way the client is only ever trusted for where it may send the
person back to, and every one of those is checked by `redirect_allowed`.
"""

from __future__ import annotations

import json
import time
from base64 import urlsafe_b64decode
from collections.abc import Callable
from urllib.parse import urlsplit
from datetime import datetime, timezone

from fastmcp.server.auth.cimd import (
    CIMDAssertionValidator,
    CIMDDocument,
    CIMDFetcher,
    CIMDFetchError,
    CIMDValidationError,
)
from fastmcp.server.auth.redirect_validation import matches_allowed_pattern
from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import (
    InvalidRedirectUriError,
    InvalidScopeError,
    OAuthClientInformationFull,
)
from pydantic import AnyUrl, model_validator

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.mcp_access.domain.entities import ALL_SCOPES, ClientRegistration
from app.modules.mcp_access.domain.redirects import redirect_allowed
from app.modules.mcp_access.domain.tokens import digest
from app.modules.mcp_access.infrastructure.ephemeral import (
    EphemeralStore,
    RegisteredClient,
)
from app.modules.mcp_access.infrastructure.repositories import (
    McpAccessRepository,
    StoredClient,
)

logger = get_logger(__name__)

OFFLINE_ACCESS = "offline_access"
"""Accepted and ignored. Some clients add it to every request when an
authorization server lists it; this one issues refresh tokens regardless and
does not list it, as the MCP spec asks."""

_SECRET_FIELDS = ("client_secret", "client_secret_expires_at")


class LemmaOAuthClient(OAuthClientInformationFull):
    """A resolved client, with the two checks the SDK delegates to it.

    ``redirect_patterns`` holds a metadata document's redirect URIs verbatim.
    They are patterns rather than URLs: Claude Code's document lists
    ``http://localhost/callback`` and then listens on whatever port is free,
    which RFC 8252 §7.3 says a loopback redirect may do.
    """

    registration: ClientRegistration = ClientRegistration.DYNAMIC
    client_secret_hash: str | None = None
    redirect_patterns: list[str] = []

    @model_validator(mode="after")
    def _always_refreshable(self) -> "LemmaOAuthClient":
        """Every code grant here comes with a refresh token, and a metadata
        document that omits ``grant_types`` defaults to the code grant alone
        (RFC 7591 §2). Refusing the refresh that follows would send the person
        back through consent every hour for a client that never said no."""
        if (
            "authorization_code" in self.grant_types
            and "refresh_token" not in self.grant_types
        ):
            self.grant_types = [*self.grant_types, "refresh_token"]
        return self

    @property
    def verified_host(self) -> str | None:
        """The host serving a metadata document -- the only thing about a
        client that is checked rather than claimed. ``None`` for a dynamically
        registered client, about which nothing is."""
        if self.registration is not ClientRegistration.METADATA_DOCUMENT:
            return None
        return urlsplit(self.client_id or "").hostname

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        """Registered *and* safe, on every path. A registered URI is only as
        good as whoever registered it, and anyone can host a document."""
        allowed = [str(uri) for uri in self.redirect_uris or []] + list(
            self.redirect_patterns
        )
        if redirect_uri is None:
            if (
                len(allowed) == 1
                and "*" not in allowed[0]
                and redirect_allowed(allowed[0])
            ):
                return AnyUrl(allowed[0])
            raise InvalidRedirectUriError(
                "redirect_uri must be specified unless the client has exactly "
                "one registered URI"
            )
        candidate = str(redirect_uri)
        if redirect_allowed(candidate) and any(
            candidate == pattern or matches_allowed_pattern(candidate, pattern)
            for pattern in allowed
        ):
            return redirect_uri
        raise InvalidRedirectUriError(
            f"Redirect URI '{candidate}' not registered for client"
        )

    def validate_scope(self, requested_scope: str | None) -> list[str] | None:
        """Checked against what this server offers, not what the client
        registered: a scope is the person's to grant on the consent screen, and
        a client that registered none must still be able to ask."""
        if requested_scope is None:
            return None
        known = {scope.value for scope in ALL_SCOPES}
        requested = [scope for scope in requested_scope.split() if scope]
        unknown = [s for s in requested if s not in known and s != OFFLINE_ACCESS]
        if unknown:
            raise InvalidScopeError(f"Unknown scope: {' '.join(unknown)}")
        return [scope for scope in requested if scope in known]


def _from_stored(stored: StoredClient) -> LemmaOAuthClient:
    registration = ClientRegistration(stored.registration)
    metadata = dict(stored.client_metadata)
    patterns: list[str] = []
    if registration is ClientRegistration.METADATA_DOCUMENT:
        raw = metadata.pop("redirect_uris", None)
        patterns = [str(uri) for uri in raw] if isinstance(raw, list) else []
    return LemmaOAuthClient.model_validate(
        {
            **metadata,
            "client_id": stored.client_id,
            "registration": registration,
            "client_secret_hash": stored.client_secret_hash,
            "redirect_patterns": patterns,
        }
    )


def _from_document(document: CIMDDocument) -> dict[str, object]:
    return document.model_dump(
        mode="json",
        exclude_none=True,
        include={
            "client_name",
            "client_uri",
            "redirect_uris",
            "grant_types",
            "response_types",
            "token_endpoint_auth_method",
            "jwks_uri",
            "software_id",
            "software_version",
        },
    )


def _unverified_claims(assertion: str) -> dict[str, object]:
    """The payload of an assertion whose signature has already been checked."""
    payload = assertion.split(".")[1]
    decoded = json.loads(urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return decoded if isinstance(decoded, dict) else {}


class ClientDirectory:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        ephemeral: EphemeralStore,
        fetcher: CIMDFetcher | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._uow_factory = uow_factory
        self._ephemeral = ephemeral
        self._fetcher = fetcher or CIMDFetcher()
        self._assertions = CIMDAssertionValidator()
        self._now = clock

    def is_metadata_document_id(self, client_id: str) -> bool:
        return self._fetcher.is_cimd_client_id(client_id)

    async def get(self, client_id: str) -> LemmaOAuthClient | None:
        if self.is_metadata_document_id(client_id):
            return await self._resolve_document(client_id)
        async with self._uow_factory() as uow:
            stored = await McpAccessRepository(uow).get_client(client_id)
        if stored is not None:
            return _from_stored(stored)
        pending = await self._ephemeral.read_client(client_id)
        if pending is None:
            return None
        return _from_stored(
            StoredClient(
                client_id=pending.client_id,
                registration=ClientRegistration.DYNAMIC.value,
                client_metadata=pending.client_metadata,
                client_secret_hash=pending.client_secret_hash,
                updated_at=self._now(),
            )
        )

    async def register(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not redirect_allowed(str(uri)):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description=f"Redirect URI not allowed: {uri}",
                )
        metadata = client_info.model_dump(
            mode="json", exclude_none=True, exclude={"client_id", *_SECRET_FIELDS}
        )
        secret = client_info.client_secret
        await self._ephemeral.hold_client(
            RegisteredClient(
                client_id=client_info.client_id or "",
                client_metadata=metadata,
                client_secret_hash=digest(secret) if secret else None,
            )
        )
        logger.info(
            "mcp_access.client.registered",
            client_name=str(metadata.get("client_name") or "")[:120],
        )

    async def persist(self, client: LemmaOAuthClient) -> None:
        """Write the client down, because a person has just allowed it.

        A grant points at this row, and the connected-apps list reads the
        client's name from it. A metadata document's copy is refreshed each
        time a person consents.
        """
        metadata = client.model_dump(
            mode="json",
            exclude_none=True,
            include={
                "client_name",
                "client_uri",
                "redirect_uris",
                "grant_types",
                "response_types",
                "scope",
                "token_endpoint_auth_method",
                "software_id",
                "software_version",
            },
        )
        if client.registration is ClientRegistration.METADATA_DOCUMENT:
            metadata["redirect_uris"] = list(client.redirect_patterns)
        async with self._uow_factory() as uow:
            await McpAccessRepository(uow).save_client(
                client_id=client.client_id or "",
                registration=client.registration.value,
                client_metadata=metadata,
                client_secret_hash=client.client_secret_hash,
                now=self._now(),
            )
            await uow.commit()

    async def verify_assertion(
        self, *, client_id: str, assertion: str, audiences: tuple[str, ...]
    ) -> None:
        """Check a ``private_key_jwt`` client assertion (RFC 7523) against the
        keys the client's metadata document publishes. Raises ``ValueError``.

        ChatGPT's document asks for this method. The assertion is accepted with
        the token endpoint or the issuer as its audience: RFC 7523 names the
        token endpoint, and newer guidance names the issuer, and clients follow
        either. Each assertion is used once across every replica: fastmcp's own
        replay check is per process.
        """
        try:
            document = await self._fetcher.fetch(client_id)
        except (CIMDFetchError, CIMDValidationError) as exc:
            raise ValueError(f"client metadata document unusable: {exc}") from exc
        if document.token_endpoint_auth_method != "private_key_jwt":
            raise ValueError("client is not registered for private_key_jwt")
        refusal: ValueError | None = None
        for audience in audiences:
            try:
                await self._assertions.validate_assertion(
                    assertion, client_id, audience, document
                )
                break
            except ValueError as exc:
                refusal = exc
        else:
            raise refusal or ValueError("no audience to check the assertion against")
        claims = _unverified_claims(assertion)
        jti, expires = claims.get("jti"), claims.get("exp")
        if not isinstance(jti, str) or not isinstance(expires, (int, float)):
            raise ValueError("assertion needs jti and exp")
        if not await self._ephemeral.claim_once(
            f"jti:{digest(client_id)}:{digest(jti)}", int(expires - time.time()) + 60
        ):
            logger.warning("mcp_access.client_assertion.replayed")
            raise ValueError("assertion already used")

    async def _resolve_document(self, client_id: str) -> LemmaOAuthClient | None:
        try:
            document = await self._fetcher.fetch(client_id)
        except CIMDFetchError, CIMDValidationError:
            logger.warning(
                "mcp_access.client_document.unusable",
                client_id=client_id,
                exc_info=True,
            )
            return None
        metadata = _from_document(document)
        safe = [
            str(uri)
            for uri in metadata.get("redirect_uris") or []  # type: ignore[union-attr]
            if redirect_allowed(str(uri))
        ]
        if not safe:
            logger.warning(
                "mcp_access.client_document.no_safe_redirect", client_id=client_id
            )
            return None
        metadata["redirect_uris"] = safe
        return _from_stored(
            StoredClient(
                client_id=client_id,
                registration=ClientRegistration.METADATA_DOCUMENT.value,
                client_metadata=metadata,
                client_secret_hash=None,
                updated_at=self._now(),
            )
        )
