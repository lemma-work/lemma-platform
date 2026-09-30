"""Lemma's OAuth 2.1 authorization server for outside MCP clients.

The MCP SDK ships the HTTP half -- request parsing, PKCE verification, redirect
checks, error formats -- as handlers over a provider protocol. This is the
provider: where clients, codes and tokens come from and go to.

Why an authorization server of our own rather than one we already have:
SuperTokens' OAuth2 provider recipe exists only on its managed service, which a
self-hosted Lemma cannot assume, and it registers no clients dynamically and
reads no client metadata documents -- the two ways MCP clients arrive. fastmcp's
OAuth proxy needs an upstream authorization server to proxy, and SuperTokens is
not one. What remains is small: the person signs in to Lemma exactly as they
always do, and this issues tokens for the one pod they agreed to.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.mcp_access.domain.entities import (
    ALL_SCOPES,
    TokenKind,
    parse_scopes,
)
from app.modules.mcp_access.domain.resources import pod_id_for_resource
from app.modules.mcp_access.domain.tokens import (
    ACCESS_TOKEN_TTL,
    REFRESH_TOKEN_TTL,
    digest,
    looks_like,
    mint,
)
from app.modules.mcp_access.infrastructure.ephemeral import (
    EphemeralStore,
    PendingAuthorization,
)
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository
from app.modules.mcp_access.services.clients import ClientDirectory, LemmaOAuthClient


logger = get_logger(__name__)


class LemmaAuthorizationCode(AuthorizationCode):
    grant_id: str


class LemmaRefreshToken(RefreshToken):
    grant_id: str
    token_id: str
    rotated_at: datetime | None = None
    """When it was already exchanged, if it was. What that means -- a retry or
    a replay -- is decided when it is exchanged again."""


REFRESH_RETRY_GRACE = timedelta(seconds=60)
"""How long after rotation the old refresh token still counts as the same
client retrying, once. OAuth 2.1 §4.3.1 ends the grant on reuse; a lost
response followed by a retry is not theft, and ending the connection for it
sends the person back through consent for a network blip."""


_PKCE_S256_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
"""BASE64URL(SHA-256(verifier)) without padding, which is always 43 characters.
Checked because the challenge is held in Redis until the person answers, and
the SDK takes any string."""

MAX_STATE_LENGTH = 2048
"""`state` is opaque to the server and held with the pending request; clients
use a nonce, or a signed blob of a few hundred bytes."""


def consent_page_url(auth_frontend_url: str, request_id: str) -> str:
    """Where the person answers. On the auth portal, beside `lemma auth login`'s
    page, because both need a signed-in browser and nothing else."""
    return f"{auth_frontend_url.rstrip('/')}/auth/authorize?" + urlencode(
        {"request": request_id}
    )


class LemmaAuthorizationServer(
    OAuthAuthorizationServerProvider[
        LemmaAuthorizationCode, LemmaRefreshToken, AccessToken
    ]
):
    """The provider behind ``/oauth/*``."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clients: ClientDirectory,
        ephemeral: EphemeralStore,
        api_url: str,
        auth_frontend_url: str,
        account_may_sign_in: Callable[[UUID], Awaitable[bool]],
        pod_is_live: Callable[[UUID], Awaitable[bool]],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._uow_factory = uow_factory
        self._clients = clients
        self._ephemeral = ephemeral
        self._api_url = api_url
        self._auth_frontend_url = auth_frontend_url
        self._account_may_sign_in = account_may_sign_in
        self._pod_is_live = pod_is_live
        self._now = clock

    # --- clients -----------------------------------------------------------

    async def get_client(self, client_id: str) -> LemmaOAuthClient | None:
        return await self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._clients.register(client_info)

    # --- authorization -----------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Hold the request and send the browser to the consent page.

        The resource indicator is required. It is the only thing that says which
        pod the client wants, and a token must never be issued for "whichever
        pod" -- the MCP spec makes clients send it for exactly this reason.
        """
        if not _PKCE_S256_CHALLENGE.fullmatch(params.code_challenge or ""):
            raise AuthorizeError(
                error="invalid_request",
                error_description="code_challenge must be an S256 challenge",
            )
        if params.state is not None and len(params.state) > MAX_STATE_LENGTH:
            raise AuthorizeError(
                error="invalid_request",
                error_description=f"state is longer than {MAX_STATE_LENGTH}",
            )
        pod_id = pod_id_for_resource(self._api_url, params.resource)
        if pod_id is None:
            raise AuthorizeError(
                error="invalid_target",
                error_description=(
                    "resource must be a Lemma pod MCP URL on this server, "
                    "such as <api>/mcp/<pod id>"
                ),
            )
        scopes = params.scopes or [scope.value for scope in ALL_SCOPES]
        request_id = await self._ephemeral.hold_pending(
            PendingAuthorization(
                client_id=client.client_id or "",
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                code_challenge=params.code_challenge,
                scopes=scopes,
                state=params.state,
                resource=params.resource or "",
                pod_id=str(pod_id),
            )
        )
        return consent_page_url(self._auth_frontend_url, request_id)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> LemmaAuthorizationCode | None:
        issued = await self._ephemeral.read_code(authorization_code)
        if issued is None or issued.client_id != client.client_id:
            return None
        return LemmaAuthorizationCode(
            code=authorization_code,
            scopes=issued.scopes,
            expires_at=issued.expires_at,
            client_id=issued.client_id,
            code_challenge=issued.code_challenge,
            redirect_uri=issued.redirect_uri,  # type: ignore[arg-type]  # pydantic coerces str to AnyUrl
            redirect_uri_provided_explicitly=issued.redirect_uri_provided_explicitly,
            resource=issued.resource,
            grant_id=issued.grant_id,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: LemmaAuthorizationCode,
    ) -> OAuthToken:
        # Taken, not read: the SDK loaded the code to verify PKCE, and two
        # concurrent redemptions both pass that. Only one GETDEL succeeds.
        if await self._ephemeral.take_code(authorization_code.code) is None:
            raise TokenError(
                error="invalid_grant",
                error_description="authorization code already used",
            )
        async with self._uow_factory() as uow:
            tokens = await self._write_pair(
                McpAccessRepository(uow),
                grant_id=UUID(authorization_code.grant_id),
                scopes=authorization_code.scopes,
            )
            await uow.commit()
        return tokens

    # --- refresh -----------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> LemmaRefreshToken | None:
        """The token, if it is this client's and its grant still stands.

        Only reads. Both the token endpoint and the revocation endpoint load a
        refresh token through here, and a client revoking with a token it has
        already rotated is disconnecting, not a thief replaying it -- so the
        answer to a replay is given where the token is exchanged.
        """
        if not looks_like(refresh_token, TokenKind.REFRESH):
            return None
        now = self._now()
        async with self._uow_factory() as uow:
            found = await McpAccessRepository(uow).find_token(digest(refresh_token))
        if (
            found is None
            or found.kind is not TokenKind.REFRESH
            or found.client_id != client.client_id
            or found.grant_revoked
            or found.expires_at <= now
        ):
            return None
        # The same standing a tool call needs. Without it a deactivated account
        # or a deleted pod would go on refreshing to the grant's ceiling, even
        # though every token it got was refused at the endpoint.
        if not await self._account_may_sign_in(found.user_id):
            return None
        if not await self._pod_is_live(found.pod_id):
            return None
        return LemmaRefreshToken(
            token=refresh_token,
            client_id=found.client_id,
            scopes=sorted(
                scope.value
                for scope in parse_scopes(found.scopes)
                & parse_scopes(found.grant_scopes)
            ),
            expires_at=int(found.expires_at.timestamp()),
            grant_id=str(found.grant_id),
            token_id=str(found.token_id),
            rotated_at=found.rotated_at,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: LemmaRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """A new pair for a refresh token, in one transaction with spending it.

        Split in two, a failure in between left the old token spent and no new
        one issued -- and the client's retry with the only token it has read as
        a replay, which ends the whole grant.

        A token already exchanged is one of two things. Within
        `REFRESH_RETRY_GRACE`, once, it is the client retrying a response it
        lost: it cannot be answered with the pair the first exchange issued,
        because only digests are kept, so that pair -- and any other the grant
        holds -- is cancelled before a new one is written. Any other time it is
        a second party holding a copy, and the grant ends for both (OAuth 2.1
        §4.3.1).
        """
        now = self._now()
        grant_id = UUID(refresh_token.grant_id)
        token_id = UUID(refresh_token.token_id)
        rotated_at = refresh_token.rotated_at
        if rotated_at is not None and not (
            now - rotated_at <= REFRESH_RETRY_GRACE
            and await self._ephemeral.claim_once(
                f"refresh-retry:{token_id}",
                int(REFRESH_RETRY_GRACE.total_seconds()),
            )
        ):
            await self._end_replayed_grant(grant_id, client_id=refresh_token.client_id)
            raise TokenError(
                error="invalid_grant", error_description="refresh token already used"
            )
        async with self._uow_factory() as uow:
            repository = McpAccessRepository(uow)
            if rotated_at is not None:
                await repository.supersede_live_tokens(
                    grant_id=grant_id, rotated_at=rotated_at, now=now
                )
                logger.info(
                    "mcp_access.refresh_token.retried",
                    grant_id=str(grant_id),
                    client_id=refresh_token.client_id,
                )
            elif await repository.mark_rotated(token_id=token_id, now=now):
                await repository.prune_grant_tokens(
                    grant_id=grant_id, keep_rotated=token_id, now=now
                )
            else:
                # Rotated by a concurrent request since it was loaded: the
                # two raced, and exactly one wins.
                raise TokenError(
                    error="invalid_grant",
                    error_description="refresh token already used",
                )
            tokens = await self._write_pair(
                repository, grant_id=grant_id, scopes=scopes
            )
            await uow.commit()
        return tokens

    async def _end_replayed_grant(self, grant_id: UUID, *, client_id: str) -> None:
        async with self._uow_factory() as uow:
            await McpAccessRepository(uow).revoke_grant(
                grant_id=grant_id, now=self._now()
            )
            await uow.commit()
        logger.warning(
            "mcp_access.refresh_token.replayed",
            grant_id=str(grant_id),
            client_id=client_id,
        )

    # --- access tokens and revocation --------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        """For the revocation endpoint. Serving MCP requests goes through
        `TokenVerifier`, which also checks the pod and the account."""
        if not looks_like(token, TokenKind.ACCESS):
            return None
        async with self._uow_factory() as uow:
            found = await McpAccessRepository(uow).find_token(digest(token))
        if found is None or found.grant_revoked or found.expires_at <= self._now():
            return None
        return AccessToken(
            token=token,
            client_id=found.client_id,
            scopes=found.scopes,
            expires_at=int(found.expires_at.timestamp()),
            resource=found.resource,
            subject=str(found.user_id),
            claims={"grant_id": str(found.grant_id)},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """A client disconnecting. Ends the grant, not just the one token: the
        SDK asks for both halves of the pair, and the grant is the pair's only
        owner."""
        if isinstance(token, LemmaRefreshToken):
            grant_id = UUID(token.grant_id)
        elif token.claims and isinstance(token.claims.get("grant_id"), str):
            grant_id = UUID(str(token.claims["grant_id"]))
        else:
            return
        async with self._uow_factory() as uow:
            revoked = await McpAccessRepository(uow).revoke_grant(
                grant_id=grant_id, now=self._now()
            )
            await uow.commit()
        if revoked:
            logger.info(
                "mcp_access.grant.revoked_by_client",
                grant_id=str(grant_id),
                client_id=token.client_id,
            )

    async def _write_pair(
        self,
        repository: McpAccessRepository,
        *,
        grant_id: UUID,
        scopes: list[str],
    ) -> OAuthToken:
        """Add a new access and refresh token to the caller's transaction.

        Scopes are what was asked for *and* what the grant holds, so no token
        -- a refresh's included -- ever carries more than the person allowed.
        """
        allowed = await repository.live_grant_scopes(grant_id)
        if allowed is None:
            # Revoked between consent and redemption, or between refreshes.
            raise TokenError(
                error="invalid_grant", error_description="access was revoked"
            )
        granted = sorted(
            scope.value for scope in parse_scopes(scopes) & parse_scopes(allowed)
        )
        if not granted:
            raise TokenError(
                error="invalid_scope",
                error_description="nothing asked for is something the grant holds",
            )
        now = self._now()
        access = mint(TokenKind.ACCESS)
        refresh = mint(TokenKind.REFRESH)
        for token, kind, ttl in (
            (access, TokenKind.ACCESS, ACCESS_TOKEN_TTL),
            (refresh, TokenKind.REFRESH, REFRESH_TOKEN_TTL),
        ):
            await repository.add_token(
                token_hash=digest(token),
                grant_id=grant_id,
                kind=kind,
                scopes=granted,
                expires_at=now + ttl,
            )
        return OAuthToken(
            access_token=access,
            expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
            scope=" ".join(granted),
            refresh_token=refresh,
        )
