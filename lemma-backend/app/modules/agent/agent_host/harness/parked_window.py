"""Ending an Agent Host turn that has waited too long on a person.

A remote harness cannot pause inside a tool call, so a question or an approval
it raises is parked in its turn: the host's bridge holds the MCP call open (or
the host holds its ACP permission request open) until the person decides, and
the run -- and the worker job reading it -- stay RUNNING the whole time.
A pause waits days before it expires and nothing reminds anybody, so that can
be hours: a worker slot held for nothing, every message typed meanwhile joined
to a turn that cannot read it, and an "interrupted" run at the next deploy.

So the wait is bounded. Past ``window_seconds`` the run is marked and its turn
ended with the ordinary ``CANCEL_RUN`` -- the same request Stop and ``wait_for``
make, which every host already honours by ending its ACP turn cleanly -- and
the terminal that comes back is read as WAITING (``harness_events``). The pause
itself is left exactly as it was: still listed, still answerable. Answering it
then resumes a new run with the decision, through the path every in-process
pause already takes.

A person who answers inside the window is answered inside the turn, as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.pauses import PERSON_KINDS
from app.modules.agent.domain.run_state import ParkedSuspension
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.agent_host.control.channels import poke_host
from app.modules.agent.agent_host.control.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel

logger = get_logger(__name__)

#: How often a running turn asks whether it is parked past its window. The
#: window is minutes wide, so this only has to be a small fraction of it; each
#: look is one indexed read of the run's own pauses.
DEFAULT_PARKED_CHECK_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ParkedPause:
    """The oldest unanswered pause a run is parked on, and the database's now:
    how long it has waited is measured on one clock, however far this worker's
    own has drifted."""

    tool_call_id: str
    tool_name: str
    asked_at: datetime
    now: datetime


async def oldest_parked_pause(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, agent_run_id: UUID
) -> ParkedPause | None:
    """The run's oldest pause still waiting on a person, parked *inside* it: a
    call Lemma recorded over MCP, or an ACP permission request the host holds.
    A ``wait_for`` already ends its run, and an in-process pause never leaves
    one running at all."""
    pauses = Pauses(uow)
    parked = [
        pause
        for pause in await pauses.pending(
            conversation_id, run_id=agent_run_id, kinds=PERSON_KINDS
        )
        if pause.parked or pause.kind is PauseKind.HOST_PERMISSION
    ]
    if not parked:
        return None
    pause = parked[0]
    return ParkedPause(
        tool_call_id=pause.tool_call_id,
        tool_name=pause.tool_name,
        asked_at=pause.call.created_at if pause.call else pause.created_at,
        now=await pauses.database_now(),
    )


async def mark_suspended_while_parked(
    uow: SqlAlchemyUnitOfWork, *, agent_run_id: UUID, pause: ParkedPause
) -> bool:
    """Record on a still-RUNNING run that its turn is being ended for ``pause``.

    False when the run has moved on -- a Stop already requested, or the run
    finished: a Stop means stop, and a finished run needs nothing.
    """
    marked = await uow.session.execute(
        update(AgentRunModel)
        .where(
            AgentRunModel.id == agent_run_id,
            AgentRunModel.status == AgentRunStatus.RUNNING.value,
        )
        .values(
            suspended_while_parked=ParkedSuspension(
                tool_call_id=pause.tool_call_id, tool_name=pause.tool_name
            ).to_json()
        )
        .returning(AgentRunModel.id)
        .execution_options(synchronize_session=False)
    )
    return marked.scalar_one_or_none() is not None


def parked_too_long(pause: ParkedPause, *, window_seconds: float) -> bool:
    """Whether ``pause`` has waited past the window. A window of 0 is off."""
    if window_seconds <= 0:
        return False
    return (pause.now - pause.asked_at).total_seconds() >= window_seconds


@dataclass(slots=True)
class ParkedWindow:
    """One run's parked-window clock, checked between stream batches."""

    window_seconds: float
    interval: float = DEFAULT_PARKED_CHECK_SECONDS
    next_check_at: float = 0.0
    suspended: bool = False

    def due(self, now: float) -> bool:
        """Whether to look now; arms the next look when it is."""
        if self.suspended or self.window_seconds <= 0 or now < self.next_check_at:
            return False
        self.next_check_at = now + self.interval
        return True

    async def check(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        now: float,
        conversation_id: UUID,
        agent_run_id: UUID,
    ) -> None:
        """End the turn if it has been parked on a person past the window."""
        if not self.due(now):
            return
        self.suspended = await suspend_if_parked_too_long(
            uow_factory,
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            window_seconds=self.window_seconds,
        )


async def suspend_if_parked_too_long(
    uow_factory: UnitOfWorkFactory,
    *,
    conversation_id: UUID,
    agent_run_id: UUID,
    window_seconds: float,
) -> bool:
    """Mark the run and ask its host to end the turn. True once it has.

    The lease row is locked first, and the pause read under that lock. A
    permission decision is queued for the host under the same lock and is
    refused once a cancel is queued (``enqueue_permission_decision``), so the
    two cannot cross: either the decision reached the host and this sees the
    pause answered, or the turn is ending and the decision resumes a new run.
    """
    async with uow_factory() as uow:
        await uow.session.get(
            AgentHostRunLeaseModel, agent_run_id, with_for_update=True
        )
        pause = await oldest_parked_pause(
            uow, conversation_id=conversation_id, agent_run_id=agent_run_id
        )
        if pause is None or not parked_too_long(pause, window_seconds=window_seconds):
            await uow.commit()
            return False
        if not await mark_suspended_while_parked(
            uow, agent_run_id=agent_run_id, pause=pause
        ):
            # A Stop got there first; it ends the turn its own way.
            await uow.commit()
            return False
        command = await AgentHostDispatchRepository(uow).enqueue_cancel(
            run_id=agent_run_id
        )
        await uow.commit()
    logger.info(
        "agent.agent_host.parked_run_suspended",
        agent_run_id=str(agent_run_id),
        conversation_id=str(conversation_id),
        tool_name=pause.tool_name,
    )
    if command is not None:
        await poke_host(command.host_id)
    return True


__all__ = [
    "DEFAULT_PARKED_CHECK_SECONDS",
    "ParkedPause",
    "ParkedWindow",
    "mark_suspended_while_parked",
    "oldest_parked_pause",
    "parked_too_long",
    "suspend_if_parked_too_long",
]
