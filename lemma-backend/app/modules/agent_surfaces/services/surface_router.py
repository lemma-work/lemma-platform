"""The three questions an inbound event is asked, behind one name.

Who sent it (`sender_resolver`), which surface it belongs to
(`surface_selector`, which also documents the precedence), and which agent
answers it under what conversation key (`route_resolver`). They were one class
that held all three jobs and a Telegram API call besides; each is its own
object now, and this composes them for the callers -- inbound, interactions,
onboarding -- that ask more than one.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.agent_surfaces.services.route_resolver import RouteResolver
from app.modules.agent_surfaces.services.sender_resolver import SenderResolver
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.agent_surfaces.services.surface_selector import SurfaceSelector


class SurfaceRouter:
    """Sender, surface and route for an inbound event, by delegation."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        pod_membership_port: SurfacePodMembershipPort,
        identity_service: SurfaceIdentityResolutionService,
        credential_resolver: SurfaceCredentialResolver,
        seats: ChannelSeats | None = None,
    ) -> None:
        self.uow = uow
        self.surface_repository = surface_repository
        self.identity_service = identity_service
        self.credential_resolver = credential_resolver
        self.senders = SenderResolver(
            uow=uow,
            pod_membership_port=pod_membership_port,
            identity_service=identity_service,
        )
        self.seats = seats or ChannelSeatsInDatabase(uow)
        self.selector = SurfaceSelector(
            uow=uow,
            seats=self.seats,
            surface_repository=surface_repository,
            pod_membership_port=pod_membership_port,
        )
        self.routes = RouteResolver(uow=uow)

    @property
    def pod_membership_port(self) -> SurfacePodMembershipPort:
        """Membership, which both who-sent-it and which-surface ask about."""
        return self.senders.pod_membership_port

    @pod_membership_port.setter
    def pod_membership_port(self, port: SurfacePodMembershipPort) -> None:
        self.senders.pod_membership_port = port
        self.selector.pod_membership_port = port

    async def resolve_sender(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedInboundSurfaceEvent,
        credentials: SurfaceCredentials,
        installation_id: UUID | None = None,
    ) -> ResolvedSurfaceUser:
        return await self.senders.resolve_sender(
            adapter=adapter,
            parsed=parsed,
            credentials=credentials,
            installation_id=installation_id,
        )

    async def matches_user(
        self,
        surfaces: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
    ) -> AgentSurfaceEntity | None:
        return await self.senders.matches_user(surfaces, resolved_user)

    def is_self_addressed(
        self, *, surface: AgentSurfaceEntity, parsed: ParsedInboundSurfaceEvent
    ) -> bool:
        return self.senders.is_self_addressed(surface=surface, parsed=parsed)

    async def select_surface(
        self,
        *,
        candidates: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
        parsed: ParsedInboundSurfaceEvent,
        platform: str,
        user_pod_ids: set[UUID] | None = None,
    ) -> AgentSurfaceEntity | None:
        return await self.selector.select_surface(
            candidates=candidates,
            resolved_user=resolved_user,
            parsed=parsed,
            platform=platform,
            user_pod_ids=user_pod_ids,
        )

    async def reachable_surface(
        self,
        *,
        candidates: list[AgentSurfaceEntity],
        user_id: UUID,
        platform: SurfacePlatform,
        parsed: ParsedInboundSurfaceEvent,
    ) -> AgentSurfaceEntity | None:
        return await self.selector.reachable_surface(
            candidates=candidates, user_id=user_id, platform=platform, parsed=parsed
        )

    async def deliverable_default(
        self,
        *,
        user_id: UUID,
        parsed: ParsedInboundSurfaceEvent,
        receiver_surface_ids: list[UUID] | None,
        system_credentials_only: bool = False,
    ) -> AgentSurfaceEntity | None:
        return await self.selector.deliverable_default(
            user_id=user_id,
            parsed=parsed,
            receiver_surface_ids=receiver_surface_ids,
            system_credentials_only=system_credentials_only,
        )

    @staticmethod
    def scoped_fallback_surface(
        request: SurfacePlatformWebhookIngress,
        surfaces: list[AgentSurfaceEntity],
    ) -> AgentSurfaceEntity | None:
        return SurfaceSelector.scoped_fallback_surface(request, surfaces)

    async def resolve_route(
        self, *, surface: AgentSurfaceEntity, parsed: ParsedInboundSurfaceEvent
    ) -> ResolvedSurfaceRoute | None:
        return await self.routes.resolve_route(surface=surface, parsed=parsed)
