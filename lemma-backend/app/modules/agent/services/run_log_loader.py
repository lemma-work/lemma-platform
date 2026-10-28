"""What an in-process run replays: its conversation's run log, and what to take in.

Read once, after the turn's open calls are settled (``turn_resume``), so the
returns that settling wrote are there to answer them. See ``domain/run_log``.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.run_log import (
    ReplayStep,
    RunLogReplay,
    open_tool_call_ids,
    pending_rows,
)
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.db.run_steps import RunSteps
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.services.run_phase_spans import run_phase


async def load_run_log(
    uow_factory: UnitOfWorkFactory,
    *,
    conversation: Conversation,
    run: AgentRun,
    history: Sequence[Message],
) -> RunLogReplay:
    """The log to replay, and the rows of ``history`` it does not carry yet.

    A private note's run replays the log and writes nothing to it: what it says
    must never reach the history a stranger's run replays.
    """
    with run_phase("run_log"):
        async with uow_factory() as uow:
            repository = RunSteps(uow)
            steps = await repository.replay(conversation.id)
            open_ids = open_tool_call_ids(step.messages for step in steps)
            returns = await repository.returns(
                conversation.id, _with_renamings(open_ids, steps)
            )
            stopped = await _last_run_stopped(uow.session, steps, current=run.id)
    return RunLogReplay(
        steps=steps,
        pending=pending_rows(history, fallback=not steps),
        returns=_by_logged_id(open_ids, steps, returns),
        stopped=stopped,
        read_only=run.state.private_note,
    )


def _renamed(call_id: str, run_id: UUID | None) -> str | None:
    """The id ``RunMessageWriter`` stored a reused call id under, in that run."""
    return f"{call_id}-{run_id.hex[-12:]}" if run_id is not None else None


def _run_of(call_id: str, steps: Sequence[ReplayStep]) -> UUID | None:
    for step in reversed(steps):
        if f'"tool_call_id":"{call_id}"' in step.messages:
            return step.run_id
    return None


def _with_renamings(open_ids: list[str], steps: Sequence[ReplayStep]) -> list[str]:
    """Each open call's id, and the one its row has if the provider reused it."""
    ids = list(open_ids)
    for call_id in open_ids:
        renamed = _renamed(call_id, _run_of(call_id, steps))
        if renamed is not None:
            ids.append(renamed)
    return ids


def _by_logged_id(
    open_ids: list[str], steps: Sequence[ReplayStep], found: dict[str, Message]
) -> dict[str, Message]:
    """The return for each open call, under the id the log knows it by.

    A provider that numbers its calls hands a later run an id an earlier call
    already had; the writer stored that call, and the return a person gave it,
    under an id of the run's own. That return is the one owed, before any
    earlier call's.
    """
    returns: dict[str, Message] = {}
    for call_id in open_ids:
        renamed = _renamed(call_id, _run_of(call_id, steps))
        stored = found.get(renamed) if renamed is not None else None
        if stored is None:
            stored = found.get(call_id)
        if stored is not None:
            returns[call_id] = stored
    return returns


async def _last_run_stopped(
    session: AsyncSession, steps: Sequence[ReplayStep], *, current: UUID
) -> bool:
    """Whether the last run the log holds was stopped by a member."""
    last = next(
        (step.run_id for step in reversed(steps) if step.run_id is not None), None
    )
    if last is None or last == current:
        return False
    status = await session.scalar(
        select(AgentRunModel.status).where(AgentRunModel.id == last)
    )
    return status == AgentRunStatus.STOPPED.value


__all__ = ["load_run_log"]
