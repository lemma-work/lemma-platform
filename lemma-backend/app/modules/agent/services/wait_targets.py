"""Reading whatever a wait is waiting on, for the two callers that must agree.

``wait_for`` asks before it suspends, so a target that has already finished
answers immediately instead of costing a suspend, a wake and a full history
replay. The resolver asks on every fire and poll. If those two disagreed about what
"finished" means, an agent could be told a process was still running and then
woken a second later to learn it had ended before it asked.

Nothing here decides *policy* — whether to wake, re-arm or give up is the
resolver's call, because only it knows the deadline. This only answers what the
target is doing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from uuid import UUID

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import (
    TERMINAL_AGENT_RUN_STATUSES,
    ConversationStatus,
)
from app.modules.agent.domain.delegation import (
    GONE,
    NEEDS_INPUT,
    RUNNING,
    ChildReport,
)
from app.modules.agent.domain.wait import AgentWaitType, AgentWaitWakeReason
from app.modules.agent.infrastructure.tools.waiting.models import wake_message

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TargetOutcome:
    """What the target is doing. ``reason is None`` means "still going".

    ``unreadable`` qualifies "still going": nothing answered, so it is only not
    known to have stopped. The resolver gives that a grace period, not forever.
    """

    reason: AgentWaitWakeReason | None
    exit_code: int | None = None
    message: str = ""
    unreadable: bool = False
    #: Each child's report, for a wait on sub-agents.
    reports: tuple[ChildReport, ...] = ()


_STILL_GOING = TargetOutcome(reason=None)
_UNREADABLE = TargetOutcome(reason=None, unreadable=True)


async def read_target(
    *,
    wait_type: AgentWaitType,
    target_ref: str | None,
    user_id: UUID,
    pod_id: UUID | None,
    conversation_id: UUID,
) -> TargetOutcome:
    """What ``target_ref`` is doing right now, in wake-reason terms.

    ``conversation_id`` is the conversation doing the waiting. A sub-agent run
    is only readable from the conversation that spawned it: the run id comes
    from the model, and anything else would let it read any run it can name.
    """
    if wait_type is AgentWaitType.TIME or not target_ref:
        # A TIME wait has no target: its timer *is* the condition, so there is
        # never anything to look at and the resolver wakes it on schedule.
        return _STILL_GOING
    if wait_type is AgentWaitType.PROCESS:
        return await _read_process(target_ref, user_id=user_id)
    if wait_type is AgentWaitType.SUBAGENT:
        return await _read_subagent_run(
            target_ref, user_id=user_id, parent_conversation_id=conversation_id
        )
    return _STILL_GOING


def _outcome(
    wait_type: AgentWaitType,
    reason: AgentWaitWakeReason,
    *,
    exit_code: int | None = None,
) -> TargetOutcome:
    return TargetOutcome(
        reason=reason,
        exit_code=exit_code,
        message=wake_message(wait_type, reason, exit_code=exit_code),
    )


async def _read_process(process_id: str, *, user_id: UUID) -> TargetOutcome:
    from app.modules.workspace.contracts.processes import (
        ProcessProbeStatus,
        probe_process,
    )

    probe = await probe_process(user_id=user_id, process_id=process_id)
    if probe.status is ProcessProbeStatus.FINISHED:
        return _outcome(
            AgentWaitType.PROCESS,
            AgentWaitWakeReason.TARGET_FINISHED,
            exit_code=probe.exit_code,
        )
    if probe.status is ProcessProbeStatus.GONE:
        return _outcome(AgentWaitType.PROCESS, AgentWaitWakeReason.TARGET_GONE)
    if probe.status is ProcessProbeStatus.UNREADABLE:
        # "Ask again", not an ending. An unreachable provider is not evidence
        # that anything stopped -- the idle sweep learned that by releasing a
        # sandbox mid-command -- but it is not evidence of running either, and
        # the resolver stops waiting blind after a grace period.
        return _UNREADABLE
    return _STILL_GOING


async def _read_subagent_run(
    run_id: str, *, user_id: UUID, parent_conversation_id: UUID
) -> TargetOutcome:
    return await read_children(
        [run_id], user_id=user_id, parent_conversation_id=parent_conversation_id
    )


async def read_children(
    run_ids: Sequence[str], *, user_id: UUID, parent_conversation_id: UUID
) -> TargetOutcome:
    """``read_children_in`` on a unit of work of its own (the check a wait
    makes before it suspends)."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        outcome = await read_children_in(
            uow,
            run_ids,
            user_id=user_id,
            parent_conversation_id=parent_conversation_id,
        )
        await uow.commit()
    return outcome


