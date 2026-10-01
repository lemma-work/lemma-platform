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
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.created_groups import (
    created_group_surfaces,
    named_in_group,
)
from app.modules.agent_surfaces.services.group_log import GroupLog
from app.modules.agent_surfaces.services.surface_candidates import (
    admitted_surfaces,
    needs_mention_verification,
)


async def logged_and_addressed(
    uow: SqlAlchemyUnitOfWork,
    *,
    router,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
    platform: str,
) -> ParsedInboundSurfaceEvent:
    """The message, logged where the pod keeps the group's log, and read for
    whether it was put to the bot where the platform does not say."""
    # Before anything decides whether this is for the bot: most of a group is
    # not, and all of it is what the log is for. Committed at once so no write
    # is held open across the platform calls that follow.
    if await GroupLog(uow).note_inbound(list(surfaces), parsed):
        await commit_now(uow)
    if needs_mention_verification(platform, parsed, list(surfaces)):
        async with connection_released(uow.session):  # Telegram API
            parsed = await router.enrich_telegram_mention(parsed, surfaces[0])
    return await named_in_group(uow, parsed, list(surfaces))


async def own_bot_group_message(
    uow: SqlAlchemyUnitOfWork,
    *,
    router,
    surface_repository,
    links: SurfaceConversationLinkRepository,
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
        uow, router=router, surfaces=[surface], parsed=parsed, platform=platform
    )
    if not await admitted_surfaces([surface], parsed, links=links):
        return None
    return parsed
