"""The run log's repository: appending a step, and reading back what to replay.

A step takes the next ``seq`` of its conversation under the conversation row's
lock -- the one every message append takes -- so two writers in one
conversation cannot claim the same place. A compaction step is a checkpoint:
replay starts there, so the steps before it are dead weight and are removed in
the same transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, select, update

from app.modules.agent.domain.entities import Message as MessageEntity
from app.modules.channels.contracts.backgrounds import BACKGROUND_KEY
from app.modules.agent.domain.run_log import ReplayStep, RunStepDraft, RunStepKind
from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models import (
    AgentRunStepModel,
    ConversationModel,
    MessageModel,
)


#: Steps per replay read.
_REPLAY_PAGE = 200


class RunSteps(UnitOfWorkRepository):
    """A conversation's run log."""

    async def append(
        self, *, conversation_id: UUID, run_id: UUID | None, draft: RunStepDraft
    ) -> int:
        """Add ``draft`` at the end of the log; its ``seq``.

        Marks the rows it carries, so the next run does not take them in again.
        """
        await self.session.execute(
            select(ConversationModel.id)
            .where(ConversationModel.id == conversation_id)
            .with_for_update()
        )
        seq = (
            int(
                await self.session.scalar(
                    select(func.coalesce(func.max(AgentRunStepModel.seq), 0)).where(
                        AgentRunStepModel.conversation_id == conversation_id
                    )
                )
                or 0
            )
            + 1
        )
        self.session.add(
            AgentRunStepModel(
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                kind=draft.kind,
                messages=draft.messages,
            )
        )
        if draft.kind is RunStepKind.COMPACTION:
            await self.session.execute(
                delete(AgentRunStepModel).where(
                    AgentRunStepModel.conversation_id == conversation_id,
                    AgentRunStepModel.seq < seq,
                )
            )
        await self.carry(conversation_id, draft.covers, step=seq)
        await self.session.flush()
        return seq

    async def carry(
        self, conversation_id: UUID, row_ids: Sequence[UUID], *, step: int
    ) -> None:
        """Mark ``row_ids`` as carried into the model's history by ``step``."""
        if not row_ids:
            return
        await self.session.execute(
            update(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.id.in_(list(row_ids)),
                MessageModel.run_step_seq.is_(None),
            )
            .values(run_step_seq=step)
        )

    async def replay(self, conversation_id: UUID) -> list[ReplayStep]:
        """The latest checkpoint and every step after it, oldest first.

        Paged from the checkpoint by ``seq``: the log between two checkpoints
        is one conversation's model history, which compaction keeps within the
        model's window, but a page is what a read asks for.
        """
        # The first page starts at the checkpoint in the same statement.
        checkpoint = (
            select(func.coalesce(func.max(AgentRunStepModel.seq), 0))
            .where(
                AgentRunStepModel.conversation_id == conversation_id,
                AgentRunStepModel.kind == RunStepKind.COMPACTION,
            )
            .scalar_subquery()
        )
        steps = await self._page(conversation_id, after_seq=checkpoint - 1)
        if len(steps) < _REPLAY_PAGE:
            return steps
        after = steps[-1].seq
        while True:
            page = await self._page(conversation_id, after_seq=after)
            steps.extend(page)
            if len(page) < _REPLAY_PAGE:
                return steps
            after = page[-1].seq

    async def _page(
        self, conversation_id: UUID, *, after_seq: int | ColumnElement[int]
    ) -> list[ReplayStep]:
        rows = await self.session.scalars(
            select(AgentRunStepModel)
            .where(
                AgentRunStepModel.conversation_id == conversation_id,
                AgentRunStepModel.seq > after_seq,
            )
            .order_by(AgentRunStepModel.seq)
            .limit(_REPLAY_PAGE)
        )
        return [row.to_replay() for row in rows]

    async def forget(
        self, *, pod_id: UUID, reading_channels: Sequence[UUID] | None = None
    ) -> None:
        """Drop the run log of the pod's conversations -- all of them, or those
        whose runs were shown a background from ``reading_channels``.

        What the log holds was rendered when it was sent, and the projection is
        what changes: a line edited or emptied on the platform, a forgotten
        contact's words. Each such conversation's next run takes its past in
        again from the rows, once, as a conversation from before the log does.
        """
        conversations = select(ConversationModel.id).where(
            ConversationModel.pod_id == pod_id
        )
        if reading_channels is not None:
            refs = MessageModel.message_metadata[BACKGROUND_KEY]["channel_id"]
            conversations = (
                select(MessageModel.conversation_id)
                .join(
                    ConversationModel,
                    ConversationModel.id == MessageModel.conversation_id,
                )
                .where(
                    ConversationModel.pod_id == pod_id,
                    refs.astext.in_([str(channel) for channel in reading_channels]),
                )
            )
        await self.session.execute(
            delete(AgentRunStepModel).where(
                AgentRunStepModel.conversation_id.in_(conversations)
            )
        )
        await self.session.execute(
            update(MessageModel)
            .where(
                MessageModel.conversation_id.in_(conversations),
                MessageModel.run_step_seq.is_not(None),
            )
            .values(run_step_seq=None)
        )

    async def returns(
        self, conversation_id: UUID, tool_call_ids: Sequence[str]
    ) -> dict[str, MessageEntity]:
        """The first stored return for each of ``tool_call_ids``.

        The first, because a resumed pause can leave two, and the earlier one is
        what the run that resumed it was shown.
        """
        if not tool_call_ids:
            return {}
        rows = await self.session.scalars(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.kind == MessageKind.TOOL_RETURN.value,
                MessageModel.tool_call_id.in_(list(tool_call_ids)),
            )
            .order_by(MessageModel.sequence)
        )
        found: dict[str, MessageEntity] = {}
        for row in rows:
            if row.tool_call_id is not None:
                found.setdefault(row.tool_call_id, row.to_entity())
        return found


__all__ = ["RunSteps"]
