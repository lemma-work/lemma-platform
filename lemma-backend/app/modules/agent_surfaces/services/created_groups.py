"""A group the bot created belongs to the pod that asked for it.

WhatsApp is the case (``bot_creates_groups``): a business number cannot be
added to a group, only create one, and it creates it for one pod. Every pod on
a shared number sees every message that number receives, so without this a
group message would be routed like a private one -- to whichever of the
sender's pods the routing precedence picks -- and a member of two pods could
be answered in the group by the one that has nothing to do with it.

So a group message is first narrowed to the surfaces that know the group, and a
group no surface here knows is dropped: nobody asked for it, so nobody answers
in it.

WhatsApp also marks no mention. The parser reads an ``@`` of the business
number and a reply to the bot's own message; being named in words -- "Sales,
can you..." -- needs the names people use for the bot, which only the surface
knows (``group_names``), so it is read here once the surface is.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.addressing import names_the_agent
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    bot_creates_groups_on,
)
from app.modules.agent_surfaces.services.group_names import names_people_use


async def created_group_surfaces(
    uow: SqlAlchemyUnitOfWork,
    surfaces: SurfaceInstallationRepositoryPort,
    *,
    platform: str,
    parsed: ParsedInboundSurfaceEvent,
    receiver_surface_ids: Collection[UUID] | None,
) -> list[AgentSurfaceEntity] | None:
    """The surfaces a message in a created group belongs to.

    None where this does not apply -- a private message, or a platform whose
    bot is added to groups -- and the caller finds candidates as it always has.
    An empty list is an answer, not an absence of one: a group nobody here
    created, which nobody answers.
    """
    if parsed.is_dm or not parsed.external_channel_id:
        return None
    if not bot_creates_groups_on(platform):
        return None
    surface_ids = await SurfaceGroupRepository(uow.session).surface_ids_for_channel(
        platform=platform, external_channel_id=parsed.external_channel_id
    )
    if receiver_surface_ids is not None:
        allowed = set(receiver_surface_ids)
        surface_ids = [
            surface_id for surface_id in surface_ids if surface_id in allowed
        ]
    if not surface_ids:
        return []
    return await surfaces.list_active_for_routing(platform, surface_ids=surface_ids)


async def named_in_group(
    uow: SqlAlchemyUnitOfWork,
    parsed: ParsedInboundSurfaceEvent,
    surfaces: Sequence[AgentSurfaceEntity],
) -> ParsedInboundSurfaceEvent:
    """The message, marked as put to the bot when a line names its agent.

    Asked before admission, which drops a group message nobody addressed, and
    only where the platform marks no mention of its own.
    """
    if parsed.is_dm or parsed.mentioned_agent or not surfaces:
        return parsed
    if not bot_creates_groups_on(parsed.platform.value):
        return parsed
    for surface in surfaces:
        for name in await names_people_use(uow, surface):
            if names_the_agent(parsed.message_text, name):
                return parsed.model_copy(
                    update={"mentioned_agent": True, "should_start_conversation": True}
                )
    return parsed
