"""One agent's id from its name, and its name from its id.

Replaces the `AgentServiceDep` in `app/composition/surface_agent.py`.
`agent_surfaces` had `AgentService` injected into five endpoints and used it for
these two lookups and nothing else -- one of them as
`agent_service.agent_repository.get(agent_id)`, a repository reached off a
service across a module boundary, wrapped in a bare `except Exception` because
the caller could not say what it might raise.

Neither takes an authorization argument, and that is not an omission: the
service call these replace was made without one too. The surface endpoints
authorize with `require_surface_agent_action` against the id this returns,
which is the check that has to happen anyway and the only one that knows what
the caller is about to do with the agent.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.infrastructure.models import AgentModel


async def agent_id_for_name(session, *, pod_id: UUID, name: str) -> UUID:
    """The id of the agent called ``name`` in this pod.

    Raises ``AgentNotFoundError`` when there is none, because every caller is
    resolving a name a person typed and needs it to come back as a 404 rather
    than as a surface silently bound to nothing.
    """
    agent_id = (
        await session.execute(
            select(AgentModel.id).where(
                AgentModel.pod_id == pod_id, AgentModel.name == name
            )
        )
    ).scalar_one_or_none()
    if agent_id is None:
        raise AgentNotFoundError(name)
    return agent_id


async def agent_name_for_id(session, agent_id: UUID) -> str | None:
    """This agent's display name, or ``None`` when there is no such agent.

    ``None`` rather than a raise: the caller is labelling a row in a listing,
    and a listing that 500s because one agent was deleted is worse than a
    listing with one unlabelled row.
    """
    return (
        await session.execute(select(AgentModel.name).where(AgentModel.id == agent_id))
    ).scalar_one_or_none()


__all__ = ["agent_id_for_name", "agent_name_for_id"]
