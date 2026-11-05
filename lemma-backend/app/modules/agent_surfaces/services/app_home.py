"""The app's home tab: what one viewer sees when they open the app.

Apart from `AppEventHandler` because it is a read and a render, with no write
and no configuration decision in it -- the handler decides *when* a home is
published (an open, a surface chosen), this decides what goes in it.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.context import Context
from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import channels as agent_conversations
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.adapter_port import InChatSetup
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.services.configuration_access import (
    ConfigurationAccess,
)
from app.modules.pod.contracts.members import pod_name

logger = get_logger(__name__)


async def surface_agent_name(
    uow: SqlAlchemyUnitOfWork, surface: AgentSurfaceEntity
) -> str:
    """What this surface's agent is called in front of a person."""
    agent = await agent_conversations.surface_agent_identity(uow, surface.agent_id)
    if agent is None or agent.is_pod_default:
        return DEFAULT_RESPONDER_NAME
    return agent.name


class AppHomeView:
    """Renders the home tab for one viewer, with what that viewer may see."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        access: ConfigurationAccess,
        external_user_repository: ExternalSurfaceUserRepository,
    ) -> None:
        self.uow = uow
        self.access = access
        self.external_user_repository = external_user_repository

    async def show_to(
        self,
        *,
        surface: AgentSurfaceEntity,
        adapter: InChatSetup,
        credentials: SurfaceCredentials,
        external_user_id: str,
        ctx: Context | None,
    ) -> None:
        """Render the Home tab for one viewer."""
        if not external_user_id:
            return
        agents = await self.access._visible_agents(
            surface=surface, ctx=ctx, action=Permissions.AGENT_READ
        )
        name_of_pod = await pod_name(self.uow.session, surface.pod_id)
        # Egress: the pod and agent rows are read above; the connection
        # goes back before the view is pushed to the platform.
        async with connection_released(self.uow.session):
            await adapter.publish_home_view(
                credentials=credentials,
                user_id=external_user_id,
                pod_name=name_of_pod or None,
                agent_name=await surface_agent_name(self.uow, surface),
                # The channels this bot answers in. Ids alone: each entry used
                # to name the agent routed to that channel, and there is one
                # agent now, so the Home tab states it once above the list.
                channel_ids=[
                    route.channel_id
                    for route in surface.config.channels
                    if route.channel_id
                ],
                agents=[(agent.name, agent.description) for agent in agents],
                apps=await self.apps_for(
                    surface=surface,
                    external_user_id=external_user_id,
                    auth_ctx=ctx,
                ),
                workspace_url=settings.frontend_url or None,
                logo_url=surface_settings.slack_home_logo_url,
            )

    async def apps_for(
        self,
        *,
        surface: AgentSurfaceEntity,
        external_user_id: str,
        auth_ctx: Context | None = None,
    ) -> list[tuple[str, str]]:
        """Apps this *viewer* may open, as (name, url) — never the pod's full list.

        Visibility is per-user, so an unresolvable Slack identity gets no apps
        rather than everyone else's.
        """
        try:
            from app.modules.apps.contracts import list_ready_pod_apps

            if auth_ctx is None:
                external = await self.external_user_repository.get_by_identity(
                    platform="SLACK",
                    tenant_id=surface.external_workspace_id,
                    external_user_id=external_user_id,
                )
                resolved_user_id = getattr(external, "resolved_user_id", None)
                if resolved_user_id is None:
                    return []
                auth_ctx = await create_authorization_data_service(
                    self.uow
                ).build_user_context(user_id=resolved_user_id, pod_id=surface.pod_id)
            apps = await list_ready_pod_apps(
                uow=self.uow, pod_id=surface.pod_id, ctx=auth_ctx
            )
        except SQLAlchemyError:
            logger.warning(
                "agent_surfaces.app_event_handler.home_apps_unavailable.degraded",
                surface_id=str(surface.id),
                exc_info=True,
            )
            return []
        domain = settings.app_base_domain.strip()
        if not domain:
            return []
        return [(app.name, f"https://{app.public_slug}.{domain}") for app in apps]
