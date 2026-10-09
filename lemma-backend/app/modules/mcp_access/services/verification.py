"""Checking an access token on a pod's MCP endpoint.

Every condition is checked on every request, from the database, because each
is something a person can change and expect to take effect at once: revoking
the grant, losing access to the pod, the pod being deleted, the account being
deactivated. The first two are the grant row and the tool layer's own
authorization; the pod is `pod_is_live` and the account `account_may_sign_in`.
A deleted pod needs its own check: deletion is soft and memberships survive it,
so the person's standing in it still reads as it did.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.mcp_access.domain.entities import (
    McpPrincipal,
    TokenKind,
    parse_scopes,
)
from app.modules.mcp_access.domain.resources import pod_resource_url
from app.modules.mcp_access.domain.tokens import digest, looks_like
from app.modules.mcp_access.infrastructure.repositories import (
    LAST_USED_RESOLUTION,
    McpAccessRepository,
)


class AccessTokenVerifier:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        api_url: str,
        account_may_sign_in: Callable[[UUID], Awaitable[bool]],
        pod_is_live: Callable[[UUID], Awaitable[bool]],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._uow_factory = uow_factory
        self._api_url = api_url
        self._account_may_sign_in = account_may_sign_in
        self._pod_is_live = pod_is_live
        self._now = clock

    async def grant_is_live(self, grant_id: UUID) -> bool:
        """Whether a connection still stands: not revoked, not past its ceiling.

        For credentials minted *from* a connection and used elsewhere -- a
        framed app's access to its own files -- which must end when the
        connection does, by whichever of its several paths it ends.
        """
        async with self._uow_factory() as uow:
            scopes = await McpAccessRepository(uow).live_grant_scopes(grant_id)
        return scopes is not None

    async def verify(self, token: str, *, pod_id: UUID) -> McpPrincipal | None:
        """The principal this token acts as on this pod, or ``None``.

        ``None`` for a token issued for a different pod as much as for a forged
        one: the audience is the pod's resource URL (RFC 8707), and a token is
        accepted only where its audience says.
        """
        if not looks_like(token, TokenKind.ACCESS):
            return None
        now = self._now()
        async with self._uow_factory() as uow:
            repository = McpAccessRepository(uow)
            found = await repository.find_token(digest(token))
            if (
                found is None
                or found.kind is not TokenKind.ACCESS
                or found.grant_revoked
                or found.expires_at <= now
                or found.pod_id != pod_id
                or found.resource != pod_resource_url(self._api_url, pod_id)
            ):
                return None
            if (
                found.grant_last_used_at is None
                or now - found.grant_last_used_at > LAST_USED_RESOLUTION
            ):
                await repository.touch_grant(grant_id=found.grant_id, now=now)
                await uow.commit()
        if not await self._account_may_sign_in(found.user_id):
            return None
        if not await self._pod_is_live(found.pod_id):
            return None
        return McpPrincipal(
            user_id=found.user_id,
            pod_id=found.pod_id,
            grant_id=found.grant_id,
            client_id=found.client_id,
            client_name=found.client_name,
            # Never more than the grant holds, whatever the token row says.
            scopes=(
                parse_scopes(found.scopes) & parse_scopes(found.grant_scopes)
                if found.scopes and found.grant_scopes
                else frozenset()
            ),
        )
