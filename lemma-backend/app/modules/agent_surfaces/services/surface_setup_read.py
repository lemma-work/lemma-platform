"""Read model for the remaining setup work on an existing surface."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.setup_guides import build_surface_setup_actions
from app.modules.agent_surfaces.platforms.common import computed_webhook_url


class SurfaceSetupReadMixin:
    async def get_surface_setup_by_name(
        self, *, pod_id: UUID, name: str
    ) -> dict[str, Any]:
        surface = await self.get_surface_by_name_in_pod(pod_id=pod_id, name=name)
        guide = self.get_platform_setup_guide(surface.surface_type.value)
        webhook_url = computed_webhook_url(surface)
        admin_consent = await self._surface_admin_consent(surface)
        is_custom_app = await self._surface_uses_org_custom_app(surface)
        signing_secret_missing = await self._slack_signing_secret_missing(
            surface=surface, is_custom_app=is_custom_app
        )
        frontend_url = str(getattr(settings, "frontend_url", "") or "").rstrip("/")
        actions = build_surface_setup_actions(
            platform=surface.surface_type,
            is_custom_app=is_custom_app,
            webhook_url=webhook_url,
            slack_socket_mode=surface_settings.enable_slack_socket_mode,
            slack_signing_secret_missing=signing_secret_missing,
            slack_repair_url=(
                f"{frontend_url}/pod/{surface.pod_id}/connectors"
                if frontend_url
                else None
            ),
            whatsapp_verify_token=await self._whatsapp_verify_token_for_setup(surface),
        )
        pending_consent = bool(
            admin_consent and admin_consent["required"] and not admin_consent["granted"]
        )
        return {
            "platform": surface.surface_type,
            "exists": True,
            "status": (
                AgentSurfaceStatus.NEEDS_SETUP
                if signing_secret_missing
                else surface.status
            ),
            "ready": not any(action.is_blocking for action in actions)
            and not pending_consent,
            "webhook_url": webhook_url,
            "admin_consent": admin_consent,
            "actions": actions,
            "guide": guide,
        }

    async def _slack_signing_secret_missing(
        self, *, surface, is_custom_app: bool
    ) -> bool:
        if (
            surface.surface_type is not SurfacePlatform.SLACK
            or not is_custom_app
            or self._credential_resolver is None
        ):
            return False
        credentials = await self._credential_resolver.slack_webhook_credentials(surface)
        return not bool(credentials.signing_secret)
