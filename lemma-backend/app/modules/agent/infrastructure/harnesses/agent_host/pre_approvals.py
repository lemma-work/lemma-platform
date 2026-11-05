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

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AgentHostHarnessCapabilities,
    AgentHostPreApproval,
)
from app.modules.agent.domain.agent_host_permissions import (
    AGENT_HOST_PERMISSION_KEY,
    is_permission_approval_tool_call_id,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    MessageKind,
)


def supports_pre_approval(capabilities: JsonObject) -> bool:
    """Whether a harness with these published capabilities honours them."""
    return AgentHostHarnessCapabilities.model_validate(capabilities).pre_approval


def pre_approvals_for(
    messages: Sequence[Message], *, resumed_tool_call_id: str | None
) -> list[AgentHostPreApproval]:
    """The approval a resume run carries to its host, if it resumes one.

    Only for a run started to answer an Agent Host permission request, and only
    when the answer was yes: a denial is never carried, so the host still asks
    -- and the agent was already told not to.
    """
    if resumed_tool_call_id is None or not is_permission_approval_tool_call_id(
        resumed_tool_call_id
    ):
        return []
    call = _message(messages, MessageKind.TOOL_CALL, resumed_tool_call_id)
    answer = _message(messages, MessageKind.TOOL_RETURN, resumed_tool_call_id)
    if call is None or answer is None or not isinstance(call.tool_args, dict):
        return []
    marker = call.tool_args.get(AGENT_HOST_PERMISSION_KEY)
    tool_name = call.tool_args.get("tool_name")
    decision = _decision(answer.tool_result)
    if (
        not isinstance(marker, dict)
        or not isinstance(tool_name, str)
        or not tool_name
        or decision is None
        or decision == AgentRunApprovalDecision.DENY
    ):
        return []
    tool_input = marker.get("input")
    return [
        AgentHostPreApproval(
            approval_id=resumed_tool_call_id,
            tool_name=tool_name,
            input=tool_input if isinstance(tool_input, dict) else {},
            decision=decision,
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
    from app.modules.agent.infrastructure.agent_host.repository import (
        AgentHostRepository,
    )
    from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel

    lease = await uow.session.get(AgentHostRunLeaseModel, agent_run_id)
    if lease is None:
        return False
    harness = await AgentHostRepository(uow).get_harness(harness_id=lease.harness_id)
    return harness is not None and supports_pre_approval(harness.capabilities or {})


def _message(
    messages: Sequence[Message], kind: MessageKind, tool_call_id: str
) -> Message | None:
    return next(
        (
            message
            for message in reversed(messages)
            if message.kind == kind and message.tool_call_id == tool_call_id
        ),
        None,
    )


def _decision(result: object) -> AgentRunApprovalDecision | None:
    if not isinstance(result, dict):
        return None
    raw = result.get("decision")
    if not isinstance(raw, str):
        return None
    try:
        return AgentRunApprovalDecision(raw)
    except ValueError:
        return None


__all__ = ["host_pre_approves", "pre_approvals_for", "supports_pre_approval"]
