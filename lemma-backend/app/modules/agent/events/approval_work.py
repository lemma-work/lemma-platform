"""``agent.approval.execute``: carry out an approved ``request_approval``.

The decision's transaction writes this item (``stage_approval_execution``), so
a recorded approval always has something that will act on it. The job:

1. takes the one-shot execution claim in a checkpoint -- committed on its own,
   because evidence that dies with the worker is no evidence;
2. runs the approved tool with the conversation owner's authority, outside any
   work transaction (it can run for minutes);
3. in the fenced completion, appends the tool return and starts the resume run
   (``stage_agent_turn`` through ``start_resume_run_if_ready``).

A claim that is already held when this runs means an earlier attempt took it
and lost its lease. Two items for one approval never run at once (the
concurrency key), so no live executor can be holding it: the tool may or may not
have run, and it is never run again. The return says so -- "approved, outcome
unknown" -- and the conversation resumes. That replaces the 35-minute
``_ABANDONED_EXECUTION_SECONDS`` wait with one lease.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.context import Context
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.scope import context_scope
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.work import (
    DeadContext,
    Exponential,
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent.application.pauses.reconcile import ReconcileDecision
from app.modules.agent.application.pauses.resume import ResumeRun
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pausing_tools import PausedCall
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    RunTrigger,
)
from app.modules.agent.events.work_kinds import AGENT_APPROVAL_EXECUTE
from app.modules.agent.infrastructure.db.approvals import ApprovalDecisions
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.services.approval_reconciliation import (
    ApprovalExecution,
    runs_in_worker,
    unconfirmed_execution_return,
)
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)
from app.modules.agent.services.pause_questions import tell_where_asked
from app.modules.agent.services.pause_resume import paused_call
from app.modules.agent.services.runtime_owned_uow import RuntimeOwnedUnitOfWork

logger = get_logger(__name__)

#: Few attempts: everything before the claim is reads, and once the claim is
#: taken a retry can only ever write "outcome unknown". Lease losses retry
#: quickly, since the next attempt is what tells the person.
APPROVAL_RETRY = RetryPolicy(
    max_attempts=3,
    max_lease_losses=3,
    backoff=Exponential(base=timedelta(seconds=2)),
)


@dataclass(frozen=True, slots=True)
class _Decided:
    """What a decision says to do, read in one short transaction."""

    conversation: Conversation
    paused: PausedCall
    decision: AgentRunApprovalDecision
    response: JsonObject
    has_return: bool


def _unconfirmed_return(
    payload: ApprovalExecution, decided: _Decided, *, claimed: bool
) -> tuple[str, object]:
    return unconfirmed_execution_return(
        conversation_id=decided.conversation.id,
        approval_id=payload.approval_id,
        paused=decided.paused,
        decision=decided.decision,
        response=decided.response,
        claimed=claimed,
    )


async def _resume(
    uow: SqlAlchemyUnitOfWork,
    payload: ApprovalExecution,
    decided: _Decided,
    *,
    tool_return: tuple[str, object] | None,
) -> None:
    """Write ``tool_return`` and start the resume run (``ResumeRun``)."""
    await ResumeRun.build(uow).resume(
        conversation=decided.conversation,
        paused_run_id=decided.paused.agent_run_id,
        tool_call_id=payload.approval_id,
        user_id=decided.conversation.user_id,
        trigger=RunTrigger.RESUME,
        tool_return=tool_return,
        decided_call_args=decided.paused.tool_args,
    )


async def _read(
    uow: SqlAlchemyUnitOfWork, payload: ApprovalExecution
) -> _Decided | None:
    """The decision and its paused call, or None when there is nothing to do.

    Both can legitimately be missing -- the conversation was deleted, or the
    call never persisted -- and neither is worth retrying.
    """
    conversation = await Conversations(uow).get_conversation(payload.conversation_id)
    if conversation is None:
        return None
    recorded = await ApprovalDecisions(uow).get_approval_decision(
        conversation_id=payload.conversation_id, approval_id=payload.approval_id
    )
    paused = await paused_call(
        Pauses(uow),
        conversation_id=payload.conversation_id,
        approval_id=payload.approval_id,
    )
    if recorded is None or paused is None:
        return None
    decision, response = recorded
    existing = await Pauses(uow).get_tool_return(
        conversation_id=payload.conversation_id, tool_call_id=payload.approval_id
    )
    return _Decided(conversation, paused, decision, response, existing is not None)


async def _owner_context(
    uow: SqlAlchemyUnitOfWork, conversation: Conversation
) -> Context:
    # An approved request_approval runs its wrapped tool with a user's
    # authority. It is the conversation owner's, per
    # resolve_user_approval_internal's contract: the agent acts for the owner,
    # and whoever clicked approve is deciding, not lending their authority.
    return await create_authorization_data_service(uow).build_user_context(
        user_id=conversation.user_id, pod_id=conversation.pod_id
    )


#: What carries out one decision, given the item's context; replaceable at
#: registration so a test can make it fail without patching this module.
Executor = Callable[[JobContext, ApprovalExecution], Awaitable[None]]


async def _execute_with_the_app_sessions(
    ctx: JobContext, payload: ApprovalExecution
) -> None:
    await _execute(
        ctx, payload, uow_factory=SessionUnitOfWorkFactory(async_session_maker)
    )


def approval_handler(
    execute: Executor = _execute_with_the_app_sessions,
) -> Callable[[JobContext, ApprovalExecution], Awaitable[None]]:
    async def execute_approval(ctx: JobContext, payload: ApprovalExecution) -> None:
        try:
            await execute(ctx, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            if ctx.item.attempts + 1 >= APPROVAL_RETRY.max_attempts:
                await _report_failure(payload)
            raise

    return execute_approval


async def _execute(
    ctx: JobContext, payload: ApprovalExecution, *, uow_factory: UnitOfWorkFactory
) -> None:
    async with ctx.checkpoint() as uow:
        decided = await _read(uow, payload)
        if decided is None:
            return
        auth_ctx = await _owner_context(uow, decided.conversation)
        executes = runs_in_worker(
            kind=decided.paused.kind.value,
            tool_args=decided.paused.tool_args,
            decision=decided.decision,
            has_tool_return=decided.has_return,
        )
        took_claim = executes and await ApprovalDecisions(uow).claim_approval_execution(
            conversation_id=payload.conversation_id, approval_id=payload.approval_id
        )
        # Committed by the checkpoint before the tool runs: the claim is the
        # evidence a lost lease leaves behind.

    async with context_scope(auth_ctx):
        built: tuple[str, object] | None = None
        if took_claim:
            built = await _run_approved_tool(decided, payload, uow_factory=uow_factory)
        async with ctx.complete_work() as uow:
            await _finish(
                RuntimeOwnedUnitOfWork(uow),
                payload,
                built=built,
                lost_claim=executes and not took_claim,
            )


async def _run_approved_tool(
    decided: _Decided, payload: ApprovalExecution, *, uow_factory: UnitOfWorkFactory
) -> tuple[str, object]:
    """Run the wrapped tool; never raises (failures become the tool's return).

    On a unit of work of its own, not a work session: the run can take minutes,
    and the builder hands its connection back before the tool starts and
    records any session approvals after that commit.
    """
    async with uow_factory() as uow:
        built = await ResumeToolReturnBuilder(uow, AgentRepository(uow)).build(
            conversation=decided.conversation,
            user_id=decided.conversation.user_id,
            kind=decided.paused.kind.value,
            tool_args=decided.paused.tool_args,
            decision=decided.decision,
            response=decided.response,
            paused_agent_run_id=decided.paused.agent_run_id,
            tool_call_id=payload.approval_id,
        )
        await uow.commit()
    return built


async def _finish(
    uow: RuntimeOwnedUnitOfWork,
    payload: ApprovalExecution,
    *,
    built: tuple[str, object] | None,
    lost_claim: bool,
) -> None:
    """Write the return and resume, in the item's fenced completion.

    State is re-read here, under the transaction that commits: a Stop, a
    supersession or a racing item may have moved it since the checkpoint.
    """
    await Conversations(uow).lock_conversation(payload.conversation_id)
    decided = await _read(uow, payload)
    if decided is None:
        return
    if decided.has_return:
        await _resume(uow, payload, decided, tool_return=None)
    elif built is not None:
        await _resume(uow, payload, decided, tool_return=built)
    elif lost_claim:
        await _resume(
            uow,
            payload,
            decided,
            tool_return=_unconfirmed_return(payload, decided, claimed=True),
        )
    else:
        # Runs nothing (an answer, a denial, a host permission): the inline
        # path, for an item written for an adopted one.
        await ReconcileDecision.build(uow).reconcile(
            conversation=decided.conversation,
            approval_id=payload.approval_id,
            paused_run_id=decided.paused.agent_run_id,
            kind=decided.paused.kind.value,
            tool_args=decided.paused.tool_args,
            decision=decided.decision,
            response=decided.response,
            user_id=decided.conversation.user_id,
            agent_name=None,
        )


async def on_approval_dead(ctx: DeadContext) -> None:
    """An execution that never confirmed still ends with a return and a resume.

    Claimed: "approved, outcome unknown". Never claimed: it did not run. Either
    way the conversation is not left waiting on a card nobody can finish.
    """
    try:
        payload = ApprovalExecution.model_validate(ctx.item.payload)
    except ValueError:
        return
    uow = RuntimeOwnedUnitOfWork(ctx.uow)
    await Conversations(uow).lock_conversation(payload.conversation_id)
    decided = await _read(uow, payload)
    if decided is None or decided.has_return:
        return
    claimed = await ApprovalDecisions(uow).approval_execution_claimed(
        conversation_id=payload.conversation_id, approval_id=payload.approval_id
    )
    auth_ctx = await _owner_context(uow, decided.conversation)
    async with context_scope(auth_ctx):
        await _resume(
            uow,
            payload,
            decided,
            tool_return=_unconfirmed_return(payload, decided, claimed=claimed),
        )


async def _report_failure(payload: ApprovalExecution) -> None:
    """Tell the person that the approved action failed: on the timeline, where
    its question was asked, and on the conversation's platform.

    A chat tap on Approve is answered "running it now" before this runs, so a
    failure is a broken promise the person would otherwise never hear about.
    Only on the last attempt, and never on a shutdown: a released item is
    handed to the next worker, and nothing has failed yet.
    """
    # Lazily: it is only needed on this path, and importing it loads the
    # platforms.
    from app.modules.agent_surfaces.contracts.egress import (
        APPROVED_ACTION_FAILED_NOTICE,
        report_approved_action_failed,
    )

    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    try:
        async with uow_factory() as uow:
            await tell_where_asked(
                uow,
                conversation_id=payload.conversation_id,
                text=APPROVED_ACTION_FAILED_NOTICE,
                key=f"approval-failed:{payload.conversation_id}:{payload.approval_id}",
            )
            await uow.commit()
    except SQLAlchemyError:
        # The caller is already handling a failure; this must not replace it
        # with its own. The platform's notice below still goes.
        logger.warning(
            "agent.approval.failure_notice_line_failed.degraded",
            conversation_id=str(payload.conversation_id),
            exc_info=True,
        )
    await report_approved_action_failed(
        uow_factory=uow_factory, conversation_id=payload.conversation_id
    )


def register_approval_work(
    registry: WorkRegistry, *, execute: Executor = _execute_with_the_app_sessions
) -> None:
    registry.handler(
        kind=AGENT_APPROVAL_EXECUTE,
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.JOB,
        payload=ApprovalExecution,
        retry=APPROVAL_RETRY,
        concurrency=1,
        on_dead=on_approval_dead,
    )(approval_handler(execute))


register_approval_work(default_registry)

__all__ = [
    "APPROVAL_RETRY",
    "Executor",
    "approval_handler",
    "on_approval_dead",
    "register_approval_work",
]
