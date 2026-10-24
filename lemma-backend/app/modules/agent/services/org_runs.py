"""Agent runs on the ``org`` topic: which conversations are running now.

Each running conversation is told to who may read it -- its owner, holding
``agent.read`` on its agent (the pod, for the pod's own agent), the rule
``GET /pods/{pod}/conversations/{c}`` holds a reader to -- as
``conversation.running`` with ``running: true`` and the run, and again with
``running: false`` once it ends. A new run in the same conversation is told
as it starts.

Only top-level conversations: a subagent's runs are its parent's work, and a
sidebar draws no line for them. Read from the active runs alone -- a partial
index of the runs going now, everywhere -- so the read stays small however
long a pod's history.

A conversation's status moving says when to read (``run``,
``conversation_run_lifecycle.update_conversation_status``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.delegation import is_pod_default_agent
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.org_feeds import Audience, OrgItem, PodItems
from app.modules.agent.domain.value_objects import ACTIVE_AGENT_RUN_STATUSES
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel

#: The pod signal a conversation's status moving sends.
RUN_SIGNAL = "run"
#: Most running conversations one read returns, across the pods it covers.
MAX_RUNNING = 2000


def _running(
    pod_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    agent_id: UUID | None,
    owner: UUID,
) -> OrgItem:
    default = is_pod_default_agent(agent_id, pod_id=pod_id)
    agent = ResourceRef(
        resource_type=ResourceType.POD if default else ResourceType.AGENT,
        resource_id=pod_id if default else agent_id,
        pod_id=pod_id,
    )
    return OrgItem(
        audience=Audience(
            users=frozenset({owner}), permission=Permissions.AGENT_READ, resource=agent
        ),
        version=str(run_id),
        held={
            "type": "conversation.running",
            "conversation_id": str(conversation_id),
            "agent_run_id": str(run_id),
            "agent_id": str(agent_id) if agent_id is not None else None,
            "running": True,
        },
        ended={
            "type": "conversation.running",
            "conversation_id": str(conversation_id),
            "agent_run_id": str(run_id),
            "agent_id": str(agent_id) if agent_id is not None else None,
            "running": False,
        },
    )


class RunningConversations:
    name = "agent.running"
    kinds = frozenset({RUN_SIGNAL})

    async def read(self, pod_ids: Sequence[UUID]) -> Mapping[UUID, PodItems]:
        run, conversation = AgentRunModel, ConversationModel
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            rows = await uow.session.execute(
                select(
                    conversation.pod_id,
                    conversation.id,
                    run.id,
                    conversation.agent_id,
                    conversation.user_id,
                )
                .join(conversation, conversation.id == run.conversation_id)
                .where(
                    run.status.in_(
                        sorted(status.value for status in ACTIVE_AGENT_RUN_STATUSES)
                    ),
                    conversation.pod_id.in_(sorted(set(pod_ids))),
                    conversation.parent_id.is_(None),
                )
                .limit(MAX_RUNNING)
            )
        found: dict[UUID, dict[str, OrgItem]] = {}
        for pod_id, conversation_id, run_id, agent_id, owner in rows:
            found.setdefault(pod_id, {})[str(conversation_id)] = _running(
                pod_id, conversation_id, run_id, agent_id, owner
            )
        return found


__all__ = ["RUN_SIGNAL", "RunningConversations"]
