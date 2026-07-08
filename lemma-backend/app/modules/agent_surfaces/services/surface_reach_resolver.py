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

import asyncio
from typing import TYPE_CHECKING

import aiohttp

from app.core.log.log import get_logger
from app.modules.agent_surfaces.api.schemas import SurfaceReach
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.teams.client import (
    GRAPH_BASE,
    auth_headers,
    get_graph_token,
)

if TYPE_CHECKING:
    from app.modules.agent_surfaces.services.credential_resolver import (
        SurfaceCredentialResolver,
    )
    from app.modules.connectors.services.connector_service import ConnectorService

logger = get_logger(__name__)

# Hard ceiling on a single surface's live identity lookup so one slow/hung
# provider (notably the Teams Graph call, which has no client-level timeout) can
# never stall a surfaces list. On timeout we degrade to the fallback handle.
_LIVE_HANDLE_TIMEOUT_SECONDS = 6.0


class SurfaceReachResolver:
    async def resolve(
        self,
        surface: AgentSurfaceEntity,
        *,
        credential_resolver: "SurfaceCredentialResolver | None" = None,
        connector_service: "ConnectorService | None" = None,
        surface_repository=None,
    ) -> SurfaceReach:
        email = surface.surface_identity_email

        # Already resolved (lazy cache hit): reuse the stored handle, no live call.
        if surface.surface_identity_username:
            return SurfaceReach(handle=surface.surface_identity_username, email=email)

        handle = await self._resolve_live_handle(
            surface, credential_resolver=credential_resolver
        )

        # Write-through: a live call produced a NEW username → persist it once so
        # later reads short-circuit above. Idempotent + best-effort.
        if handle and surface_repository is not None:
            await self._persist_username(surface, handle, surface_repository)

        if handle is None:
            handle = await self._fallback_handle(
                surface, connector_service=connector_service
            )

        return SurfaceReach(handle=handle, email=email)

    async def _resolve_live_handle(
        self,
        surface: AgentSurfaceEntity,
        *,
        credential_resolver: "SurfaceCredentialResolver | None",
    ) -> str | None:
        """Per-platform live handle lookup (best-effort → None on any failure).

        Bounded by ``_LIVE_HANDLE_TIMEOUT_SECONDS`` so a hung provider can't stall
        the caller; a timeout is treated like any other failure (→ fallback)."""
        if surface.surface_type is SurfacePlatform.SLACK:
            coro = self._slack_handle(surface, credential_resolver)
        elif surface.surface_type is SurfacePlatform.TEAMS:
            coro = self._teams_handle(surface)
        elif surface.surface_type is SurfacePlatform.TELEGRAM:
            coro = self._telegram_handle(surface, credential_resolver)
        else:
            return None
        try:
            return await asyncio.wait_for(coro, timeout=_LIVE_HANDLE_TIMEOUT_SECONDS)
        except Exception as exc:  # timeout / API failure — never break the request
            logger.debug(
                "Surface reach live handle failed for surface=%s platform=%s: %s",
                surface.id,
                surface.surface_type,
                exc,
            )
        return None

    async def _slack_handle(
        self,
        surface: AgentSurfaceEntity,
        credential_resolver: "SurfaceCredentialResolver | None",
    ) -> str | None:
        if credential_resolver is None or not surface.surface_identity_id:
            return None
        from app.modules.agent_surfaces.platforms.slack.service import (
            SlackPlatformService,
        )

        credentials = await credential_resolver.for_surface(surface)
        return await SlackPlatformService(
            credentials=credentials
        ).get_user_display_name(surface.surface_identity_id)

    async def _telegram_handle(
        self,
        surface: AgentSurfaceEntity,
        credential_resolver: "SurfaceCredentialResolver | None",
    ) -> str | None:
        if credential_resolver is None:
            return None
        from app.modules.agent_surfaces.platforms.telegram.service import (
            TelegramPlatformService,
        )

        credentials = await credential_resolver.for_surface(surface)
        username = await TelegramPlatformService(credentials).get_bot_username()
        return f"@{username}" if username else None

    async def _teams_handle(self, surface: AgentSurfaceEntity) -> str | None:
        app_id = surface_settings.microsoft_bot_app_id
        if app_id:
            try:
                tenant_id = surface.external_tenant_id or "botframework.com"
                token = await get_graph_token(tenant_id)
                if token:
                    url = (
                        f"{GRAPH_BASE}/servicePrincipals(appId='{app_id}')"
                        "?$select=displayName"
                    )
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            url, headers=auth_headers(token)
                        ) as response:
                            if response.status < 400:
                                body = await response.json()
                                name = str(body.get("displayName") or "").strip()
                                if name:
                                    return name
            except Exception as exc:  # Application.Read.All may 403 — fall back
                logger.debug(
                    "Teams servicePrincipal lookup failed for surface=%s: %s",
                    surface.id,
                    exc,
                )
        # Fallback: configured bot display name (still a live-derived handle for
        # write-through purposes, but requires no external call).
        return surface_settings.microsoft_bot_app_name or None

    async def _fallback_handle(
        self,
        surface: AgentSurfaceEntity,
        *,
        connector_service: "ConnectorService | None",
    ) -> str | None:
        """account.display_name → surface_identity_email → None."""
        if surface.account_id is not None and connector_service is not None:
            try:
                account = await connector_service.account_repository.get(
                    surface.account_id
                )
                if account and account.display_name:
                    return account.display_name
            except Exception as exc:
                logger.debug(
                    "Surface reach account fallback failed for surface=%s: %s",
                    surface.id,
                    exc,
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
        except Exception as exc:
            logger.debug(
                "Surface reach write-through failed for surface=%s: %s",
                surface.id,
                exc,
            )
