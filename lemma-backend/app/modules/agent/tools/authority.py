"""Whose authority an agent tool call runs with. One answer, in one place.

There are exactly three:

* **The contact's**, on a run answering somebody outside the pod (see
  ``domain/outsiders``). The context names the contact and is pinned to the
  pod: Public reads and the contact's own rows, nothing else. It comes first
  and nothing overrides it, not even an approval -- such a run never pauses to
  ask one, and ``user_id`` on it is the member who looks after the
  conversation, whose authority the contact must never reach through it.
* **The agent's own.** A delegated workload context: the agent's resource
  grants, intersected with what the person it works for may do. Anything
  beyond that -- a missing grant, a destructive action, an auth-required step
  -- is refused with a code the tool turns into ``needs_approval``.
* **The person's, lent for one approved call.** When a person approves a
  ``request_approval``, that exact call runs as them: their own permissions,
  nothing more, and the destructive-action gate is satisfied because a person
  approved it at the time (PS-ACCESS-021). Nothing is left behind -- the
  agent's next call is judged on its own grants again.

Every tool family used to build its own delegated context from
``deps.workload_id or DEFAULT_POD_AGENT_ID``, and the approval executor relied
on stripping ``workload_id`` to make those builders "act as the user". #597
changed how the builders recognise the pod's own assistant, and from then on an
approved call from a named agent was authorised as a placeholder agent with no
grants: every approval failed with ``MISSING_WORKLOAD_RESOURCE_GRANT``. Making
the approval an explicit value (``ApprovedExecution``) and deciding here is
what keeps that from recurring; ``test_tool_authority`` fails if any agent
module builds a delegated context itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.anonymous import build_outsider_context
from app.core.authorization.context import Context
from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.pod_principal import build_pod_context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.context import AgentContext


async def tool_authorization_context(
    uow: SqlAlchemyUnitOfWork, deps: AgentContext
) -> Context:
    """The authorization context one agent tool call runs under."""
    audience = deps.audience
    if audience.asking_pod_id is not None:
        # Another pod asking over a link: Public reads here, plus what this pod
        # granted that pod, and nothing else.
        return build_pod_context(
            session=uow.session,
            pod_id=deps.pod_id,
            organization_id=deps.org_id,
            asking_pod_id=audience.asking_pod_id,
        )
    if audience.contact_id is not None:
        # Named in the audit trail, and narrows contact-owned tables to their rows.
        return build_outsider_context(
            session=uow.session,
            pod_id=deps.pod_id,
            organization_id=deps.org_id,
            contact_id=audience.contact_id,
        )
    authorization = create_authorization_data_service(uow)
    approved = deps.approved_execution
    if approved is not None:
        return await authorization.build_user_context(
            user_id=approved.approver_user_id, pod_id=deps.pod_id
        )
    return await authorization.build_delegated_workload_context(
        user_id=deps.user_id,
        principal_type=workload_principal_type(deps),
        principal_id=getattr(deps, "workload_id", None) or DEFAULT_POD_AGENT_ID,
        pod_id=deps.pod_id,
        is_default_pod_agent=deps.is_pod_default_agent,
        delegation_scope=getattr(deps, "delegation_scope", None),
        delegation_actor_name=deps.agent_name,
        # Session approvals (APPROVE_FOR_SESSION) are keyed by conversation.
        delegation_session_id=str(deps.conversation_id),
    )


def workload_principal_type(deps: AgentContext) -> str:
    """``FUNCTION`` for a function's own delegated token, else ``AGENT``.

    Only the pod MCP mount builds a function's tool context: a function calling
    pod tools with its token must get the function's grants, not be read as an
    agent -- or, worse, as the pod's assistant acting with the person's roles.
    """
    if getattr(deps, "workload_type", None) == "function":
        return "FUNCTION"
    return "AGENT"


def workload_actor_id(deps: AgentContext) -> str:
    """The actor id the agent's delegated context carries.

    Session approvals are recorded and looked up under this key, so both sides
    must derive it the same way: from the agent's own id.
    """
    return f"agent:{getattr(deps, 'workload_id', None) or DEFAULT_POD_AGENT_ID}"


def conversation_workload_actor_id(*, agent_id: UUID | None, pod_id: UUID) -> str:
    """:func:`workload_actor_id` for a conversation, where there is no run.

    A conversation with no agent of its own belongs to the pod's assistant,
    whose id is its pod's. It used to fall back to ``DEFAULT_POD_AGENT_ID``,
    which no run carries, so the assistant could never find an approval the
    person gave it for the conversation.
    """
    return f"agent:{agent_id or pod_id}"


@dataclass(frozen=True, slots=True)
class WorkspacePrincipal:
    """Who a workspace session's token is minted for."""

    workload_type: str | None
    workload_id: UUID | None
    workload_name: str | None


def workspace_principal(deps: AgentContext) -> WorkspacePrincipal:
    """The sandbox half of :func:`tool_authorization_context`.

    A sandbox tool (``exec_command``, ``execute_python``) runs the ``lemma`` CLI
    with whatever token its session was minted with, so this is where an
    approved destructive command -- deleting a stale pod, say -- gets the
    person's authority: the session is minted for the user, with no workload.
    """
    if deps.approved_execution is not None:
        return WorkspacePrincipal(None, None, None)
    return WorkspacePrincipal(
        getattr(deps, "workload_type", None),
        getattr(deps, "workload_id", None),
        deps.agent_name,
    )
