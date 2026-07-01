from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import UoWDep
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.agent.api.dependencies import ConversationServiceDep
from app.modules.agent_surfaces.infrastructure.adapters.account_adapter import (
    SqlAlchemySurfaceAccountAdapter,
    SqlAlchemySurfaceAuthConfigAdapter,
)
from app.modules.agent_surfaces.infrastructure.adapters.account_binding import (
    SurfaceAccountBindingResolver,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceConversationLinkRepository,
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookSecurityService,
)
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.connectors.api.dependencies import get_connector_service
from app.modules.connectors.infrastructure.repositories.connector_trigger_repository import (
    ConnectorTriggerRepository,
)
from app.modules.schedule.api.dependencies import get_schedule_service


def surface_repository_factory(uow) -> SurfaceRepository:
    return SurfaceRepository(uow, message_bus=get_message_bus())


def get_surface_service(uow: UoWDep) -> AgentSurfaceService:
    account_adapter = SqlAlchemySurfaceAccountAdapter(uow)
    return AgentSurfaceService(
        surface_repository=surface_repository_factory(uow),
        account_binding_resolver=SurfaceAccountBindingResolver(account_adapter),
        schedule_service=get_schedule_service(uow),
        connector_trigger_repository=ConnectorTriggerRepository(uow=uow),
        account_port=account_adapter,
        auth_config_port=SqlAlchemySurfaceAuthConfigAdapter(uow),
        credential_resolver=SurfaceCredentialResolver(
            session=uow.session,
            connector_service=get_connector_service(uow),
        ),
    )


def get_surface_event_handler(
    uow: UoWDep,
    conversation_service: ConversationServiceDep,
) -> AgentSurfaceIngressService:
    return AgentSurfaceIngressService(
        uow=uow,
        surface_repository=surface_repository_factory(uow),
        conversation_link_repository=SurfaceConversationLinkRepository(uow),
        conversation_service=conversation_service,
        connector_service=get_connector_service(uow),
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
    )


def get_surface_webhook_security_service(
    uow: UoWDep,
) -> SurfaceWebhookSecurityService:
    return SurfaceWebhookSecurityService(
        credential_resolver=SurfaceCredentialResolver(
            session=uow.session,
            connector_service=get_connector_service(uow),
        )
    )


SurfaceServiceDep = Annotated[AgentSurfaceService, Depends(get_surface_service)]
SurfaceEventHandlerDep = Annotated[
    AgentSurfaceIngressService, Depends(get_surface_event_handler)
]
SurfaceWebhookSecurityServiceDep = Annotated[
    SurfaceWebhookSecurityService, Depends(get_surface_webhook_security_service)
]
