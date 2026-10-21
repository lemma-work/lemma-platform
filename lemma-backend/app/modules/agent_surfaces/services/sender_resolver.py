"""Who sent an inbound event, as a Lemma user where one can be found.

One of the three jobs the surface router used to hold alone, beside which
surface the event belongs to (`surface_selector`) and which agent answers it
(`route_resolver`).
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    echoes_own_messages,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.agent_surfaces.services.slack_workspace_people import slack_guest

logger = get_logger(__name__)


def is_self_addressed(
    *, surface: AgentSurfaceEntity, parsed: ParsedInboundSurfaceEvent
) -> bool:
    """Is this event the surface hearing its own message come back?

    Two shapes of the same echo: email, where the surface's own address is the
    sender, and a platform that delivers the bot's posts back keyed on the
    bot's user id (``echoes_own_messages``). Answering either would answer the
    agent's own reply, forever.
    """
    if surface.surface_type.is_email:
        surface_email = str(surface.surface_identity_email or "").strip().lower()
        sender_email = (
            str(parsed.sender_email or parsed.sender_external_user_id or "")
            .strip()
            .lower()
        )
        return bool(surface_email and sender_email and surface_email == sender_email)
    if not echoes_own_messages(surface.surface_type):
        return False
    sender_id = parsed.sender_external_user_id
    return bool(sender_id and surface.surface_identity_id == sender_id)


class SenderResolver:
    """The sender's identity, and whether they belong to a surface's pod."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        pod_membership_port: SurfacePodMembershipPort,
        identity_service: SurfaceIdentityResolutionService,
    ) -> None:
        self.uow = uow
        self.pod_membership_port = pod_membership_port
        self.identity_service = identity_service

    async def resolve_sender(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedInboundSurfaceEvent,
        credentials: SurfaceCredentials,
        installation_id: UUID | None = None,
    ) -> ResolvedSurfaceUser:
        profile_unavailable = False
        try:
            async with connection_released(self.uow.session):
                sender_profile = await adapter.fetch_sender_profile(
                    credentials=credentials,
                    event=parsed,
                )
        except platform_transport_errors():
            # Without the profile there is no email to match, so a member reads
            # as a stranger -- for this message only: the miss is not cached.
            logger.warning(
                "agent_surfaces.surface_router.sender_profile_unavailable.degraded",
                platform=str(parsed.platform),
                exc_info=True,
            )
            sender_profile, profile_unavailable = None, True
        resolved = await self.identity_service.resolve(
            event=parsed,
            sender_profile=sender_profile,
            installation_id=installation_id,
            profile_unavailable=profile_unavailable,
        )
        if parsed.platform is SurfacePlatform.SLACK:
            # Read off this message's profile however resolution answered: a
            # cached answer says who they are, never what Slack lets them do.
            resolved.is_guest = slack_guest(sender_profile)
        return await self._hydrate_resolved_user(resolved)

    async def _hydrate_resolved_user(
        self,
        resolved_user: ResolvedSurfaceUser,
    ) -> ResolvedSurfaceUser:
        if resolved_user.internal_user_id is not None and not resolved_user.email:
            resolved_user.email = await self.pod_membership_port.get_user_email(
                resolved_user.internal_user_id
            )
        return resolved_user

    async def matches_user(
        self,
        surfaces: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
    ) -> AgentSurfaceEntity | None:
        """Return the first surface whose pod the resolved user is a member of."""
        if resolved_user is None or resolved_user.internal_user_id is None:
            return None

        user_pod_ids = set(
            await self.pod_membership_port.get_user_pod_ids(
                resolved_user.internal_user_id
            )
        )
        for surface in surfaces:
            if surface.pod_id in user_pod_ids:
                return surface
        return None

    def is_self_addressed(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> bool:
        return is_self_addressed(surface=surface, parsed=parsed)
