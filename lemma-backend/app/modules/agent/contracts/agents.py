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

from collections.abc import Iterable
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.infrastructure.repositories import AgentRepository


async def agent_id_for_name(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, name: str
) -> UUID:
    """The id of the agent called ``name`` in this pod.

    Raises ``AgentNotFoundError`` when there is none, because every caller is
    resolving a name a person typed and needs it to come back as a 404 rather
    than as a surface silently bound to nothing.
    """
    agent_id = await AgentRepository(uow).id_for_name(pod_id=pod_id, name=name)
    if agent_id is None:
        raise AgentNotFoundError(name)
    return agent_id


async def agent_name_for_id(uow: SqlAlchemyUnitOfWork, agent_id: UUID) -> str | None:
    """This agent's display name, or ``None`` when there is no such agent --
    a listing with one unlabelled row rather than one that 500s."""
    return await AgentRepository(uow).name_for_id(agent_id)


async def agent_name_in_pod(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, agent_id: UUID
) -> str:
    """The stored name of this pod's agent, or ``AgentNotFoundError`` when the
    pod has no agent with that id."""
    name = await AgentRepository(uow).name_for_id(agent_id, pod_id=pod_id)
    if name is None:
        raise AgentNotFoundError(str(agent_id))
    return name


async def agent_names_for_ids(
    uow: SqlAlchemyUnitOfWork, agent_ids: Iterable[UUID | None]
) -> dict[UUID, str]:
    """Display names for many agents at once, keyed by id; ids with no agent
    are absent, as above."""
    return await AgentRepository(uow).names_for_ids(
        agent_id for agent_id in agent_ids if agent_id is not None
    )


__all__ = [
    "agent_id_for_name",
    "agent_name_for_id",
    "agent_name_in_pod",
    "agent_names_for_ids",
]
