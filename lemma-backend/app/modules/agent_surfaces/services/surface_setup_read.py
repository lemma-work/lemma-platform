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
from app.modules.agent_surfaces.domain.setup_actions import build_surface_setup_actions
from app.modules.agent_surfaces.platforms.common import computed_webhook_url


class SurfaceSetupReadMixin:
    async def get_surface_setup_by_name(
        self, *, pod_id: UUID, name: str, reveal_secrets: bool
    ) -> dict[str, Any]:
        """The remaining setup work on this surface, for one reader.

        ``reveal_secrets`` decides whether the org's own shared secrets appear
        in the copy-able fields. ``SurfaceSetupActionField.secret`` is a
        rendering hint, not an access control, and the WhatsApp verify token is
        what re-points the org's webhook subscription — so it goes only to a
        reader who could change the surface anyway, not to everyone holding
        ``AGENT_READ`` on the pod. Required rather than defaulted: whether the
        caller may see them is not a question a call site should be able to skip.
        """
        surface = await self.get_surface_by_name_in_pod(pod_id=pod_id, name=name)
        guide = self.get_platform_setup_guide(surface.surface_type.value)
        webhook_url = computed_webhook_url(surface)
        admin_consent = await self._surface_admin_consent(surface)
        is_custom_app = await self._surface_uses_org_custom_app(surface)
        signing_secret_missing, app_id_missing = await self._slack_verification_gaps(
            surface=surface, is_custom_app=is_custom_app
        )
        cannot_verify = signing_secret_missing or app_id_missing
        frontend_url = settings.frontend_url.rstrip("/")
        actions = build_surface_setup_actions(
            platform=surface.surface_type,
            is_custom_app=is_custom_app,
            webhook_url=webhook_url,
            slack_socket_mode=surface_settings.enable_slack_socket_mode,
            slack_signing_secret_missing=signing_secret_missing,
            slack_app_id_missing=app_id_missing,
            slack_repair_url=(
                f"{frontend_url}/pod/{surface.pod_id}/connectors"
                if frontend_url
                else None
            ),
            whatsapp_verify_token=(
                await self._whatsapp_verify_token_for_setup(surface)
                if reveal_secrets
                else None
            ),
        )
        pending_consent = bool(
            admin_consent and admin_consent["required"] and not admin_consent["granted"]
        )
        return {
            "platform": surface.surface_type,
            "exists": True,
            "status": (
                AgentSurfaceStatus.NEEDS_SETUP if cannot_verify else surface.status
            ),
            "ready": not any(action.is_blocking for action in actions)
            and not pending_consent,
            "webhook_url": webhook_url,
            "admin_consent": admin_consent,
            "actions": actions,
            "guide": guide,
        }

    async def _slack_verification_gaps(
        self, *, surface, is_custom_app: bool
    ) -> tuple[bool, bool]:
        """``(signing_secret_missing, app_id_missing)`` for a custom Slack app.

        Both halves, because ``slack_candidates_for_workspace`` requires both:
        a surface missing either is skipped as a verification candidate, and
        every event for it is rejected. Only the secret was checked here, so a
        custom app whose account never stored an ``app_id`` — connected before
        we recorded it, or through a broker that drops the field — reported
        itself ready and then answered nothing, with no way to find out why.
        There is no guessing the id: it belongs to the org's own app.
        """
        if (
            surface.surface_type is not SurfacePlatform.SLACK
            or not is_custom_app
            or self._credential_resolver is None
        ):
            return False, False
        credentials = await self._credential_resolver.slack_webhook_credentials(surface)
        return not bool(credentials.signing_secret), not bool(credentials.app_id)
