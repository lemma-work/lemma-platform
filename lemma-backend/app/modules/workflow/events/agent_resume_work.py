"""``workflow.resume_for_agent``: an AGENT step resumes when the agent's run ends.

Subscribed to the agent module's ``AgentRunFinished``, which is written in the
transaction that finished the run -- so a step can no longer miss its agent's
completion, which is what the ``workflow-agent-events`` group, its inbox and
the five-minute reconciliation sweep behind them were for.

FAST: one transaction under the item's row lock. The wait is looked up by its
external ref (the conversation) and must still be ACTIVE, so a duplicate, a late
item or a conversation still working (it paused on a person) is a no-op; the
run that ends the pause emits its own fact.
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.services.run_resume_service import RunResumeService

WORKFLOW_RESUME_FOR_AGENT = "workflow.resume_for_agent"

#: Conversation states that are not an answer yet: the step keeps waiting.
_STILL_WORKING = frozenset({"RUNNING", "STOP_REQUESTED", "WAITING"})


def is_workflow_step(fact: AgentRunFinished) -> bool:
    return fact.parent_kind == "workflow"


async def resume_for_agent(ctx: FastContext, payload: AgentRunFinished) -> None:
    if payload.conversation_status in _STILL_WORKING:
        return
    engine = build_workflow_engine(ctx.uow, work=ctx.work)
    await RunResumeService(engine).resume_for_agent_conversation(
        conversation_id=str(payload.conversation_id)
    )


def register_agent_resume_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=WORKFLOW_RESUME_FOR_AGENT,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=AgentRunFinished,
        retry=RetryPolicy(max_attempts=5),
    )(resume_for_agent)


register_agent_resume_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=WORKFLOW_RESUME_FOR_AGENT,
    cls=WorkClass.EVENTS,
    when=is_workflow_step,
    dedup=lambda fact: f"workflow-resume:agent:{fact.run_id}",
)


__all__ = [
    "WORKFLOW_RESUME_FOR_AGENT",
    "register_agent_resume_work",
    "resume_for_agent",
]
