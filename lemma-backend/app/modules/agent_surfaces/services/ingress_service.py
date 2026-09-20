from __future__ import annotations


from app.modules.agent_surfaces.services.surface_configuration import (
    SurfaceConfigurationMixin,
)
from app.modules.agent_surfaces.services.surface_interactions import (
    SurfaceInteractionMixin,
)
from app.modules.agent_surfaces.services.surface_routing import SurfaceRoutingMixin
from app.modules.agent_surfaces.services.surface_conversation_links import (
    SurfaceConversationLinkMixin,
)
from app.modules.agent_surfaces.services.surface_inbound import SurfaceInboundMixin
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceIngressRequest,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
    SurfaceInstallationRepositoryPort,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.


class AgentSurfaceIngressService(
    SurfaceConfigurationMixin,
    SurfaceRoutingMixin,
    SurfaceConversationLinkMixin,
    SurfaceInboundMixin,
    SurfaceInteractionMixin,
):
    """Everything between a webhook arriving and a run being queued.

    Six bases were eight. Four went to `SurfaceEgress`, `SurfaceProgress`,
    `SurfaceDelivery` and `MemberReach` -- the outbound half never read an
    inbound event, and the inbound half reached it exactly once, from a method
    that already had a unit of work in hand.

    What went with them is a *mode*. This took either a unit of work or a
    factory; in factory mode seven collaborators were `None` and about forty of
    its ninety-two methods would have raised `AttributeError`. One signature
    produced two different objects, and it survived only because exactly one
    caller used the second for exactly one method -- the worker's
    `execute_chat`, which is `SurfaceTurnStarter` now. So `uow` is required
    here, which is why nothing in this package writes
    `getattr(self.uow, "session", None)` any more: there is nothing to guard.
    """

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        conversation_link_repository: SurfaceConversationLinkRepository,
        pod_membership_port: SurfacePodMembershipPort,
        adapter_registry: SurfacePlatformAdapterRegistry | None = None,
        event_dedup_store: SurfaceEventDedupStorePort | None = None,
    ):
        self.uow = uow
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository
        self.pod_membership_port = pod_membership_port
        # The two process-wide ones keep their defaults: an adapter registry is
        # five stateless objects and the dedup store is a Redis client, so a
        # caller that has no opinion should not have to build either.
        self.adapter_registry = adapter_registry or SurfacePlatformAdapterRegistry()
        self.event_dedup_store = event_dedup_store or get_surface_event_dedup_store()
        self.external_user_repository = ExternalSurfaceUserRepository(uow)
        self.identity_service = SurfaceIdentityResolutionService(
            uow, self.external_user_repository
        )
        self.credential_resolver = SurfaceCredentialResolver(uow=uow)

    def split_webhook_deliveries(
        self, request: SurfaceIngressRequest
    ) -> list[SurfaceIngressRequest]:
        """One webhook delivery, as the one-or-more inbound events it carries.

        Only the platform knows whether its webhook can batch, so the question
        is asked of the adapter; every adapter that cannot answers "itself" and
        this returns the request unchanged, which is the whole of the behaviour
        for six of the seven platforms.
        """
        if not isinstance(request, SurfacePlatformWebhookIngress):
            return [request]
        platform = self._resolve_platform(request.source)
        adapter = self.adapter_registry.get(platform) if platform else None
        if adapter is None:
            return [request]
        # Not defended with a catch: `payload` is a validated dict, every split
        # is isinstance-guarded, and the default returns its argument. A raise
        # here would mean a genuine bug, and swallowing it would hide the same
        # class of silent message loss this exists to end.
        payloads = adapter.split_inbound_payloads(request.payload)
        if len(payloads) <= 1:
            return [request]
        logger.info(
            "agent_surfaces.ingress_service.webhook_carried_several_messages.observed",
            source=request.source,
            message_count=len(payloads),
        )
        return [request.model_copy(update={"payload": payload}) for payload in payloads]

    async def prepare_ingress(
        self, request: SurfaceIngressRequest
    ) -> AgentSurfaceContext | None:
        if isinstance(request, SurfacePlatformWebhookIngress):
            return await self._prepare_platform_webhook_ingress(request)
        return await self._prepare_surface_webhook_ingress(request)
