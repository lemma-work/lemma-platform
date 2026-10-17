"""What every group message goes through before anything decides who answers it.

Shared by both ways a message arrives: a platform's own webhook, where one
delivery may belong to any of several pods' bots, and a bot's own webhook
(``/surfaces/{id}/webhook`` -- a pod's own Telegram bot, its own WhatsApp
number), where it can belong to that bot alone. Each kept its own copy of these
steps once, and the second copy was never written: a group on an own bot went
unlogged, unaddressed by name, and answered whether or not anybody spoke to it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.infrastructure.db.session_uow import commit_now
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.services.channel_places import ChannelSeats
from app.modules.agent_surfaces.services.created_groups import (
    created_group_surfaces,
    named_in_group,
)
from app.modules.agent_surfaces.services.group_log import GroupLog
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
    arrival_number,
)
from app.modules.agent_surfaces.services.surface_candidates import admitted_surfaces


async def logged_and_addressed(
    uow: SqlAlchemyUnitOfWork,
    *,
    adapter: SurfacePlatformAdapterPort,
    credential_resolver: SurfaceCredentialResolver,
    seats: ChannelSeats,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
) -> ParsedInboundSurfaceEvent:
    """The message, logged where the pod keeps the group's log, and read for
    whether it was put to the bot where the platform does not say."""
    # Before anything decides whether this is for the bot: most of a group is
    # not, and all of it is what the log is for. Committed at once so no write
    # is held open across the platform calls that follow.
    if await GroupLog(uow, seats=seats).note_inbound(list(surfaces), parsed):
        await commit_now(uow)
    if surfaces and adapter.mention_is_provisional(parsed):
        parsed = await _settled_mention(
            uow,
            adapter=adapter,
            credential_resolver=credential_resolver,
            surfaces=surfaces,
            parsed=parsed,
        )
    return await named_in_group(uow, parsed, list(surfaces))


async def _settled_mention(
    uow: SqlAlchemyUnitOfWork,
    *,
    adapter: SurfacePlatformAdapterPort,
    credential_resolver: SurfaceCredentialResolver,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
) -> ParsedInboundSurfaceEvent:
    """Whether the mention was this bot, asked of the platform's adapter.

    The candidates share one bot -- they were narrowed to the receiver that
    took delivery -- so the first answers for all of them, and the bot's id is
    whichever of them recorded it.
    """
    bot_user_id = next(
        (s.surface_identity_id for s in surfaces if s.surface_identity_id), None
    )
    credentials = await credential_resolver.for_surface(
        surfaces[0], arrived_on=arrival_number(parsed)
    )
    async with connection_released(uow.session):  # the platform call
        return await adapter.settle_mention(
            credentials=credentials, event=parsed, bot_user_id=bot_user_id
        )


async def own_bot_group_message(
    uow: SqlAlchemyUnitOfWork,
    *,
    adapter: SurfacePlatformAdapterPort,
    credential_resolver: SurfaceCredentialResolver,
    surface_repository,
    seats: ChannelSeats,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
) -> ParsedInboundSurfaceEvent | None:
    """A group message to a bot's own webhook, readied for routing; None to drop.

    The same as a shared bot's: a group this number did not create is nobody's
    here, and a message nobody put to the bot is logged and left alone.
    """
    platform = surface.surface_type.value
    created = await created_group_surfaces(
        uow,
        surface_repository,
        platform=platform,
        parsed=parsed,
        receiver_surface_ids=[surface.id],
    )
    if created == []:
        return None
    parsed = await logged_and_addressed(
        uow,
        adapter=adapter,
        credential_resolver=credential_resolver,
        seats=seats,
        surfaces=[surface],
        parsed=parsed,
    )
    if not await admitted_surfaces([surface], parsed, seats=seats):
        return None
    return parsed
