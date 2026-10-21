"""Read model for the remaining setup work on an existing surface."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.domain.setup_actions import (
    SurfaceSetupAction,
    build_surface_setup_actions,
)
from app.modules.agent_surfaces.domain.surface_representation import (
    SurfaceConnection,
    SurfaceConnectionStatus,
)
from app.modules.agent_surfaces.platforms.common import computed_webhook_url
from app.modules.agent_surfaces.services.surface_problems import surface_problem
from app.modules.channels.contracts.places import surface_heard_from


class SurfaceSetupReadMixin:
    #: ``AgentSurfaceService``'s, which composes this.
    surface_repository: SurfaceInstallationRepositoryPort

    async def get_surface_setup_by_name(
        self,
        *,
        pod_id: UUID,
        name: str,
        reveal_secrets: bool,
        connection: SurfaceConnection | None,
    ) -> dict[str, Any]:
        """The remaining setup work on this surface, for one reader.

        ``reveal_secrets`` decides whether the org's own shared secrets appear
        in the copy-able fields, and whether a Teams consent link does. ``SurfaceSetupActionField.secret`` is a
        rendering hint, not an access control, and the WhatsApp verify token is
        what re-points the org's webhook subscription — so it goes only to a
        reader who could change the surface anyway, not to everyone holding
        ``AGENT_READ`` on the pod. Required rather than defaulted: whether the
        caller may see them is not a question a call site should be able to skip.

        ``connection`` is the surface's account as ``SurfaceConnectionResolver``
        reads it for this reader -- best-effort, so None when it could not be
        read. Required for the same reason: it is how Slack removing the app is
        seen at all (the account is what ``services/app_uninstall`` retires),
        and who connected it is who the reinstall step names.
        """
        surface = await self.get_surface_by_name_in_pod(pod_id=pod_id, name=name)
        guide = self.get_platform_setup_guide(surface.surface_type.value)
        webhook_url = computed_webhook_url(surface)
        admin_consent = await self._surface_admin_consent(
            surface, include_consent_url=reveal_secrets
        )
        is_custom_app = await self._surface_uses_org_custom_app(surface)
        signing_secret_missing, app_id_missing = await self._slack_verification_gaps(
            surface=surface, is_custom_app=is_custom_app
        )
        cannot_verify = signing_secret_missing or app_id_missing
        retired = _slack_retired(surface, connection)
        frontend_url = settings.frontend_url.rstrip("/")
        actions = build_surface_setup_actions(
            platform=surface.surface_type,
            is_custom_app=is_custom_app,
            webhook_url=webhook_url,
            slack_socket_mode=surface_settings.enable_slack_socket_mode,
            slack_signing_secret_missing=signing_secret_missing,
            slack_app_id_missing=app_id_missing,
            slack_retired_connection=retired,
            # Connectors have no route of their own in the workspace: Settings
            # opens at a section named in `?settings=`. The space in the path
            # is what picks the organization whose connectors those are.
            slack_repair_url=(
                f"{frontend_url}/t/{surface.pod_id}?settings=connectors"
                if frontend_url
                else None
            ),
            whatsapp_verify_token=(
                await self._whatsapp_verify_token_for_setup(surface)
                if reveal_secrets
                else None
            ),
        )
        actions = await self._as_the_platform_sees_it(surface, actions)
        pending_consent = bool(
            admin_consent and admin_consent["required"] and not admin_consent["granted"]
        )
        return {
            "platform": surface.surface_type,
            "exists": True,
            "status": (
                AgentSurfaceStatus.NEEDS_SETUP
                if cannot_verify or retired is not None
                else surface.status
            ),
            "ready": not any(action.is_blocking for action in actions)
            and not pending_consent,
            "webhook_url": webhook_url,
            "admin_consent": admin_consent,
            "actions": actions,
            "guide": guide,
        }

    async def _as_the_platform_sees_it(
        self, surface: AgentSurfaceEntity, actions: list[SurfaceSetupAction]
    ) -> list[SurfaceSetupAction]:
        """The checklist corrected by what the platform has said since."""
        if surface.surface_type is SurfacePlatform.WHATSAPP and actions:
            # The repository's unit of work is the request's SQLAlchemy one.
            uow = cast(SqlAlchemyUnitOfWork, self.surface_repository.uow)
            if await surface_heard_from(uow, surface.id):
                # Messages are arriving: the webhook is set, whatever the
                # checklist says. Kept as reference, not as work.
                actions = [
                    action.model_copy(update={"informational": True})
                    if action.key == "whatsapp_webhook"
                    else action
                    for action in actions
                ]
        problem = await surface_problem(surface)
        if problem is not None:
            # Whatever else is done, a surface the platform refuses is not ready.
            actions = [
                SurfaceSetupAction(
                    key="surface_problem",
                    title="Fix this surface",
                    description=problem,
                ),
                *actions,
            ]
        return actions

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


def _slack_retired(
    surface: AgentSurfaceEntity, connection: SurfaceConnection | None
) -> SurfaceConnection | None:
    """The connection, when it is a Slack one Slack took the app away from.

    Read off the account, because that is where the uninstall is recorded: the
    surface keeps its channels and settings for when it comes back.
    """
    if (
        surface.surface_type is SurfacePlatform.SLACK
        and connection is not None
        and connection.status is SurfaceConnectionStatus.REAUTH_REQUIRED
    ):
        return connection
    return None
