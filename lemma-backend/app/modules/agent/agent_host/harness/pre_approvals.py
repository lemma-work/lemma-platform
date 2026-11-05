"""Carrying a late approval into the turn that resumes it.

An Agent Host turn parked on a native permission request is ended once it has
waited past its parked window (``parked_window.py``). The request itself is
dropped with that turn: when the person approves it afterwards, Lemma resumes
the conversation with a new run, and the agent -- told it was approved -- does
the thing again, which raises a fresh request on the new turn. Without help the
person is asked a second time for what they just allowed.

The resume run carries the approval instead (``AgentHostRunSpec.pre_approvals``),
and the host answers the first request for exactly the same tool and input
without asking. Only for a harness that says it understands that
(``AgentHostHarnessCapabilities.pre_approval``); any other host is sent none,
and asks again as before.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.agent_host.domain.host import (
    AgentHostHarnessCapabilities,
    AgentHostPreApproval,
)
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
)


def supports_pre_approval(capabilities: JsonObject) -> bool:
    """Whether a harness with these published capabilities honours them."""
    return AgentHostHarnessCapabilities.model_validate(capabilities).pre_approval


def pre_approvals_for(resumed: Pause | None) -> list[AgentHostPreApproval]:
    """The approval a resume run carries to its host, if it resumes one.

    Only for a run started to answer an Agent Host permission request, and only
    when the answer was yes: a denial is never carried, so the host still asks
    -- and the agent was already told not to.
    """
    if (
        resumed is None
        or resumed.kind is not PauseKind.HOST_PERMISSION
        or resumed.host_permission is None
        or not resumed.host_permission.tool_name
        or resumed.decision in (None, AgentRunApprovalDecision.DENY)
    ):
        return []
    return [
        AgentHostPreApproval(
            approval_id=resumed.tool_call_id,
            tool_name=resumed.host_permission.tool_name,
            input=dict(resumed.host_permission.input),
            decision=resumed.decision,
        )
    ]


async def host_pre_approves(uow: SqlAlchemyUnitOfWork, *, agent_run_id: UUID) -> bool:
    """Whether the harness this run went to carries an approval into a resume.

    Asked when a late approval is recorded, so what the agent is told matches
    what its next turn will do. A resume goes to the conversation's runtime,
    which is this run's harness unless somebody changes it in between -- and
    then the agent is asked again, which is the old behaviour, not a wrong one.
    """
    # Lazy: the repositories import the harness infrastructure, which imports
    # back through here.
    from app.modules.agent.agent_host.control.repository import (
        AgentHostRepository,
    )
    from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel

    lease = await uow.session.get(AgentHostRunLeaseModel, agent_run_id)
    if lease is None:
        return False
    harness = await AgentHostRepository(uow).get_harness(harness_id=lease.harness_id)
    return harness is not None and supports_pre_approval(harness.capabilities or {})


__all__ = ["host_pre_approves", "pre_approvals_for", "supports_pre_approval"]
