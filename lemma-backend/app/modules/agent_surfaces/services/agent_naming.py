"""Whose name a surface message goes out under.

Free functions rather than methods because two unrelated objects need the same
answer and neither owns it: routing names the agent while it builds an inbound
context, and delivery names it when notification delivery opens a conversation
for a recipient.

It used to be a ``SurfaceRoutingMixin`` method that ``NotificationEgress``
reached through ``self.egress`` -- which is why
:class:`SurfaceNotificationEgressPort`, a port describing *sending*, requires a
routing method. That worked only because both mixins were flattened onto one
object. Once they are separate objects the shared answer has to live somewhere
they can both see, and this is it.

The assistant's real name, not its display name: the callers feed it to
``create_conversation``, which resolves it back to a row. Anything a person
reads wants ``agent_display_name`` instead.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import agents as agent_directory
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity


async def agent_name_for_agent_id(
    uow: SqlAlchemyUnitOfWork, agent_id: UUID | None
) -> str | None:
    if agent_id is None:
        return None
    return await agent_directory.agent_name_for_id(uow.session, agent_id)


async def agent_name_for_surface(
    uow: SqlAlchemyUnitOfWork, surface: AgentSurfaceEntity
) -> str | None:
    return await agent_name_for_agent_id(uow, surface.agent_id)
