"""Tenant admin consent, and the setup guide that asks for it.

A surface on an organisation's own app cannot receive anything until a tenant
admin has consented to it. Everything here is about establishing whether that
has happened, telling the person how to make it happen, and activating the
surface once it has.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx

from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.services import teams_consent
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
)
from app.modules.connectors.contracts import AuthConfigSource
from app.modules.agent_surfaces.domain.setup_guides import (
    SurfacePlatformSetupGuide,
    build_surface_setup_guide,
)
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    pass
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Shared Redis cache of Teams admin-consent probe results (per-entry TTL: 60 s
# granted / 10 s denied), so the Graph probe is shared across replicas. Redis
# unavailable -> re-probe (never fails).
_consent_check_cache: RedisJsonCache | None = None


def _get_consent_cache() -> RedisJsonCache:
    global _consent_check_cache
    if (
        _consent_check_cache is None
        or _consent_check_cache._redis_url != settings.redis_url
    ):
        _consent_check_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:teams-consent",
            ttl_seconds=60,
        )
    return _consent_check_cache


class SurfaceConsentMixin:
    """Split out of :class:`AgentSurfaceService`; see the module docstring."""

    def get_platform_setup_guide(self, platform: str) -> SurfacePlatformSetupGuide:
        resolved_platform = SurfacePlatform.from_source(platform)
        if resolved_platform is None:
            normalized = str(platform).upper()
            try:
                resolved_platform = SurfacePlatform[normalized]
            except KeyError as exc:
                raise AgentSurfaceValidationError(
                    f"Unsupported surface platform '{platform}'"
                ) from exc
        return build_surface_setup_guide(resolved_platform)

    async def _surface_uses_org_custom_app(self, surface: AgentSurfaceEntity) -> bool:
        """True when the org must manually point a platform app's webhook at Lemma.

        For OAuth platforms this means the account was set up with the org's
        own app (auth config ``ORG_CUSTOM``) — Lemma's system app is already
        wired up centrally. WhatsApp is credential-managed (API_KEY, no OAuth
        app registration) so there is no Lemma-shared "system app" to fall
        back to: any connected account is the org's own WhatsApp Business app
        and always needs its webhook configured.
        """
        if surface.account_id is None:
            return False
        if surface.surface_type is SurfacePlatform.WHATSAPP:
            return True

        if self._account_port is None or self._auth_config_port is None:
            return False
        account = await self._account_port.get_account(surface.account_id)
        if account is None or account.auth_config_id is None:
            return False
        auth_config = await self._auth_config_port.get_auth_config(
            account.auth_config_id
        )
        return bool(
            auth_config
            and auth_config.config_source == AuthConfigSource.ORG_CUSTOM.value
        )

    async def _whatsapp_verify_token_for_setup(
        self, surface: AgentSurfaceEntity
    ) -> str | None:
        """The verify token to show the user for pasting into Meta's console.

        A connected account's own ``verify_token`` (from its stored
        credentials) — the value the backend actually checks incoming
        ``hub.verify_token`` requests against for that surface. Falls back to
        the system-wide token for account-less (Lemma-managed) surfaces.
        """
        if (
            surface.surface_type is SurfacePlatform.WHATSAPP
            and surface.account_id is not None
            and self._credential_resolver is not None
        ):
            try:
                credentials = await self._credential_resolver.for_account(
                    surface.account_id
                )
            except Exception:
                logger.debug(
                    "agent_surfaces.surface_service.could_not_resolve_whatsapp_verify.diagnostic",
                    account_id=surface.account_id,
                    exc_info=True,
                )
                return None
            return credentials.get("verify_token")
        return surface_settings.whatsapp_verify_token

    async def _surface_admin_consent(
        self, surface: AgentSurfaceEntity
    ) -> dict[str, Any] | None:
        """Teams admin-consent state, or None for platforms that never need it."""
        if surface.surface_type is not SurfacePlatform.TEAMS:
            return None
        info = await self.get_admin_consent_info(surface)
        return {
            "required": True,
            "granted": info.get("status") is AgentSurfaceStatus.ACTIVE,
            "consent_url": info.get("consent_url"),
        }

    async def activate_after_consent(
        self,
        *,
        surface_id: UUID,
        tenant_id: str,
    ) -> AgentSurfaceEntity | None:
        surface = await self.surface_repository.get(surface_id)
        if surface is None:
            return None

        # `external_tenant_id` is the inbound-message tenant gate, and this
        # write is first-wins, so a wrong value here would both reject the real
        # tenant's messages and let the writer's own tenant through -- and then
        # persist, because later legitimate activations skip the overwrite.
        if not surface.external_tenant_id:
            surface.external_tenant_id = tenant_id

        surface.activate()
        return await self.surface_repository.update(surface)

    async def get_admin_consent_info(
        self, surface: AgentSurfaceEntity
    ) -> dict[str, Any]:
        if surface.surface_type != SurfacePlatform.TEAMS:
            return {"status": surface.status}

        tenant_id = surface.external_tenant_id
        if not tenant_id:
            return {"status": surface.status, "consent_url": None}

        if surface.status is AgentSurfaceStatus.ACTIVE:
            return {"status": AgentSurfaceStatus.ACTIVE}

        already_granted = await self._check_admin_consent_granted(tenant_id)
        if already_granted:
            surface.activate()
            await self.surface_repository.update(surface)
            return {"status": AgentSurfaceStatus.ACTIVE}

        consent_url = await teams_consent.build_consent_url(surface.id, tenant_id)
        return {
            "status": AgentSurfaceStatus.PENDING_ADMIN_CONSENT,
            "consent_url": consent_url,
        }

    async def _check_admin_consent_granted(self, tenant_id: str) -> bool:
        app_id = surface_settings.microsoft_bot_app_id
        app_password = surface_settings.microsoft_bot_app_password
        if not app_id or not app_password:
            return False

        cache_key = f"consent_check:{tenant_id}"
        cache = _get_consent_cache()
        try:
            cached = await cache.get_json(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            return bool(cached)

        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                token_response = await client.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": app_id,
                        "client_secret": app_password,
                        "scope": _GRAPH_SCOPE,
                    },
                )
                if token_response.status_code != 200:
                    try:
                        await cache.set_json(cache_key, False, ttl_seconds=10)
                    except Exception:
                        pass
                    return False
                token = token_response.json().get("access_token")
        except Exception:
            return False

        if not token:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                probe = await client.get(
                    "https://graph.microsoft.com/v1.0/users?$top=1&$select=id",
                    headers={"Authorization": f"Bearer {token}"},
                )
                granted = probe.status_code == 200
        except Exception:
            granted = False

        try:
            await cache.set_json(cache_key, granted, ttl_seconds=60 if granted else 10)
        except Exception:
            pass
        return granted
