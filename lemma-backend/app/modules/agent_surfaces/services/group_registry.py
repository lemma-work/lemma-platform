"""The moment a pod comes to know a group: a member brought its bot in.

Being added to a group is the platform telling us the app's situation changed,
and on Telegram it names who did it. When that person is somebody who may edit
the bot -- the same bar the lifecycle path already sets before configuring
anything -- the pod adopts the group and they answer for it: its log starts,
and its people from outside the pod are answered, for the pod, from what is
Public (see ``domain/groups``).

A group that already has somebody answering for it keeps them. Adding the bot
again is not taking the group over; switching outsiders on from the bot's page
is where that choice is made on purpose.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.services.group_log import keeps_group_log

logger = get_logger(__name__)


async def adopt_joined_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedSurfaceLifecycleEvent,
    owner_user_id: UUID | None,
) -> bool | None:
    """Adopt the group the bot was just added to.

    None when the event was not one; otherwise whether the group is new to the
    pod, which is when the bot introduces itself (``group_hello``). Only for
    platforms whose groups the pod keeps itself: a Slack channel is configured
    through its own setup prompt, which this must not pre-empt.
    """
    channel_id = parsed.external_channel_id
    if (
        parsed.kind is not SurfaceLifecycleKind.JOINED_CHANNEL
        or not channel_id
        or not keeps_group_log(surface.surface_type.value)
    ):
        return None
    groups = SurfaceGroupRepository(uow.session)
    group, created = await groups.ensure_noting_creation(
        pod_id=surface.pod_id,
        surface_id=surface.id,
        platform=surface.surface_type.value,
        external_channel_id=channel_id,
        title=parsed.channel_title,
    )
    if group.owner_user_id is None and owner_user_id is not None:
        await groups.set_owner(group.id, owner_user_id)
    logger.info(
        "agent_surfaces.group_registry.group_adopted.observed",
        surface_id=str(surface.id),
        group_id=str(group.id),
        owner_set=group.owner_user_id is None and owner_user_id is not None,
    )
    return created


async def adopt_connected_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    surface: AgentSurfaceEntity,
    channel_id: str,
    owner_user_id: UUID | None,
) -> None:
    """A channel somebody just connected, as one of the pod's groups.

    For a platform whose bot is added to channels by configuration (Slack),
    not by joining. The person who connected it answers for its people outside
    the pod unless somebody already does.
    """
    if not channel_id:
        return
    groups = SurfaceGroupRepository(uow.session)
    group = await groups.ensure(
        pod_id=surface.pod_id,
        surface_id=surface.id,
        platform=surface.surface_type.value,
        external_channel_id=channel_id,
    )
    if group.owner_user_id is None and owner_user_id is not None:
        await groups.set_owner(group.id, owner_user_id)
