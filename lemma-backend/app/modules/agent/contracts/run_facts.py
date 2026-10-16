"""What the agent module announces when a run ends.

The transaction that makes a run terminal emits ``AgentRunFinished``, so every
follow-up -- the reply to the surface, the queued messages, the parent waiting
on a child, the workflow step, the schedule outcome, the pod-to-pod answer --
gets its work item committed with the outcome, or not at all. Subscribers
register at wiring time (``facts.subscribe``) and decide with a pure predicate
on these fields, so none of them has to read the agent module's tables to
learn whether the run is theirs.

A submodule rather than ``contracts/__init__``: a subscriber registering at
wiring time should not pay for the rest of the agent contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.core.infrastructure.work import Fact, pod_namespace

#: A run's terminal status.
AgentRunOutcome = Literal["COMPLETED", "FAILED", "STOPPED"]
#: The conversation's status as of the same commit. ``WAITING`` means the run
#: ended on a pause (an approval, a ``wait_for``), not with an answer.
AgentConversationStatus = Literal[
    "RUNNING", "STOP_REQUESTED", "WAITING", "COMPLETED", "FAILED", "STOPPED"
]
#: Who is waiting on this run, if anybody: a workflow's AGENT step, a parent
#: agent's SUBAGENT wait, or a pod that asked this one a question.
AgentRunParentKind = Literal["workflow", "agent", "pod_ask"]
#: What started the run. Runs started by automation run in the automation
#: class, so a burst of them never queues a person's chat.
AgentRunOrigin = Literal[
    "chat", "surface", "schedule", "workflow", "trigger", "subagent", "pod_ask"
]


class AgentRunFinished(Fact):
    run_id: UUID
    conversation_id: UUID
    pod_id: UUID
    organization_id: UUID | None = None
    agent_id: UUID | None = None
    user_id: UUID | None = None
    status: AgentRunOutcome
    conversation_status: AgentConversationStatus
    parent_kind: AgentRunParentKind | None = None
    #: The waiter's own id for ``parent_kind`` (a workflow wait id, a parent
    #: run id, a pod-ask id), so its subscriber can key on it.
    parent_ref: str | None = None
    origin: AgentRunOrigin
    #: Whether the conversation has a surface to answer on.
    has_reply_target: bool = False
    started_at: datetime | None = None

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = [
    "AgentConversationStatus",
    "AgentRunFinished",
    "AgentRunOrigin",
    "AgentRunOutcome",
    "AgentRunParentKind",
]
