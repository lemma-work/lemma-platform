"""An installation transports the DM; an authorized personal pod answers it."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.provisioning import pod_default_agent_exists
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PersonalDMRoute,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    platform_binding_key,
)
from app.modules.agent_surfaces.services.surface_route_types import ResolvedSurfaceRoute
from app.modules.connectors.contracts.surfaces import account
from app.modules.identity.contracts.onboarding import active_chat_user
from app.modules.pod.contracts.agent_access import live_pod_organization_id


class PersonalRouteUnavailable(ValueError):
    pass


async def validate_personal_dm_route(
    uow: SqlAlchemyUnitOfWork, *, route_id: UUID, event: ParsedInboundSurfaceEvent
) -> PersonalDMRoute:
    route = await uow.session.get(PersonalDMRoute, route_id)
    if route is None or not event.is_dm:
        raise PersonalRouteUnavailable(
            "The personal conversation is no longer available"
        )
    surface = await SurfaceRepository(uow).get(route.installation_surface_id)
    if surface is None:
        raise PersonalRouteUnavailable("The installation is unavailable")
    if route.binding_key != platform_binding_key(
        event, surface.account_id or surface.id
    ):
        raise PersonalRouteUnavailable(
            "The sender does not own this personal conversation"
        )
    identity = await uow.session.scalar(
        select(VerifiedSurfaceIdentity).where(
            VerifiedSurfaceIdentity.binding_key == route.binding_key,
            VerifiedSurfaceIdentity.user_id == route.user_id,
            VerifiedSurfaceIdentity.revoked_at.is_(None),
        )
    )
    if (
        identity is None
        or surface.surface_type != event.platform
        or not surface.is_active
        or not surface.status.accepts_inbound_events()
        or not surface.matches_tenant(event.tenant_id)
    ):
        raise PersonalRouteUnavailable(
            "The installation or sender binding is unavailable"
        )
    await _require_installation_organization(uow, surface, route.pod_id)
    if await active_chat_user(uow, route.user_id) is None:
        raise PersonalRouteUnavailable("The account cannot chat")
    if (
        await SqlAlchemySurfaceRoutingResolutionAdapter(uow).get_pod_member_id(
            route.user_id, route.pod_id
        )
        is None
    ):
        raise PersonalRouteUnavailable("Ask your team administrator for access")
    if route.assistant_id != route.pod_id or not await pod_default_agent_exists(
        uow, pod_id=route.pod_id
    ):
        raise PersonalRouteUnavailable("The personal assistant is unavailable")
    return route


async def prepare_personal_dm_context(
    uow: SqlAlchemyUnitOfWork, *, route_id: UUID, event: ParsedInboundSurfaceEvent
) -> SurfaceChatContext:
    from app.modules.agent_surfaces.api.dependencies import get_surface_event_handler

    route = await validate_personal_dm_route(uow, route_id=route_id, event=event)
    installation = await SurfaceRepository(uow).get(route.installation_surface_id)
    user = await active_chat_user(uow, route.user_id)
    assert installation is not None and user is not None
    # This is a conversation-building snapshot only. The stored installation,
    # its credentials and every channel route remain owned by the source pod.
    destination = installation.model_copy(
        update={"pod_id": route.pod_id, "agent_id": route.assistant_id}
    )
    resolved = ResolvedSurfaceUser(
        internal_user_id=user.id,
        external_user_id=event.sender_external_user_id,
        email=str(user.email),
        phone=user.mobile_number,
        display_name=user.first_name,
    )
    assistant = ResolvedSurfaceRoute(
        agent_id=route.assistant_id,
        agent_name=DEFAULT_POD_AGENT_NAME,
        agent_display_name="Assistant",
        conversation_kind="DM",
        route_key=f"personal:{route.id}",
    )
    link, title = await get_surface_event_handler(uow)._get_or_create_conversation_link(
        surface=destination, parsed=event, resolved_user=resolved, route=assistant
    )
    return SurfaceChatContext(
        personal_dm_route_id=route.id,
        platform=installation.surface_type,
        surface_id=installation.id,
        surface_name=installation.name,
        surface_account_id=installation.account_id,
        surface_config=installation.config,
        pod_id=route.pod_id,
        agent_name=DEFAULT_POD_AGENT_NAME,
        agent_display_name="Assistant",
        conversation_id=link.conversation_id,
        user_id=user.id,
        message_text=event.message_text,
        message_user_id=user.id,
        message_external_user_id=event.sender_external_user_id,
        message_external_message_id=event.external_message_id,
        message_metadata=SurfaceMessageMetadata(
            surface_platform=event.platform,
            sender_email=str(user.email),
            conversation_kind="DM",
            event_metadata=event.metadata,
        ),
        event=event,
        created_conversation_title=title,
    )


async def _require_installation_organization(
    uow: SqlAlchemyUnitOfWork, surface: AgentSurfaceEntity, pod_id: UUID
) -> None:
    installation_org = await live_pod_organization_id(uow, surface.pod_id)
    target_org = await live_pod_organization_id(uow, pod_id)
    if installation_org is None or installation_org != target_org:
        raise PersonalRouteUnavailable("The personal pod is outside this installation")
    if surface.account_id is not None:
        connected = await account(uow, surface.account_id)
        if (
            connected is None
            or connected.organization_id != installation_org
            or connected.status != "CONNECTED"
        ):
            raise PersonalRouteUnavailable(
                "The installation credentials are unavailable"
            )
