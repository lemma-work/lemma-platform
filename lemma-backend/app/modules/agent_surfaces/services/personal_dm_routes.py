"""An installation transports the DM; an authorized personal pod answers it."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.provisioning import pod_default_agent_exists
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
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
) -> VerifiedSurfaceIdentity:
    """Three questions, asked in order, each able to refuse on its own.

    Is this identity still live and pointed somewhere; is the installation it
    points through still usable and still this sender's; and may this person
    still reach that pod. They were one long chain of conditions, which is how a
    reader loses track of which failure means what.
    """
    route = await uow.session.get(VerifiedSurfaceIdentity, route_id)
    if route is None or not event.is_dm or not route.is_routable:
        raise PersonalRouteUnavailable(
            "The personal conversation is no longer available"
        )
    # Narrowed rather than trusted from `is_routable`: the destination is three
    # nullable columns and a property cannot tell the type checker which of them
    # it looked at. It is also the honest reading -- a row missing any one of
    # them has nowhere to send a message.
    pod_id, surface_id = route.pod_id, route.installation_surface_id
    if pod_id is None or surface_id is None:
        raise PersonalRouteUnavailable(
            "The personal conversation is no longer available"
        )
    await _require_usable_installation(uow, route, event, surface_id, pod_id)
    await _require_live_access(uow, route, pod_id)
    return route


async def _require_usable_installation(
    uow: SqlAlchemyUnitOfWork,
    route: VerifiedSurfaceIdentity,
    event: ParsedInboundSurfaceEvent,
    surface_id: UUID,
    pod_id: UUID,
) -> None:
    """The installation still exists, still serves this platform, and is theirs."""
    surface = await SurfaceRepository(uow).get(surface_id)
    if surface is None:
        raise PersonalRouteUnavailable("The installation is unavailable")
    if route.binding_key != platform_binding_key(
        event, surface.account_id or surface.id
    ):
        raise PersonalRouteUnavailable(
            "The sender does not own this personal conversation"
        )
    if (
        surface.surface_type != event.platform
        or not surface.is_active
        or not surface.status.accepts_inbound_events()
        or not surface.matches_tenant(event.tenant_id)
    ):
        raise PersonalRouteUnavailable(
            "The installation or sender binding is unavailable"
        )
    await _require_installation_organization(uow, surface, pod_id)


async def _require_live_access(
    uow: SqlAlchemyUnitOfWork,
    route: VerifiedSurfaceIdentity,
    pod_id: UUID,
) -> None:
    """The account can still chat, is still in the pod, and the agent is there.

    Checked on every message rather than trusted from when the route was
    written: membership is exactly the thing that gets taken away.
    """
    if await active_chat_user(uow, route.user_id) is None:
        raise PersonalRouteUnavailable("The account cannot chat")
    if (
        await SqlAlchemySurfaceRoutingResolutionAdapter(uow).get_pod_member_id(
            route.user_id, pod_id
        )
        is None
    ):
        raise PersonalRouteUnavailable("Ask your team administrator for access")
    # The assistant is the pod's own, whose row id is the pod's id -- so there
    # is nothing to compare, only to check is still there.
    if not await pod_default_agent_exists(uow, pod_id=pod_id):
        raise PersonalRouteUnavailable("The personal assistant is unavailable")


class ConversationLinker(Protocol):
    """The one thing context-building needs from the ingress service.

    Named here rather than imported so this module keeps to the services layer.
    Reaching for `api.dependencies` to get the handler put the module's own
    FastAPI wiring on a service's import path, which is the wrong direction and
    the only real import cycle this change introduced.
    """

    async def _get_or_create_conversation_link(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
    ) -> tuple[AgentSurfaceConversationLink, str | None]: ...


async def prepare_personal_dm_context(
    uow: SqlAlchemyUnitOfWork,
    *,
    route_id: UUID,
    event: ParsedInboundSurfaceEvent,
    linker: ConversationLinker,
) -> SurfaceChatContext:
    route = await validate_personal_dm_route(uow, route_id=route_id, event=event)
    # `validate_personal_dm_route` refuses a row missing any of these; the
    # assert is what tells the type checker so.
    assert route.installation_surface_id is not None
    assert route.pod_id is not None
    installation = await SurfaceRepository(uow).get(route.installation_surface_id)
    user = await active_chat_user(uow, route.user_id)
    assert installation is not None and user is not None
    # This is a conversation-building snapshot only. The stored installation,
    # its credentials and every channel route remain owned by the source pod.
    destination = installation.model_copy(
        update={"pod_id": route.pod_id, "agent_id": route.pod_id}
    )
    resolved = ResolvedSurfaceUser(
        internal_user_id=user.id,
        external_user_id=event.sender_external_user_id,
        email=str(user.email),
        phone=user.mobile_number,
        display_name=user.first_name,
    )
    assistant = ResolvedSurfaceRoute(
        agent_id=route.pod_id,
        agent_name=DEFAULT_POD_AGENT_NAME,
        agent_display_name="Assistant",
        conversation_kind="DM",
        route_key=f"personal:{route.id}",
    )
    link, title = await linker._get_or_create_conversation_link(
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
