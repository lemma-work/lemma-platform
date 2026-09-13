"""Resolve how a human reaches a configured surface (its ``reach.handle``).

The handle is the platform-native name a person types or sees to message the
bot: a Slack/Teams bot display name, a Telegram ``@username``, a WhatsApp phone,
or the connected account / email address for email surfaces.

Resolution is **lazy write-through**: the first read that needs a live call
(Slack/Teams/Telegram) fetches the value once and persists it onto the surface's
``surface_identity_username`` column, so every later read reuses the stored value
with no external call. Everything here is best-effort — a GET must always
succeed, so failures degrade to a fallback handle (or None) and never raise.
"""

from __future__ import annotations

from contextlib import suppress
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING
from uuid import UUID


from app.modules.agent_surfaces.platforms.common import (
    PLATFORM_TRANSPORT_ERRORS,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.api.schemas import SurfaceReach
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.core.net.aiohttp_client import new_aiohttp_session
from app.modules.agent_surfaces.platforms.teams.client import (
    GRAPH_BASE,
    auth_headers,
    get_graph_token,
)

from app.modules.connectors.contracts.surfaces import SurfaceAccount

if TYPE_CHECKING:
    from app.modules.agent_surfaces.services.credential_resolver import (
        SurfaceCredentialResolver,
    )

logger = get_logger(__name__)

# Reads the connected account behind a surface. Bound to a unit of work by the
# caller; ``None`` when the account is gone.
FindAccount = Callable[[UUID], Awaitable[SurfaceAccount | None]]

# Hard ceiling on a single surface's live identity lookup so one slow/hung
# provider (notably the Teams Graph call, which has no client-level timeout) can
# never stall a surfaces list. On timeout we degrade to the fallback handle.
_LIVE_HANDLE_TIMEOUT_SECONDS = 6.0

#: Platforms whose live handle lookup needs credentials resolved first --
#: the database step. Teams reads the deployment's own Graph app instead,
#: so it has nothing to look up and nothing to serialise behind.
_CREDENTIALLED_PLATFORMS = frozenset(
    {
        SurfacePlatform.SLACK,
        SurfacePlatform.TELEGRAM,
        SurfacePlatform.WHATSAPP,
    }
)


class SurfaceReachResolver:
    async def resolve(
        self,
        surface: AgentSurfaceEntity,
        *,
        credential_resolver: "SurfaceCredentialResolver | None" = None,
        find_account: FindAccount | None = None,
        surface_repository=None,
    ) -> SurfaceReach:
        """One surface. The batch below is the same work; this is one element."""
        reaches = await self.resolve_many(
            [surface],
            credential_resolver=credential_resolver,
            find_account=find_account,
            surface_repository=surface_repository,
        )
        return reaches[0]

    async def resolve_many(
        self,
        surfaces: Sequence[AgentSurfaceEntity],
        *,
        credential_resolver: "SurfaceCredentialResolver | None" = None,
        find_account: FindAccount | None = None,
        surface_repository=None,
    ) -> list[SurfaceReach]:
        """A page of surfaces, in three phases split by what they touch.

        A listing used to run this whole method concurrently, once per surface,
        over the request's single unit of work -- and resolving one surface
        reads credentials, sometimes refreshes and stores them, may read the
        connected account, and writes the resolved handle back. An
        ``AsyncSession`` serves one operation at a time: two surfaces needing
        any of that at once is an illegal concurrent operation on the session,
        and the surfaces that avoided it only did so by already having a stored
        handle and doing nothing at all.

        Concurrency was reached for because a page of eight surfaces was eight
        sequential round trips to Slack or Telegram. That is still true, and
        still worth fixing -- but the round trips are the only part of this
        that is slow *and* the only part that touches no database. So they are
        the only part that overlaps:

        1. credentials, in series, through the caller's unit of work;
        2. the platform calls, concurrently, over credentials already in hand;
        3. write-through and the account fallback, in series, back on the unit
           of work.

        Credential resolution no longer degrades quietly either: it was inside
        the same guard as the platform call, and a failed statement leaves the
        session unusable for everyone after it, so carrying on past one turned
        a single failure into a page of them with no error to show for it. The
        write-through and the account fallback below keep their guards -- both
        are genuinely optional, and a read must still succeed without them.
        """
        pending: list[int] = []
        resolved: dict[int, SurfaceReach] = {}
        for index, surface in enumerate(surfaces):
            # Already resolved (lazy cache hit): the stored handle, no live call.
            if surface.surface_identity_username:
                resolved[index] = SurfaceReach(
                    handle=surface.surface_identity_username,
                    email=surface.surface_identity_email,
                )
            else:
                pending.append(index)

        credentials = [
            await self._credentials_for(surfaces[i], credential_resolver)
            for i in pending
        ]
        handles = await asyncio.gather(
            *(
                self._live_handle(surfaces[i], credentials[position])
                for position, i in enumerate(pending)
            )
        )

        for position, index in enumerate(pending):
            surface = surfaces[index]
            handle = handles[position]
            # Write-through: a live call produced a NEW username → persist it
            # once so later reads short-circuit above. Idempotent, best-effort.
            if handle and surface_repository is not None:
                await self._persist_username(surface, handle, surface_repository)
            if handle is None:
                handle = await self._fallback_handle(surface, find_account=find_account)
            resolved[index] = SurfaceReach(
                handle=handle, email=surface.surface_identity_email
            )

        # Indexed rather than appended, so the answer lines up with the input
        # positionally. A surface that somehow reached neither branch raises
        # here instead of shifting every reach after it onto the wrong row.
        return [resolved[index] for index in range(len(surfaces))]

    async def _credentials_for(
        self,
        surface: AgentSurfaceEntity,
        credential_resolver: "SurfaceCredentialResolver | None",
    ) -> dict[str, object] | None:
        """The database half of a live handle lookup, on its own.

        Separated so it can run in series while the calls it feeds run
        concurrently. ``None`` means there is nothing to look up -- no
        resolver, or a platform whose handle needs no credentials.
        """
        if credential_resolver is None:
            return None
        if surface.surface_type not in _CREDENTIALLED_PLATFORMS:
            return None
        return await credential_resolver.for_surface(surface)

    async def _live_handle(
        self,
        surface: AgentSurfaceEntity,
        credentials: dict[str, object] | None,
    ) -> str | None:
        """Per-platform live handle lookup (best-effort → None on any failure).

        Bounded by ``_LIVE_HANDLE_TIMEOUT_SECONDS`` so a hung provider can't stall
        the caller; a timeout is treated like any other failure (→ fallback)."""
        if surface.surface_type is SurfacePlatform.SLACK:
            user_id = surface.surface_identity_id
            if credentials is None or not user_id:
                return None
            coro = self._slack_handle(user_id, credentials)
        elif surface.surface_type is SurfacePlatform.TEAMS:
            coro = self._teams_handle(surface)
        elif surface.surface_type is SurfacePlatform.TELEGRAM:
            if credentials is None:
                return None
            coro = self._telegram_handle(credentials)
        elif surface.surface_type is SurfacePlatform.WHATSAPP:
            if credentials is None:
                return None
            coro = self._whatsapp_handle(credentials)
        else:
            return None
        try:
            return await asyncio.wait_for(coro, timeout=_LIVE_HANDLE_TIMEOUT_SECONDS)
        except Exception:  # timeout / API failure — never break the request
            logger.debug(
                "agent_surfaces.surface_reach_resolver.surface_reach_live_handle_surface.observed",
                surface_type=surface.surface_type,
            )
        return None

    async def _slack_handle(
        self, user_id: str, credentials: dict[str, object]
    ) -> str | None:
        from app.modules.agent_surfaces.platforms.slack.service import (
            SlackPlatformService,
        )

        return await SlackPlatformService(
            credentials=credentials
        ).get_user_display_name(user_id)

    async def _telegram_handle(self, credentials: dict[str, object]) -> str | None:
        from app.modules.agent_surfaces.platforms.telegram.service import (
            TelegramPlatformService,
        )

        username = await TelegramPlatformService(credentials).get_bot_username()
        return f"@{username}" if username else None

    async def _whatsapp_handle(self, credentials: dict[str, object]) -> str | None:
        from app.modules.agent_surfaces.platforms.whatsapp.service import (
            WhatsAppPlatformService,
        )

        return await WhatsAppPlatformService(credentials).get_display_phone_number()

    async def _teams_handle(self, surface: AgentSurfaceEntity) -> str | None:
        app_id = surface_settings.microsoft_bot_app_id
        if app_id:
            with suppress(*PLATFORM_TRANSPORT_ERRORS):
                tenant_id = surface.external_tenant_id or "botframework.com"
                token = await get_graph_token(tenant_id)
                if token:
                    url = (
                        f"{GRAPH_BASE}/servicePrincipals(appId='{app_id}')"
                        "?$select=displayName"
                    )
                    async with new_aiohttp_session() as session:
                        async with session.get(
                            url, headers=auth_headers(token)
                        ) as response:
                            if response.status < 400:
                                body = await response.json()
                                name = str(body.get("displayName") or "").strip()
                                if name:
                                    return name
        # Fallback: configured bot display name (still a live-derived handle for
        # write-through purposes, but requires no external call).
        return surface_settings.microsoft_bot_app_name or None

    async def _fallback_handle(
        self,
        surface: AgentSurfaceEntity,
        *,
        find_account: FindAccount | None,
    ) -> str | None:
        """account.display_name → surface_identity_email → None."""
        if surface.account_id is not None and find_account is not None:
            try:
                connected = await find_account(surface.account_id)
                if connected and connected.display_name:
                    return connected.display_name
            except Exception:
                logger.debug(
                    "agent_surfaces.surface_reach_resolver.surface_reach_account_fallback_surface.observed"
                )
        return surface.surface_identity_email or None

    async def _persist_username(
        self,
        surface: AgentSurfaceEntity,
        handle: str,
        surface_repository,
    ) -> None:
        """Write-through the resolved handle; best-effort (never fails the read)."""
        try:
            surface.surface_identity_username = handle
            await surface_repository.update(surface)
        except Exception:
            logger.debug(
                "agent_surfaces.surface_reach_resolver.surface_reach_write_through_surface.observed"
            )
