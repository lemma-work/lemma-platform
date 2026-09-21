"""A webhook in, a routed context out.

Two things a surfaces webhook can be once the app-event paths have declined it:
a native form submission that resumes a paused run, or a message that becomes
one. `SurfaceInboundMixin` and `SurfaceInteractionMixin` are those two, and they
are mixins rather than collaborators because they are the *only* two and neither
calls the other -- this class is the pair, not a namespace they were filed in.

It had eight bases and an MRO of fifteen. What the other six became:

  `SurfaceEgress`, `SurfaceProgress`, `SurfaceDelivery`, `MemberReach`
      the outbound half, which never read an inbound event
  `SurfaceTurnStarter`
      the worker's half, which was the second of two constructor modes
  `AppEventHandler`, `ConfigurationAccess`
      set-up and lifecycle, which nothing outside ever called into
  `SurfaceRouter`, `ConversationBinder`
      the bottom of the graph: both halves here reach into them and they reach
      into neither. Collaborators, declared on both mixins, which is what lets
      the type checker follow a call that used to resolve to nothing.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.ingress_context import AgentSurfaceContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceIngressRequest,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.entities import platform_value_for_source
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.surface_inbound import SurfaceInboundMixin
from app.modules.agent_surfaces.services.surface_interactions import (
    SurfaceInteractionMixin,
)
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter

logger = get_logger(__name__)


class AgentSurfaceIngressService(SurfaceInboundMixin, SurfaceInteractionMixin):
    """Everything between a webhook arriving and a run being queued."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        router: SurfaceRouter,
        binder: ConversationBinder,
        surface_repository: SurfaceInstallationRepositoryPort,
        conversation_link_repository: SurfaceConversationLinkRepository,
        credential_resolver: SurfaceCredentialResolver,
        adapter_registry: SurfacePlatformAdapterRegistry | None = None,
        event_dedup_store: SurfaceEventDedupStorePort | None = None,
    ):
        self.uow = uow
        self.router = router
        self.binder = binder
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository
        self.credential_resolver = credential_resolver
        # The two process-wide ones keep their defaults: an adapter registry is
        # five stateless objects and the dedup store is a Redis client, so a
        # caller that has no opinion should not have to build either.
        self.adapter_registry = adapter_registry or SurfacePlatformAdapterRegistry()
        self.event_dedup_store = event_dedup_store or get_surface_event_dedup_store()

    def split_webhook_deliveries(
        self, request: SurfaceIngressRequest
    ) -> list[SurfaceIngressRequest]:
        """One webhook delivery, as the one-or-more inbound events it carries.

        Only the platform knows whether its webhook can batch, so the question
        is asked of the adapter; every adapter that cannot answers "itself" and
        this returns the request unchanged, which is the whole of the behaviour
        for four of the five platforms -- only WhatsApp overrides it.
        """
        if not isinstance(request, SurfacePlatformWebhookIngress):
            return [request]
        platform = platform_value_for_source(request.source)
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
