"""``agent.execute``, asked of a workflow run's person before an agent step.

A function step has always re-checked ``function.execute`` as the run's person
at dispatch. An agent step did not: anyone who could start a workflow could
have its agent node run an agent they may not run themselves -- a RESTRICTED
one, or one in a pod they have since left -- because the conversation was
started on the run's say-so alone.

Asked as the run's person *as of now*, built fresh like the function step's
check, not with whatever context drove this advance: a form submitted by its
assignee or an agent's completion resuming the run is not who the run is for.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.delegation import is_pod_default_agent
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.agents import agent_id_for_name
from app.modules.workflow.domain.ports import AgentAccessPort


class AgentExecuteCheck(AgentAccessPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def require_execute(
        self, *, agent_name: str, pod_id: UUID, user_id: UUID
    ) -> None:
        session = self._uow.session
        # Raises AgentNotFoundError for a name the pod no longer has, which
        # fails the step exactly as starting it would have.
        agent_id = await agent_id_for_name(session, pod_id=pod_id, name=agent_name)
        ctx = await AuthorizationDataService(session).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        await ctx.require(Permissions.AGENT_EXECUTE, _agent_ref(agent_id, pod_id))


def _agent_ref(agent_id: UUID, pod_id: UUID) -> ResourceRef:
    """The pod's own assistant is asked about pod-scoped; grants match on (type, id)."""
    if is_pod_default_agent(agent_id, pod_id=pod_id):
        return ResourceRef.pod(pod_id)
    return ResourceRef(
        resource_type=ResourceType.AGENT, resource_id=agent_id, pod_id=pod_id
    )


__all__ = ["AgentExecuteCheck"]
