from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.services.email_surface_provisioning import (
    provision_email_surface,
)

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.message_bus import get_message_bus
from app.composition.surface_agent import ConversationServiceDep
from app.modules.agent_surfaces.infrastructure.adapters.account_adapter import (
    SqlAlchemySurfaceAccountAdapter,
    SqlAlchemySurfaceAuthConfigAdapter,
)
from app.modules.agent_surfaces.infrastructure.adapters.account_binding import (
    SurfaceAccountBindingResolver,
)
from app.modules.agent_surfaces.infrastructure.adapters.connection_owner_adapter import (
    SqlAlchemySurfaceConnectionOwnerAdapter,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.adapters.user_directory_adapter import (
    IdentityUserDirectoryAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (
    NotificationRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceConversationLinkRepository,
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.notification_rate_limiter import (
    NotificationRateLimiter,
)
from app.modules.agent_surfaces.services.notification_service import (
    NotificationService,
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
from app.modules.agent_surfaces.services.surface_connection_resolver import (
    SurfaceConnectionResolver,
)
from app.modules.agent_surfaces.services.user_surfaces_service import (
    UserSurfacesService,
)
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TelegramManagerService,
)
from app.modules.agent_surfaces.services.pod_name_lookup import pod_name_for


def surface_repository_factory(uow) -> SurfaceRepository:
    return SurfaceRepository(uow, message_bus=get_message_bus())


def get_surface_service(uow: UoWDep) -> AgentSurfaceService:
    account_adapter = SqlAlchemySurfaceAccountAdapter(uow)
    return AgentSurfaceService(
        surface_repository=surface_repository_factory(uow),
        account_binding_resolver=SurfaceAccountBindingResolver(account_adapter),
        account_port=account_adapter,
        auth_config_port=SqlAlchemySurfaceAuthConfigAdapter(uow),
        credential_resolver=SurfaceCredentialResolver(uow=uow),
    )


def get_surface_connection_resolver(uow: UoWDep) -> SurfaceConnectionResolver:
    return SurfaceConnectionResolver(
        account_port=SqlAlchemySurfaceAccountAdapter(uow),
        owner_port=SqlAlchemySurfaceConnectionOwnerAdapter(uow),
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
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
    )


def get_notification_service(
    uow: UoWDep,
    conversation_service: ConversationServiceDep,
) -> NotificationService:
    return NotificationService(
        uow=uow,
        notification_repository=NotificationRepository(uow),
        surface_repository=surface_repository_factory(uow),
        conversation_link_repository=SurfaceConversationLinkRepository(uow),
        external_user_repository=ExternalSurfaceUserRepository(uow),
        conversation_service=conversation_service,
        ingress_service=get_surface_event_handler(uow, conversation_service),
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
        rate_limiter=NotificationRateLimiter(),
        surface_provisioner=_build_system_email_provisioner(uow),
    )


def _build_system_email_provisioner(uow: UoWDep):
    """Give an agent that has no way to reach anyone a mailbox, on first use.

    Delegates to the same function agent creation uses, so a lazily-provisioned
    mailbox is indistinguishable from an eagerly-provisioned one: same readable
    address, same credential checks, same receiver registration. An
    auto-provisioned surface that inbound routing did not recognise would
    deliver mail nobody could reply to.

    Injected as a callable rather than imported by the service, which keeps
    surface *creation* the surface service's job and avoids a new import edge
    into notification delivery.
    """

    async def provision(
        pod_id: UUID, agent_id: UUID | None, agent_name: str | None
    ) -> tuple[AgentSurfaceEntity | None, str | None]:
        return await provision_email_surface(
            get_surface_service(uow),
            uow.session,
            pod_id=pod_id,
            agent_id=agent_id,
            agent_name=agent_name,
            pod_name=await pod_name_for(uow, pod_id),
        )

    return provision


def get_surface_webhook_security_service(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> SurfaceWebhookSecurityService:
    """Factory mode: the secret lookup opens its own short scope.

    Inbound webhook routes carry this, and their request rate belongs to the
    sending platform. Holding a request-scoped connection so that signature
    verification can read one per-workspace secret is the worst place in the app
    to pin one.
    """

    def _resolver(uow) -> SurfaceCredentialResolver:
        return SurfaceCredentialResolver(uow=uow)

    return SurfaceWebhookSecurityService(
        uow_factory=uow_factory, resolver_factory=_resolver
    )


def get_user_surfaces_service(uow: UoWDep) -> UserSurfacesService:
    return UserSurfacesService(
        surface_repository=surface_repository_factory(uow),
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
        user_directory=IdentityUserDirectoryAdapter(uow),
    )


def get_telegram_manager_service(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> TelegramManagerService:
    return TelegramManagerService(uow_factory=uow_factory)


SurfaceServiceDep = Annotated[AgentSurfaceService, Depends(get_surface_service)]
SurfaceConnectionResolverDep = Annotated[
    SurfaceConnectionResolver, Depends(get_surface_connection_resolver)
]
UserSurfacesServiceDep = Annotated[
    UserSurfacesService, Depends(get_user_surfaces_service)
]
SurfaceEventHandlerDep = Annotated[
    AgentSurfaceIngressService, Depends(get_surface_event_handler)
]
SurfaceWebhookSecurityServiceDep = Annotated[
    SurfaceWebhookSecurityService, Depends(get_surface_webhook_security_service)
]
TelegramManagerServiceDep = Annotated[
    TelegramManagerService, Depends(get_telegram_manager_service)
]
NotificationServiceDep = Annotated[
    NotificationService, Depends(get_notification_service)
]
