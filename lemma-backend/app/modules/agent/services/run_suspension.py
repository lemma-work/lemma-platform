"""How a remote harness's turn ends when a tool puts the agent to sleep.

The in-process harness suspends from the inside: ``wait_for`` raises
``AgentInputRequired``, the run loop catches it, and the turn is over. A remote
harness owns its own session and runs the tool over MCP, so nothing raised
inside that tool call can end its turn — the agent simply carries on with the
result. Lemma has to ask it to stop.

The ask is the ordinary ``CANCEL_RUN``, which every host already implements. It
is *what Lemma asked for* that differs, not what the host does, so nothing here
touches the protocol. The two halves live together because they are one
statement made twice: :func:`suspend_remote_run` says "stop, this run is going
to wait", and :func:`run_suspended_on` is how the harness reads that back when
the run's terminal state arrives and it has to decide whether a stopped turn
means STOPPED or WAITING.

A turn parked on a person (``ask_user``, an approval, an ACP permission
request) is ended the same way once it has waited past the parked window
(``harnesses/agent_host/parked_window.py``); :func:`run_suspended_while_parked`
is its read-back, keyed on the mark that window writes on the run.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.run_state import ParkedSuspension
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.agent_host.api import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.runs import Runs


async def suspend_remote_run(
    uow: SqlAlchemyUnitOfWork,
    *,
    agent_run_id: UUID,
) -> UUID | None:
    """Ask the host to end this turn. Returns the host to poke, if any.

    ``None`` covers every case where there is nothing to stop — a run with no
    host lease, one already terminal, one whose cancel is already on its way.
    All of them are fine: the wait row is what wakes the conversation, and it is
    written whether or not this lands. What is *not* fine is skipping this and
    trusting the model to end its own turn, because a turn still running when
    the timer fires makes the wake a no-op and the agent never wakes at all.
    """
    command = await AgentHostDispatchRepository(uow).enqueue_cancel(run_id=agent_run_id)
    return command.host_id if command is not None else None


async def run_suspended_on(
    uow: SqlAlchemyUnitOfWork,
    *,
    agent_run_id: UUID,
) -> Pause | None:
    """The wait this run stopped for, if it stopped for one.

    Read at the end of a remote run to tell a turn that finished from one that
    was suspended mid-thought. Deliberately keyed on the durable wait row rather
    than on the terminal state the host reported: the host says ``CANCELLED``
    when it stopped the turn and ``SUCCEEDED`` when the agent got there first,
    and which of those happens is a race between a poke and a model deciding to
    stop talking. Neither answers the question being asked.
    """
    waits = await Pauses(uow).pending(run_id=agent_run_id, kinds=(PauseKind.WAIT,))
    return waits[0] if waits else None


async def run_suspended_while_parked(
    uow: SqlAlchemyUnitOfWork,
    *,
    agent_run_id: UUID,
) -> ParkedSuspension | None:
    """The pause this run's turn was ended for, if Lemma ended it for one.

    The other half of :func:`run_suspended_on`, for a turn parked on a person
    rather than asleep on a timer: the host reports the stop it was asked for
    as ``CANCELLED`` (or ``SUCCEEDED``, when the agent finished first), and
    this is what says the run is in fact waiting. ``None`` once a Stop has been
    requested on top: Stop means stop, and wins.
    """
    run = await Runs(uow).get_agent_run(agent_run_id)
    if run is None or run.status != AgentRunStatus.RUNNING:
        return None
    return run.state.suspended_while_parked
