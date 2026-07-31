"""Durable user-approval reconciliation.

Resolving an approval is two steps with very different costs: recording the
user's decision is a single row write, while acting on it can run an approved
command for minutes or hand a decision to a remote Agent Host. Callers that sit
behind a deadline — an HTTP request, a platform webhook — must only ever pay for
the first. This module owns the pieces that let them do that: the deterministic
job identity, the queueing, and the work the queued job performs.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.agent.domain.agent_host_permissions import (
    AgentHostPermissionRequest,
    agent_host_permission_request,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    MessageKind,
    to_json_value,
)
from app.modules.agent.tools.context import BaseAgentContext

_PAUSING_TOOL_NAMES = ("ask_user", "request_approval")

RECONCILE_APPROVAL_JOB = "reconcile_agent_approval"


def approval_reconcile_job_id(conversation_id: UUID, approval_id: str) -> str:
    """Return the deterministic worker job id for one approval decision."""
    return f"agent-approval:{conversation_id}:{approval_id}"


async def queue_approval_reconciliation(
    *,
    conversation_id: UUID,
    approval_id: str,
    user_id: UUID,
    pod_id: UUID,
) -> None:
    """Hand one already-recorded decision to the worker.

    The deterministic job id makes a double-click (or a retry after a crashed
    worker) re-enqueue the same job rather than stack a second one, which is
    safe because reconciliation is itself idempotent.
    """
    await get_streaq_job_queue().enqueue(
        RECONCILE_APPROVAL_JOB,
        context={
            "conversation_id": str(conversation_id),
            "approval_id": approval_id,
            "user_id": str(user_id),
            "pod_id": str(pod_id),
        },
        _job_id=approval_reconcile_job_id(conversation_id, approval_id),
    )


def pending_user_approval_messages(messages: Sequence[Message]) -> list[Message]:
    """Keep an approval visible until its synthesized return is durable.

    A decision is not fully resolved until its tool return has been persisted.
    Approved tools now execute asynchronously, so keeping the card visible
    during that processing window lets a repeated click safely re-enqueue the
    deterministic job if a worker died mid-reconciliation.
    """
    returned_ids = {
        message.tool_call_id
        for message in messages
        if message.kind == MessageKind.TOOL_RETURN and message.tool_call_id is not None
    }
    return [
        message
        for message in messages
        if message.kind == MessageKind.TOOL_CALL
        and message.tool_name in _PAUSING_TOOL_NAMES
        and message.tool_call_id not in returned_ids
    ]


def should_defer_approved_tool(
    *,
    defer_reconciliation: bool,
    kind: str,
    tool_args: JsonObject,
    decision: AgentRunApprovalDecision,
    has_tool_return: bool,
) -> bool:
    """Whether this decision's follow-up work belongs in a worker job.

    Only an approved ``request_approval`` runs something slow: commands can
    legitimately take minutes, and keeping that inside the request made a
    browser's 30-second timeout cancel reconciliation after the decision had
    already committed. ``ask_user``, denials, already-executed approvals, and
    Agent Host permissions (a queued command plus a wake-up, measured in
    milliseconds) stay inline, since deferring them would only add latency to
    someone who is waiting.
    """
    return (
        defer_reconciliation
        and kind == "request_approval"
        and decision != AgentRunApprovalDecision.DENY
        and not has_tool_return
        and agent_host_permission_request(tool_args) is None
    )


async def dispatch_agent_host_permission(
    *,
    request: AgentHostPermissionRequest,
    agent_run_id: UUID,
    decision: AgentRunApprovalDecision,
) -> bool:
    """Answer a permission request the Agent Host is holding open.

    Returns whether the decision was *queued for* a live run. ``False`` means
    the run already ended — the host's own request timeout has taken over and
    there is nothing left to answer — which the caller reports back to the agent
    instead of pretending the action happened.

    ``True`` is deliberately weaker than "the host applied it". Delivery is a
    poll away and can still be missed if the machine never comes back, so the
    command carries a TTL sized to the host's own permission window rather than
    the default five minutes; nothing here waits for confirmation, and the
    wording the agent sees must not claim any.
    """
    # Lazy: the dispatch repository pulls in the Agent Host infrastructure,
    # which imports back through the harnesses.
    from app.modules.agent.infrastructure.agent_host_channels import poke_host
    from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
        AgentHostDispatchRepository,
    )

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        command = await AgentHostDispatchRepository(uow).enqueue_permission_decision(
            run_id=agent_run_id,
            request_id=request.request_id,
            option_id=request.option_for(decision),
        )
        await uow.commit()
    if command is None:
        return False
    # The host is long-polling; without the poke the decision waits out the
    # poll deadline while a user watches an idle agent.
    await poke_host(command.host_id)
    return True


async def record_session_approvals(
    *,
    conversation_id: UUID,
    agent_id: UUID | None,
    tool_args: JsonObject,
    user_id: UUID,
) -> None:
    """Persist APPROVE_FOR_SESSION as per-permission session approvals, plus an
    exact-match approval for the wrapped call itself.

    Keyed to (conversation, workload actor, permission) — the same key the
    authorizer checks (for `permission_ids`) or `request_approval` itself checks
    before pausing again (for the exact-command key). Structured actions
    (pod/table/folder/... delete) carry `permission_ids` copied from the denied
    tool result, unlocking the whole action TYPE for the rest of the
    conversation. Tools with no structured permission — exec_command,
    execute_python, anything not gated by the authorizer — have no category to
    unlock, so they get only the exact-command key: approving one call lets the
    agent repeat that LITERAL call again without re-prompting, but a different
    command still re-prompts (see session_approvals.exact_command_permission_id
    for why anything looser, e.g. a prefix match, would be a shell-injection
    vector).
    """
    from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
    from app.core.authorization.session_approvals import (
        exact_command_permission_id,
        record_session_approval,
    )

    workload_actor_id = f"agent:{agent_id or DEFAULT_POD_AGENT_ID}"

    inner_tool_name = tool_args.get("tool_name")
    if isinstance(inner_tool_name, str) and inner_tool_name:
        inner_args = tool_args.get("args")
        await record_session_approval(
            session_id=str(conversation_id),
            workload_actor_id=workload_actor_id,
            permission_id=exact_command_permission_id(
                inner_tool_name,
                inner_args if isinstance(inner_args, dict) else {},
            ),
            resolved_by_user_id=user_id,
        )

    permission_ids = tool_args.get("permission_ids")
    if not isinstance(permission_ids, list):
        return
    for permission_id in permission_ids:
        if not isinstance(permission_id, str) or not permission_id:
            continue
        await record_session_approval(
            session_id=str(conversation_id),
            workload_actor_id=workload_actor_id,
            permission_id=permission_id,
            resolved_by_user_id=user_id,
        )


async def agent_host_permission_tool_return(
    *,
    request: AgentHostPermissionRequest,
    agent_run_id: UUID,
    decision: AgentRunApprovalDecision,
    response: JsonObject,
) -> JsonObject:
    """Send the decision to the host and describe the outcome to the agent.

    Shaped as a ``request_approval`` return like every other approval, so the
    conversation transcript reads the same however the pause arose.

    ``executed`` stays False even for an approval, unlike the ordinary
    request_approval path. It means "the wrapped tool ran", and on this path
    Lemma runs nothing: it queues a decision for a machine that will collect it
    on its next poll, and the ACP agent then proceeds, or does not, on its own.
    Reporting True here would tell the agent an action completed at a moment
    when the decision had not even been delivered.
    """
    # Lazy: the tool models import the tool registry, which imports back here.
    from app.modules.agent.tools.user_interaction.models import RequestApprovalResponse

    delivered = await dispatch_agent_host_permission(
        request=request,
        agent_run_id=agent_run_id,
        decision=decision,
    )
    approved = decision != AgentRunApprovalDecision.DENY
    if not delivered:
        content = RequestApprovalResponse(
            success=False,
            error=(
                "The local agent's run ended before this decision reached it, "
                "so the action did not run."
            ),
            decision=decision,
            executed=False,
            response=response,
        )
    else:
        content = RequestApprovalResponse(
            success=True,
            message=(
                "Approved. The decision is queued for the local agent, which "
                "will pick it up on its next poll and decide what to do; "
                "Lemma has not run anything."
                if approved
                else "Denied. The decision is queued for the local agent, "
                "which was told not to use the tool."
            ),
            decision=decision,
            executed=False,
            response=response,
        )
    return content.model_dump(mode="json")


async def execute_approved_tool_as_user(
    *,
    uow: SqlAlchemyUnitOfWork,
    deps: BaseAgentContext,
    tool_name: str,
    args: dict[str, object],
) -> dict[str, object]:
    """Run an approved tool outside the database transaction; never raise."""
    # Lazy to avoid importing the tool registry back through ConversationService.
    from app.modules.agent.tools.approval.executor import ApprovalExecutor

    try:
        # Approved commands may run for minutes. Release the checked-out DB
        # connection before crossing that external boundary; otherwise a command
        # timeout can leave us appending its tool return through a connection the
        # server closed while it sat idle. The repository session checks out a
        # fresh one for the append/resume transaction that follows.
        await uow.commit()
        executor = ApprovalExecutor(SessionUnitOfWorkFactory(async_session_maker))
        result = await executor.execute_as_user(
            deps=deps,
            tool_name=tool_name,
            args=args,
        )
        value = to_json_value(result)
        if isinstance(value, dict) and value.get("success") is False:
            error = value.get("error") or value.get("message")
            return {
                "ok": False,
                "error": str(error or f"{tool_name} did not complete"),
            }
        return {"ok": True, "value": value}
    except Exception as exc:  # noqa: BLE001 - reported to the model, not fatal
        return {"ok": False, "error": str(exc)}
