"""Runs: starting one, ending one exactly once, and the reads about one run.

The ending is the write several parties race to make -- the worker finishing
the run, a person pressing Stop, the sweep that settles a run whose worker
died -- so it sits with the run's other writes, held to one rule.

The reads each resolve a single run: which is active, which is newest, whether
one is safe to replay, what an interrupted one left unpaired. Eager-loading
whole run collections to answer them was the defect this layout came from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.agent.domain.conversation_lifecycle import conversation_status_for
from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    Message as MessageEntity,
    MessageRole,
)
from app.modules.agent.domain.run_state import RunFailure, RunState
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRunFinishResult,
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
    JsonValue,
    MessageKind,
    RunTrigger,
)
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.db.conversations import (
    lock_conversation_row,
    update_conversation_status,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)

_ACTIVE_AGENT_RUN_STATUS_VALUES = [status.value for status in ACTIVE_AGENT_RUN_STATUSES]


class Runs(UnitOfWorkRepository):
    """A run's lifecycle, and the questions asked about one run."""

    async def create_agent_run(
        self,
        *,
        conversation_id: UUID,
        agent_id: UUID | None,
        agent_runtime: AgentRuntimeConfig,
        parent_run_id: UUID | None = None,
        state: RunState | None = None,
    ) -> AgentRunEntity:
        now = datetime.now(timezone.utc)
        model = AgentRunModel(
            conversation_id=conversation_id,
            agent_id=agent_id,
            parent_run_id=parent_run_id,
            status=AgentRunStatus.RUNNING.value,
            agent_runtime=agent_runtime.model_dump(mode="json"),
            started_at=now,
            **AgentRunModel.state_values(state or RunState()),
        )
        self.session.add(model)
        await update_conversation_status(
            self.session,
            conversation_id=conversation_id,
            status=ConversationStatus.RUNNING,
            output_data=None,
            touch_activity=True,
        )
        await self.session.flush()
        return model.to_entity()

    async def finish_agent_run(
        self,
        *,
        agent_run_id: UUID,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        error: str | None = None,
        error_code: str | None = None,
        error_reason: str | None = None,
        output_data: JsonValue | None = None,
        stop_requested_after_sequence: int | None = None,
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
        session = self.session
        conversation_locked = False
        while True:
            row = (
                await session.execute(
                    select(
                        AgentRunModel.status,
                        AgentRunModel.conversation_id,
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
                await lock_conversation_row(
                    session, conversation_id=row.conversation_id
                )
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
            values: dict[str, object] = {
                "status": next_status.value,
                "error": error,
                "failure": (
                    RunFailure(code=error_code, reason=error_reason).to_json()
                    if next_status == AgentRunStatus.FAILED and error_code
                    else None
                ),
            }
            if stop_requested_after_sequence is not None:
                values["stop_requested_after_sequence"] = stop_requested_after_sequence
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
            resolved_conversation_status = (
                conversation_status or conversation_status_for(next_status)
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

    async def get_agent_run(self, agent_run_id: UUID) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_active_agent_run_for_update(
        self,
        conversation_id: UUID,
    ) -> AgentRunEntity | None:
        """The conversation's active run, locked -- after the conversation itself.

        The conversation first, because that is the order ``finish_agent_run``
        and every turn transaction take the two in; a Stop that locked the run
        and then finished it would take them the other way round. A caller
        already holding the conversation re-takes a lock it has.
        """
        await lock_conversation_row(self.session, conversation_id=conversation_id)
        result = await self.session.execute(
            select(AgentRunModel)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.status.in_(_ACTIVE_AGENT_RUN_STATUS_VALUES),
            )
            .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
            .limit(1)
            .with_for_update()
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_active_agent_run(
        self,
        conversation_id: UUID,
    ) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.status.in_(_ACTIVE_AGENT_RUN_STATUS_VALUES),
            )
            .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_latest_agent_run_for_conversation(
        self,
        conversation_id: UUID,
    ) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.conversation_id == conversation_id)
            .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def run_is_replay_safe(self, agent_run_id: UUID) -> bool:
        """Whether a run replays a user turn and holds nothing a replay would repeat.

        This is the message half of ``AgentRun.is_safely_retryable``, and the
        same rule (``replays_without_effect``): only the person's messages and
        the model's reasoning, so replaying it cannot duplicate assistant
        output or tool effects.

        Answered as one aggregate over ``ix_agent_message_run_sequence`` instead
        of loading the run's messages. Callers should check the status half
        first: a run that did not fail is not retryable regardless, so the
        common path never reaches this query at all.

        A run started by a retry owns no message -- the turn it replays stays
        on the run it retried -- so it is asked for by its ``trigger`` instead.
        Without that, a retry that failed too could never be retried again.
        """
        is_retry = (
            select(AgentRunModel.trigger == RunTrigger.RETRY)
            .where(AgentRunModel.id == agent_run_id)
            .scalar_subquery()
        )
        row = (
            await self.session.execute(
                select(
                    func.count()
                    .filter(MessageModel.role == MessageRole.USER.value)
                    .label("user"),
                    func.coalesce(is_retry, False).label("is_retry"),
                    func.count()
                    .filter(
                        MessageModel.role != MessageRole.USER.value,
                        or_(
                            MessageModel.role != MessageRole.ASSISTANT.value,
                            MessageModel.kind != MessageKind.THINKING.value,
                        ),
                    )
                    .label("with_effect"),
                ).where(MessageModel.agent_run_id == agent_run_id)
            )
        ).one()
        return (bool(row.user) or bool(row.is_retry)) and not row.with_effect

    async def list_unpaired_tool_calls(
        self, agent_run_id: UUID, *, limit: int = 64
    ) -> list[MessageEntity]:
        """This run's tool calls that have no return, oldest first.

        What a turn interrupted mid-step left behind: the call row committed,
        the worker died before the return did. A turn resuming reads these to
        decide, per call, whether to run it again (``services/turn_resume``).
        Scoped to the run, so another run's calls -- a pause still open in the
        conversation -- are never this run's to settle.
        """
        returned = aliased(MessageModel)
        result = await self.session.execute(
            select(MessageModel)
            .where(
                MessageModel.agent_run_id == agent_run_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
                MessageModel.tool_call_id.is_not(None),
                ~exists().where(
                    returned.agent_run_id == agent_run_id,
                    returned.kind == MessageKind.TOOL_RETURN.value,
                    returned.tool_call_id == MessageModel.tool_call_id,
                ),
            )
            .order_by(MessageModel.sequence.asc())
            .limit(limit)
        )
        return [model.to_entity() for model in result.scalars()]

    async def claim_delivered_to_parent(self, agent_run_id: UUID) -> bool:
        """Mark a sub-agent run's result as handed to its parent, if nobody
        has. True if this call did.

        One ``UPDATE`` naming the column's emptiness, so of two racing
        collectors the second waits on the first's row lock, re-reads it, and
        finds it set.
        """
        claimed = await self.session.execute(
            update(AgentRunModel)
            .where(
                AgentRunModel.id == agent_run_id,
                AgentRunModel.delivered_to_parent_at.is_(None),
            )
            .values(delivered_to_parent_at=func.now())
            .returning(AgentRunModel.id)
            .execution_options(synchronize_session=False)
        )
        return claimed.scalar_one_or_none() is not None

    async def last_run_message(self, agent_run_id: UUID) -> MessageEntity | None:
        """The step this run committed last, if any (``ix_agent_message_run_sequence``)."""
        result = await self.session.execute(
            select(MessageModel)
            .where(MessageModel.agent_run_id == agent_run_id)
            .order_by(MessageModel.sequence.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None


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
