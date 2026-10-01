"""What people call a pod's bot, as the app shows it to them.

A pod's own assistant is stored as ``pod_default`` and shown in the app as the
pod itself -- "Sales" -- so that is the name people in a group use for it:
"Sales, can you send the price list?". Chat surfaces have long signed it
``Lem`` instead (``DEFAULT_RESPONDER_NAME``), and older chats still address it
that way. A named agent is called by its own name everywhere.

So a group message is put to the bot when it names any of these, and the bot
introduces itself by the first.
"""

from __future__ import annotations

from app.core.authorization.delegation import (
    DEFAULT_RESPONDER_NAME,
    agent_display_name,
    is_pod_default_agent,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.addressing import names_the_agent
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_surface
from app.modules.agent_surfaces.services.pod_name_lookup import pod_name_for


def _answers_as_the_pod(surface: AgentSurfaceEntity) -> bool:
    return surface.agent_id is None or is_pod_default_agent(
        surface.agent_id, pod_id=surface.pod_id
    )


async def visible_bot_name(
    uow: SqlAlchemyUnitOfWork, surface: AgentSurfaceEntity
) -> str:
    """The name the app shows for this surface's bot."""
    if _answers_as_the_pod(surface):
        return await pod_name_for(uow, surface.pod_id) or DEFAULT_RESPONDER_NAME
    return agent_display_name(await agent_name_for_surface(uow, surface))


async def names_people_use(
    uow: SqlAlchemyUnitOfWork, surface: AgentSurfaceEntity
) -> list[str]:
    """Every name a person in a group might call this bot by, shown one first."""
    names = [await visible_bot_name(uow, surface)]
    if _answers_as_the_pod(surface):
        names.append(DEFAULT_RESPONDER_NAME)
    return list(dict.fromkeys(name for name in names if name and name.strip()))


async def spoken_to_by_name(
    uow: SqlAlchemyUnitOfWork, surface: AgentSurfaceEntity, text: str | None
) -> bool:
    """Whether a line of ``text`` speaks to this surface's bot by any of its names."""
    return any(
        names_the_agent(text or "", name)
        for name in await names_people_use(uow, surface)
    )
