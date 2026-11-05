"""Ending a run, and moving its conversation's status with it.

Split from `ConversationRepository` the way the run and approval queries were:
these are the writes that end a run, which several parties race to make -- the
worker finishing it, a person pressing Stop, the sweep that settles a run whose
worker died -- and they are easier to hold to one rule when they sit together.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.value_objects import (
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRunFinishResult,
    AgentRunStatus,
    ConversationStatus,
    JsonObject,
    JsonValue,
)
from app.modules.agent.domain.conversation_lifecycle import conversation_status_for
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.infrastructure.repositories.conversation_activity import (
    touch_activity as _touch_activity,
)


async def finish_agent_run(
    session: AsyncSession,
    *,
    agent_run_id: UUID,
    status: AgentRunStatus,
    conversation_status: ConversationStatus | None = None,
    error: str | None = None,
    error_code: str | None = None,
    error_reason: str | None = None,
    output_data: JsonValue | None = None,
    stamp: JsonObject | None = None,
) -> AgentRunFinishResult | None:
    """Move a run on from the status it is in, once, whoever else is trying.

    A compare-and-set rather than a read then a write. The worker finishing
    a run, a person pressing Stop and the sweep that settles a run whose
    worker died can all reach here for the same run; reading it unlocked
    let the sweep's FAILED overwrite a COMPLETED committed in between, and
    report the run as ended a second time -- a second completion event, so
    a second follow-up turn and a second parent wake. The ``UPDATE`` names
    the status it read, so of two racing writers exactly one moves the row;
    the other re-reads it and finds it already ended.

    Statuses only move forward (running, stop requested, terminal), so the
    re-read terminates.

    Lock order: the conversation, then its run -- the order every transaction
    that starts, resumes, retries or stops a turn takes them in. Updating the
    run first deadlocks against a resume that holds the conversation and waits
    for the run: an approval answered while the run that asked for it is still
    being written as finished.
    """
    conversation_locked = False
    while True:
        row = (
            await session.execute(
                select(
                    AgentRunModel.status,
                    AgentRunModel.conversation_id,
                    AgentRunModel.run_metadata,
                ).where(AgentRunModel.id == agent_run_id)
            )
        ).one_or_none()
        if row is None:
            return None
        current_status = AgentRunStatus(row.status)
        if current_status in TERMINAL_AGENT_RUN_STATUSES:
            return await _already_finished(
                session,
                conversation_id=row.conversation_id,
                current_status=current_status,
            )

        if not conversation_locked:
            await lock_conversation_row(session, conversation_id=row.conversation_id)
            conversation_locked = True
            # Waiting for the lock may have let another writer end the run;
            # read it again under the lock rather than act on the old status.
            continue

        next_status = status
        if (
            current_status == AgentRunStatus.STOP_REQUESTED
            and status in TERMINAL_AGENT_RUN_STATUSES
        ):
            # Stopped, not failed: it must not inherit a failure's message.
            next_status, error = AgentRunStatus.STOPPED, None
        metadata = dict(row.run_metadata or {})
        metadata.pop("failure", None)
        if next_status == AgentRunStatus.FAILED and error_code:
            metadata["failure"] = {"code": error_code, "reason": error_reason}
        metadata.update(stamp or {})
        values: dict[str, object] = {
            "status": next_status.value,
            "error": error,
            "run_metadata": metadata,
        }
        if output_data is not None:
            values["output_data"] = output_data
        if next_status in TERMINAL_AGENT_RUN_STATUSES:
            values["finished_at"] = datetime.now(timezone.utc)
        moved = await session.execute(
            update(AgentRunModel)
            .where(
                AgentRunModel.id == agent_run_id,
                AgentRunModel.status == current_status.value,
            )
            .values(**values)
            .returning(AgentRunModel.id)
            .execution_options(synchronize_session="fetch")
        )
        if moved.scalar_one_or_none() is None:
            continue
        resolved_conversation_status = conversation_status or conversation_status_for(
            next_status
        )
        await update_conversation_status(
            session,
            conversation_id=row.conversation_id,
            status=resolved_conversation_status,
            output_data=output_data,
            touch_activity=True,
        )
        await session.flush()
        return AgentRunFinishResult(
            status=next_status,
            conversation_status=resolved_conversation_status,
            updated=True,
        )


async def lock_conversation_row(
    session: AsyncSession, *, conversation_id: UUID
) -> None:
    """Take the conversation's row lock: the first lock of any turn transaction.

    See ``finish_agent_run`` for the order and the deadlock it prevents.
    """
    await session.execute(
        select(ConversationModel.id)
        .where(ConversationModel.id == conversation_id)
        .with_for_update()
    )


async def _already_finished(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    current_status: AgentRunStatus,
) -> AgentRunFinishResult:
    """Nothing to end: report the conversation as it stands, and write nothing.

    The status the run's own finish wrote, in the same transaction as the run.
    That is what keeps the two in step, so a second finalize reading it back
    has nothing to repair.
    """
    stored = await session.scalar(
        select(ConversationModel.status).where(ConversationModel.id == conversation_id)
    )
    return AgentRunFinishResult(
        status=current_status,
        conversation_status=(
            ConversationStatus(stored)
            if stored is not None
            else conversation_status_for(current_status)
        ),
        updated=False,
    )


async def update_conversation_status(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    status: ConversationStatus,
    output_data: JsonValue | None = None,
    touch_activity: bool = False,
) -> None:
    conversation = await session.get(ConversationModel, conversation_id)
    if conversation is None:
        return
    conversation.status = status.value
    # A run starting (every wake starts one) or ending is activity; a
    # cancelled wait or a repaired status is not. Same UPDATE either way.
    if touch_activity:
        _touch_activity(conversation)
    if output_data is not None or status == ConversationStatus.RUNNING:
        conversation.output_data = output_data
