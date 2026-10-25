"""Closing a pause the person moved past without answering.

A new message, a follow-up turn and Stop all reach a conversation that may
still be sitting on an ``ask_user`` / ``request_approval`` nobody decided. Each
closes it the same way -- a DENY recorded on the person's behalf and the return
the next run replays -- and that is this module.

Split from ``conversation_approvals`` because it is the one resolution nobody
asked for: everything else there records a decision a person made.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES
from app.modules.agent.infrastructure.db.approvals import ApprovalDecisions
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    MessageDraft,
)
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)

logger = get_logger(__name__)

#: On the DENY recorded for a pause somebody moved past: nobody decided it.
SUPERSEDED_KEY = "superseded_by_new_message"


async def supersede_stale_pending_interactions(
    *,
    pausing_calls: Pauses,
    approval_decisions: ApprovalDecisions,
    messages: Messages,
    resume_returns: ResumeToolReturnBuilder,
    conversation: Conversation,
    user_id: UUID,
) -> list[Message]:
    """Auto-deny any ask_user/request_approval call left unresolved from an
    earlier WAITING pause, before starting a fresh run for a new message.

    This only ever runs when no run is currently active (the caller checked
    that already), so any pausing call still unresolved at this point belongs
    to a run that already finished — the user moved on without answering it.
    Always DENY, never approve: this is a safety fallback synthesizing a
    response on the user's behalf, not a real decision, so a request_approval
    must never auto-execute here. Writes ride the caller's transaction (no
    commit here) so they land atomically with the new run/message it creates;
    the caller is responsible for publishing the returned messages once that
    transaction actually commits. That also rules out side effects Lemma
    could not take back on a rollback, which is why an Agent Host permission
    is not delivered from here.
    """
    stale = await pausing_calls.pausing_calls_awaiting_a_decision(
        conversation_id=conversation.id,
        pausing_tool_names=PAUSING_TOOL_NAMES,
    )
    synthesized_returns: list[Message] = []
    for message in stale:
        saved_return = await _deny_stale_pending_interaction(
            pausing_calls=pausing_calls,
            approval_decisions=approval_decisions,
            messages=messages,
            resume_returns=resume_returns,
            conversation=conversation,
            message=message,
            user_id=user_id,
        )
        if saved_return is not None:
            synthesized_returns.append(saved_return)
    return synthesized_returns


async def _deny_stale_pending_interaction(
    *,
    pausing_calls: Pauses,
    approval_decisions: ApprovalDecisions,
    messages: Messages,
    resume_returns: ResumeToolReturnBuilder,
    conversation: Conversation,
    message: Message,
    user_id: UUID,
) -> Message | None:
    # Re-established rather than assumed: the caller's filter proves the
    # call id and the tool name, but none of that survives being passed as a
    # `Message`. A call missing any of the three cannot have a faithful
    # return synthesized for it.
    tool_call_id = message.tool_call_id
    tool_name = message.tool_name
    agent_run_id = message.agent_run_id
    if tool_call_id is None or tool_name is None or agent_run_id is None:
        # Only the run id is genuinely unproven here, and losing it is not
        # cosmetic: the pause stays unresolved, so the next run rebuilds a
        # history still showing an open question nobody can now answer.
        # Said out loud because the alternative is a conversation that
        # quietly stops making sense.
        logger.warning(
            "agent.conversation_approvals.pause_without_run_skipped.degraded",
            conversation_id=str(conversation.id),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        return None
    tool_args = message.tool_args if isinstance(message.tool_args, dict) else {}
    decision_tool_name = (
        "ask_user"
        if tool_name == "ask_user"
        else str(tool_args.get("tool_name") or "request_approval")
    )
    response: JsonObject = {SUPERSEDED_KEY: True}
    recorded = await approval_decisions.record_approval_decision(
        conversation_id=conversation.id,
        approval_id=tool_call_id,
        agent_run_id=agent_run_id,
        tool_name=decision_tool_name,
        decision=AgentRunApprovalDecision.DENY,
        response=response,
        resolved_by_user_id=user_id,
    )
    if not recorded:
        # A concurrent resolve already recorded a real decision for this call;
        # that resolve (or its own reconcile) owns synthesizing the return.
        return None
    existing_return = await pausing_calls.get_tool_return(
        conversation_id=conversation.id,
        tool_call_id=tool_call_id,
    )
    if existing_return is not None:
        return None
    return_tool_name, tool_result = await resume_returns.build(
        conversation=conversation,
        user_id=user_id,
        kind=tool_name,
        tool_args=tool_args,
        decision=AgentRunApprovalDecision.DENY,
        response=response,
        paused_agent_run_id=agent_run_id,
        deliver_to_host=False,
    )
    return await messages.append_message(
        conversation_id=conversation.id,
        agent_run_id=agent_run_id,
        draft=MessageDraft.of_tool_return(
            tool_call_id=tool_call_id,
            tool_name=return_tool_name,
            tool_result=tool_result,
        ),
    )
