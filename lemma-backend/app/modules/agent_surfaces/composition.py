"""Where a surfaces object is built, and the only place that knows how.

This lived in `api/dependencies.py`, which made the HTTP layer the module's
composition root: `contracts/` -- the surface this module *publishes* to the rest
of the backend -- imported from `api/` five times, and so did `events/`,
`infrastructure/` and four files under `services/`. Twelve edges pointing at the
one directory that should have been a leaf. Nothing was wrong with the factories;
they were in a place that made every other layer depend on the web framework to
build an object that has nothing to do with the web.

The ingress service was built in three places besides -- `api/dependencies.py`,
`events/handlers.py`, and inline in `services/surface_display_delivery.py`, whose
docstring said it "mirrors" the other two and named a fourth,
`AppWorkerContext.build_surface_event_handler`, that no longer exists. Three
copies of one four-argument constructor, kept in step by hand, with a comment
apologising for it. They are one function now.

`api/dependencies.py` keeps what is genuinely FastAPI's: the `Depends` wrappers
and the `Annotated` aliases a route signature needs. Everything else asks here.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
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
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.email_surface_provisioning import (
    provision_email_surface,
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
from app.modules.agent_surfaces.services.pod_name_lookup import pod_name_for
from app.modules.agent_surfaces.services.surface_connection_resolver import (
    SurfaceConnectionResolver,
)
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TelegramManagerService,
)
from app.modules.agent_surfaces.services.user_surfaces_service import (
    UserSurfacesService,
)
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookSecurityService,
)


def build_surface_repository(uow: SqlAlchemyUnitOfWork) -> SurfaceRepository:
    return SurfaceRepository(uow, message_bus=get_message_bus())


def build_surface_service(uow: SqlAlchemyUnitOfWork) -> AgentSurfaceService:
    account_adapter = SqlAlchemySurfaceAccountAdapter(uow)
    return AgentSurfaceService(
        surface_repository=build_surface_repository(uow),
        account_binding_resolver=SurfaceAccountBindingResolver(account_adapter),
        account_port=account_adapter,
        auth_config_port=SqlAlchemySurfaceAuthConfigAdapter(uow),
        credential_resolver=SurfaceCredentialResolver(uow=uow),
    )


def build_surface_connection_resolver(
    uow: SqlAlchemyUnitOfWork,
) -> SurfaceConnectionResolver:
    return SurfaceConnectionResolver(
        account_port=SqlAlchemySurfaceAccountAdapter(uow),
        owner_port=SqlAlchemySurfaceConnectionOwnerAdapter(uow),
    )


def build_surface_ingress(uow: SqlAlchemyUnitOfWork) -> AgentSurfaceIngressService:
    """The request-mode ingress service: every collaborator bound to one session."""
    return AgentSurfaceIngressService(
        uow=uow,
        surface_repository=build_surface_repository(uow),
        conversation_link_repository=SurfaceConversationLinkRepository(uow),
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
    )


def build_worker_surface_ingress(
    uow_factory: UnitOfWorkFactory,
) -> AgentSurfaceIngressService:
    """An ingress service that scopes its own short units of work.

    Used by the `process_surface_message` worker task: `execute_chat` runs long
    external I/O -- platform APIs, file ingest, voice transcription -- that must
    not hold a pooled DB connection. The service resolves credentials and writes
    the inbound message in separate short scopes from the factory.
    """
    return AgentSurfaceIngressService(uow_factory=uow_factory)


def build_notification_service(uow: SqlAlchemyUnitOfWork) -> NotificationService:
    return NotificationService(
        uow=uow,
        notification_repository=NotificationRepository(uow),
        surface_repository=build_surface_repository(uow),
        conversation_link_repository=SurfaceConversationLinkRepository(uow),
        external_user_repository=ExternalSurfaceUserRepository(uow),
        ingress_service=build_surface_ingress(uow),
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
        rate_limiter=NotificationRateLimiter(),
        surface_provisioner=_build_system_email_provisioner(uow),
    )


def _build_system_email_provisioner(uow: SqlAlchemyUnitOfWork):
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
            build_surface_service(uow),
            uow.session,
            pod_id=pod_id,
            agent_id=agent_id,
            agent_name=agent_name,
            pod_name=await pod_name_for(uow, pod_id),
        )

    return provision


def build_surface_webhook_security_service(
    uow_factory: UnitOfWorkFactory,
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


def build_user_surfaces_service(uow: SqlAlchemyUnitOfWork) -> UserSurfacesService:
    return UserSurfacesService(
        surface_repository=build_surface_repository(uow),
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
        user_directory=IdentityUserDirectoryAdapter(uow),
    )


def build_telegram_manager_service(
    uow_factory: UnitOfWorkFactory,
) -> TelegramManagerService:
    return TelegramManagerService(uow_factory=uow_factory)
