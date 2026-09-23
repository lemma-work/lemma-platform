"""Reading whatever a wait is waiting on, for the two callers that must agree.

``wait_for`` asks before it suspends, so a target that has already finished
answers immediately instead of costing a suspend, a wake and a full history
replay. The resolver asks on every claim. If those two disagreed about what
"finished" means, an agent could be told a process was still running and then
woken a second later to learn it had ended before it asked.

Nothing here decides *policy* — whether to wake, re-arm or give up is the
resolver's call, because only it knows the deadline. This only answers what the
target is doing.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import TERMINAL_AGENT_RUN_STATUSES
from app.modules.agent.domain.wait import AgentWaitType, AgentWaitWakeReason
from app.modules.agent.tools.waiting.models import wake_message

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TargetOutcome:
    """What the target is doing. ``reason is None`` means "still going"."""

    reason: AgentWaitWakeReason | None
    exit_code: int | None = None
    message: str = ""


_STILL_GOING = TargetOutcome(reason=None)


async def read_target(
    *,
    wait_type: AgentWaitType,
    target_ref: str | None,
    user_id: UUID,
    pod_id: UUID | None,
) -> TargetOutcome:
    """What ``target_ref`` is doing right now, in wake-reason terms."""
    if wait_type is AgentWaitType.TIME or not target_ref:
        # A TIME wait has no target: its timer *is* the condition, so there is
        # never anything to look at and the resolver wakes it on schedule.
        return _STILL_GOING
    if wait_type is AgentWaitType.PROCESS:
        return await _read_process(target_ref, user_id=user_id)
    if wait_type is AgentWaitType.SUBAGENT:
        return await _read_subagent_run(target_ref)
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
        message=wake_message(wait_type, reason),
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
    # RUNNING and UNREADABLE are both "ask again". An unreachable provider is
    # not evidence that anything stopped -- the idle sweep learned that by
    # releasing a sandbox mid-command -- and the wait's own deadline is what
    # stops an unreadable target being waited on forever.
    return _STILL_GOING


async def _read_subagent_run(run_id: str) -> TargetOutcome:
    from app.modules.agent.infrastructure.repositories import ConversationRepository

    try:
        parsed = UUID(run_id)
    except ValueError:
        return _outcome(AgentWaitType.SUBAGENT, AgentWaitWakeReason.TARGET_GONE)

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        run = await ConversationRepository(uow).get_agent_run(parsed)
    if run is None:
        return _outcome(AgentWaitType.SUBAGENT, AgentWaitWakeReason.TARGET_GONE)
    if run.status in TERMINAL_AGENT_RUN_STATUSES:
        return _outcome(AgentWaitType.SUBAGENT, AgentWaitWakeReason.TARGET_FINISHED)
    return _STILL_GOING
