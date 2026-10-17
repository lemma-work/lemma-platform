"""Continue a person's private chat when it arrives at a new address.

Split from the binder because it is its own question, asked only once the exact
thread key has missed: is there already a conversation with this person that
this message continues?
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    ThreadShape,
    thread_shape,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    dm_address_may_move,
)
from app.modules.agent_surfaces.services.contact_keepers import owned_or_moved
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)


def same_agent(left: UUID | None, right: UUID | None, *, pod_id: UUID) -> bool:
    """Whether two agent references name the same agent.

    Compared through `effective_agent_id`, because the two sides are written in
    different eras and the assistant has more than one spelling. A conversation
    now names it by the pod's own id; a route computed from surface configuration
    still names it by naming nobody. Raw, those two differ, and this reads "the
    agent changed" for a thread whose agent never changed -- cutting a fresh
    conversation and stranding the history the person can still see above the
    reply.
    """
    return effective_agent_id(left, pod_id=pod_id) == effective_agent_id(
        right, pod_id=pod_id
    )


async def adopt_earlier_dm_link(
    uow: SqlAlchemyUnitOfWork,
    *,
    surface_repository: SurfaceInstallationRepositoryPort,
    links: SurfaceConversationLinkRepository,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    resolved_user: ResolvedSurfaceUser,
    route: ResolvedSurfaceRoute,
    contact_id: UUID | None = None,
) -> AgentSurfaceConversationLink | None:
    """The link this person's private chat already has, under an older address.

    A private chat is one conversation to the person wherever it is
    delivered, but its link key names a delivery address: on WhatsApp the
    channel and the thread id both embed the number the message arrived on.
    A number reassigned within the pool, or another surface of the same pod
    taking the chat over, therefore misses the exact lookup and would open a
    fresh conversation with none of the history the person can still see.

    So, once the exact key has missed, look for the same person's latest
    private-chat link among this pod's surfaces and move it to the new
    address. Only a link whose conversation is this person's own, in the pod
    and with the agent this route names, is taken -- one number is not one
    person, and a conversation in another pod is another conversation. The
    reset and agent-change rules then run on the adopted link exactly as they
    would have on one found by the exact key.

    Private chats only: a channel or email thread is bounded by the platform,
    so there is no "same person, new address" to continue.
    """
    external_user_id = resolved_user.external_user_id
    user_id = resolved_user.internal_user_id
    if (
        external_user_id is None
        or user_id is None
        or thread_shape(route.conversation_kind) is not ThreadShape.MULTIPLEXED
        or not dm_address_may_move(surface.surface_type.value)
    ):
        return None
    platform = surface.surface_type.value
    # This pod's surfaces, plus the one that received the message: the pod
    # the conversation must be in is the route's, and the per-surface index
    # is what keeps the read cheap.
    siblings = await surface_repository.list_active_for_routing(
        platform, pod_ids=[route.pod_id]
    )
    surface_ids = {surface.id, *(sibling.id for sibling in siblings)}
    earlier = await links.find_latest_dm_link_for_person(
        platform=platform,
        external_user_id=external_user_id,
        surface_ids=sorted(surface_ids, key=str),
    )
    if earlier is None:
        return None
    conversation = await agent_conversations.surface_conversation(
        uow, earlier.conversation_id
    )
    if (
        conversation is None
        or conversation.pod_id != route.pod_id
        or not same_agent(conversation.agent_id, route.agent_id, pod_id=route.pod_id)
        or not await owned_or_moved(uow, conversation, user_id, contact_id=contact_id)
    ):
        return None
    return await links.rebind_thread_address(
        link=earlier,
        surface_id=surface.id,
        external_channel_id=parsed.external_channel_id,
        external_thread_id=parsed.external_thread_id,
    )
