"""Who is asking: resolving a ``client_id`` to what it may redirect to.

Two ways in, in the order the MCP authorization spec prefers them:

* **Client ID metadata document** -- the ``client_id`` is an HTTPS URL, and the
  document it serves names the client and its redirect URIs. Claude and ChatGPT
  both use this when the server advertises it. Nothing is issued; the document
  is fetched (SSRF-guarded, cached by its own HTTP headers) and a copy kept so
  a grant has a row to point at and a name to show.
* **Dynamic registration** (RFC 7591) -- the client POSTs its metadata and is
  issued an id, and a secret unless it asked for none. The spec now calls this
  a fallback, and clients that predate metadata documents still depend on it.

Either way the client is only ever trusted for where it may send the person
back to. Its name is shown on the consent screen as the client's own claim.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from fastmcp.server.auth.cimd import (
    CIMDAssertionValidator,
    CIMDDocument,
    CIMDFetcher,
    CIMDFetchError,
    CIMDValidationError,
)
from fastmcp.server.auth.redirect_validation import (
    is_redirect_uri_allowed_for_application_type,
    matches_allowed_pattern,
)
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
from app.modules.mcp_access.domain.tokens import digest
from app.modules.mcp_access.infrastructure.repositories import (
    McpAccessRepository,
    StoredClient,
)

logger = get_logger(__name__)

OFFLINE_ACCESS = "offline_access"
"""Accepted and ignored. Some clients add it to every request when an
authorization server lists it; this one issues refresh tokens regardless and
does not list it, as the MCP spec asks."""

_DOCUMENT_COPY_REFRESH = timedelta(hours=1)
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

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        allowed = [str(uri) for uri in self.redirect_uris or []] + list(
            self.redirect_patterns
        )
        if redirect_uri is None:
            if len(allowed) == 1 and "*" not in allowed[0]:
                return AnyUrl(allowed[0])
            raise InvalidRedirectUriError(
                "redirect_uri must be specified unless the client has exactly "
                "one registered URI"
            )
        candidate = str(redirect_uri)
        if any(
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
            "logo_uri",
            "redirect_uris",
            "grant_types",
            "response_types",
            "token_endpoint_auth_method",
            "jwks_uri",
            "software_id",
            "software_version",
        },
    )


class ClientDirectory:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        fetcher: CIMDFetcher | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._uow_factory = uow_factory
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
        return _from_stored(stored) if stored is not None else None

    async def register(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not is_redirect_uri_allowed_for_application_type(uri, "native"):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description=f"Redirect URI not allowed: {uri}",
                )
        metadata = client_info.model_dump(
            mode="json", exclude_none=True, exclude={"client_id", *_SECRET_FIELDS}
        )
        secret = client_info.client_secret
        async with self._uow_factory() as uow:
            await McpAccessRepository(uow).save_client(
                client_id=client_info.client_id or "",
                registration=ClientRegistration.DYNAMIC.value,
                client_metadata=metadata,
                client_secret_hash=digest(secret) if secret else None,
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
        either.
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
                return
            except ValueError as exc:
                refusal = exc
        raise refusal or ValueError("no audience to check the assertion against")

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
        now = self._now()
        async with self._uow_factory() as uow:
            repository = McpAccessRepository(uow)
            stored = await repository.get_client(client_id)
            if (
                stored is None
                or stored.client_metadata != metadata
                or now - stored.updated_at > _DOCUMENT_COPY_REFRESH
            ):
                await repository.save_client(
                    client_id=client_id,
                    registration=ClientRegistration.METADATA_DOCUMENT.value,
                    client_metadata=metadata,
                    client_secret_hash=None,
                    now=now,
                )
                await uow.commit()
        return _from_stored(
            StoredClient(
                client_id=client_id,
                registration=ClientRegistration.METADATA_DOCUMENT.value,
                client_metadata=metadata,
                client_secret_hash=None,
                updated_at=now,
            )
        )
