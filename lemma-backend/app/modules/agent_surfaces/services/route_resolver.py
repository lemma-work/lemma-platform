"""Which agent answers an inbound event, and under what conversation key.

One of the three jobs the surface router used to hold alone, beside who sent
the event (`sender_resolver`) and which surface it belongs to
(`surface_selector`). Every shape is answered by the surface's own agent; what
differs is the conversation kind and key, and whether the message is for us at
all -- which a platform's capabilities answer rather than a list of platforms.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.routing_facts import InboundRoutingFacts

from uuid import UUID

from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import channels as agent_conversations
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfaceChannelRoute,
    is_group_dm,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_agent_id
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)


def _addressed(parsed: ParsedInboundSurfaceEvent) -> bool:
    """Whether a group message is for the bot: an @mention, or a reply in its thread."""
    return parsed.mentioned_agent or InboundRoutingFacts.of(parsed).is_thread_reply


def _channel_route_key(
    surface: AgentSurfaceEntity, parsed: ParsedInboundSurfaceEvent
) -> str | None:
    """The conversation key for a Slack or Teams channel message, or None.

    None means the bot does not answer here: the channel is not one it is wired
    to, or nobody addressed it. The route is an allow-list entry -- it says this
    channel is a place the bot answers -- and channels always require an @mention
    or a reply inside a bot thread, with no per-route opt-out.
    """
    route = surface.channel_route_for(
        channel_id=parsed.external_channel_id,
        channel_name=parsed.metadata.get("channel_name"),
    )
    if (
        route is None
        and surface.external_channel_id
        and surface.external_channel_id == parsed.external_channel_id
    ):
        # Surface bound directly to one channel without explicit routes.
        route = SurfaceChannelRoute(channel_id=surface.external_channel_id)
    if route is None and is_group_dm(parsed) and parsed.external_channel_id:
        # A group DM needs no route: being in it is the authorization.
        route = SurfaceChannelRoute(channel_id=parsed.external_channel_id)
    if route is None or not _addressed(parsed):
        return None
    return (
        f"channel:{parsed.external_channel_id}"
        if parsed.external_channel_id
        else f"channel-name:{route.channel_name}"
    )


class RouteResolver:
    """Names the agent and conversation an admitted event belongs to. Reads only."""

    def __init__(self, *, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def resolve_route(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ResolvedSurfaceRoute | None:
        """Which agent answers this event, and under what conversation key.

        Every shape is answered by the surface's own agent -- a channel route or
        a Telegram group says *where* the bot answers, never *who*. What differs
        is only the conversation kind and key, and whether the message is for us
        at all, so that is all this decides.
        """
        if parsed.is_dm or surface.surface_type.is_email:
            is_email = surface.surface_type.is_email
            return await self._route(
                surface,
                conversation_kind="EMAIL" if is_email else "DM",
                route_key="email" if is_email else "dm",
            )
        capabilities = get_platform_capabilities(surface.surface_type.value)
        if capabilities is None:
            return None
        if capabilities.group_membership_authorizes:
            # Being added to the group by an admin is the authorization, so there
            # is no per-group route config -- and a WhatsApp group is one the bot
            # created. The sender is still resolved and pod-membership checked
            # upstream, so only pod members, or outsiders where the group
            # welcomes them, can invoke it.
            if not _addressed(parsed):
                return None
            return await self._route(
                surface,
                conversation_kind="CHANNEL",
                route_key=f"channel:{parsed.external_channel_id}",
            )
        if capabilities.is_channel_capable:
            route_key = _channel_route_key(surface, parsed)
            if route_key is None:
                return None
            return await self._route(
                surface, conversation_kind="CHANNEL", route_key=route_key
            )
        return None

    async def _route(
        self,
        surface: AgentSurfaceEntity,
        *,
        conversation_kind: str,
        route_key: str,
    ) -> ResolvedSurfaceRoute:
        agent_id = surface.agent_id
        return ResolvedSurfaceRoute(
            pod_id=surface.pod_id,
            agent_id=agent_id,
            agent_name=await self._agent_name_for_agent_id(agent_id),
            agent_display_name=await self._agent_display_name(agent_id),
            conversation_kind=conversation_kind,
            route_key=route_key,
        )

    async def _agent_display_name(self, agent_id: UUID | None) -> str:
        """What this agent calls itself in front of a person.

        Not `agent.name`. The pod's own agent is stored as `pod_default`,
        which is an internal identifier -- it used to have no row at all, so
        every caller wrote `agent_name or "Lemma"` and the null did the work.
        Now that it has one, that expression puts `pod_default` on the message.

        The name it falls back to is `Lem`, not `Lemma`: the pod's agent is an
        actor with a name of its own, and the product is what the bot and the
        sending domain already say. See `agent_display_name`.
        """
        agent = (
            await agent_conversations.surface_agent_identity(self.uow, agent_id)
            if agent_id
            else None
        )
        if agent is None or agent.is_pod_default:
            return DEFAULT_RESPONDER_NAME
        return agent.name

    async def _agent_name_for_agent_id(
        self,
        agent_id: UUID | None,
    ) -> str | None:
        return await agent_name_for_agent_id(self.uow, agent_id)