async def read_subagent_in(
    uow: SqlAlchemyUnitOfWork,
    run_id: str,
    *,
    user_id: UUID,
    parent_conversation_id: UUID,
) -> TargetOutcome:
    """One child's run, as ``read_children_in`` reads a set of them."""
    return await read_children_in(
        uow,
        [run_id],
        user_id=user_id,
        parent_conversation_id=parent_conversation_id,
    )


async def read_children_in(
    uow: SqlAlchemyUnitOfWork,
    run_ids: Sequence[str],
    *,
    user_id: UUID,
    parent_conversation_id: UUID,
) -> TargetOutcome:
    """The children's runs, in terms of their conversations as well as the runs.

    Still going while any is; needing input as soon as one stopped to ask --
    the child has no surface, so nobody but its parent sees the question --
    and finished once every one has ended. Every outcome carries each child's
    report, so the parent wakes once, with all of them.

    On the caller's unit of work, so a timer or a finished child resolves the
    parent's wait in one transaction: database reads only.
    """
    reports = [
        await _child_report(
            uow, run_id, user_id=user_id, parent_conversation_id=parent_conversation_id
        )
        for run_id in run_ids
    ]
    statuses = {report.status for report in reports}
    reason: AgentWaitWakeReason | None
    if NEEDS_INPUT in statuses:
        reason = AgentWaitWakeReason.TARGET_NEEDS_INPUT
    elif RUNNING in statuses:
        return TargetOutcome(reason=None, reports=tuple(reports))
    elif statuses == {GONE}:
        reason = AgentWaitWakeReason.TARGET_GONE
    else:
        reason = AgentWaitWakeReason.TARGET_FINISHED
    return TargetOutcome(
        reason=reason,
        message=wake_message(AgentWaitType.SUBAGENT, reason),
        reports=tuple(reports),
    )


async def _child_report(
    uow: SqlAlchemyUnitOfWork,
    run_id: str,
    *,
    user_id: UUID,
    parent_conversation_id: UUID,
) -> ChildReport:
    """One child, as its parent hears of it; claimed as collected once ended.

    A run that ended on a question has ended, but its conversation is WAITING
    on an answer only the parent can arrange. Reported as finished, the parent
    built on a result that did not exist yet.
    """
    from app.modules.agent.infrastructure.db.conversations import Conversations
    from app.modules.agent.infrastructure.db.runs import Runs
    from app.modules.agent.infrastructure.repositories import AgentRepository
    from app.modules.agent.services.subagent_result_delivery import mark_collected
    from app.modules.agent.services.subagent_service import open_question

    gone = ChildReport(conversation_id="", run_id=run_id, agent="", status=GONE)
    try:
        parsed = UUID(run_id)
    except ValueError:
        return gone
    run = await Runs(uow).get_agent_run(parsed)
    child = (
        await Conversations(uow).get_conversation(
            run.conversation_id, include_runs=False
        )
        if run is not None
        else None
    )
    if (
        run is None
        or child is None
        or child.parent_id != parent_conversation_id
        or child.user_id != user_id
    ):
        # Somebody else's run reads exactly like one that does not exist.
        return gone
    agent = await AgentRepository(uow).get(child.agent_id) if child.agent_id else None
    report = ChildReport(
        conversation_id=str(child.id),
        run_id=run_id,
        agent=agent.name if agent is not None else DEFAULT_POD_AGENT_NAME,
        status=RUNNING,
    )
    if run.status not in TERMINAL_AGENT_RUN_STATUSES:
        return report
    if child.status == ConversationStatus.WAITING:
        asked = await open_question(uow, child.id)
        return replace(report, status=NEEDS_INPUT, question=asked.get("waiting_on"))
    # Handed to the parent waiting on exactly this run: collected, so nothing
    # delivers it again.
    await mark_collected(uow, run.id)
    return replace(
        report, status=run.status.value, output=run.output_data, error=run.error
    )
