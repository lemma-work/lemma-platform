"""A conversation's messages: appending one, and reading them back.

Queued messages -- the ones sent while a run was working -- have their own
repository in ``queued_messages``; the predicate and the writes that move them
are a state machine of their own.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.modules.agent.domain.entities import Message as MessageEntity
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import (
    MessageDraft,
    MessageKind,
    to_json_value,
)
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.conversation_activity import (
    is_activity,
    touch_activity as _touch_activity,
)
from app.modules.agent.infrastructure.models import ConversationModel, MessageModel

#: How far back to look for a transcript of the file `listen` was asked for.
#: Bounded because a long-running chat holds thousands of messages and the
#: answer, when there is one, is nearly always the message being answered.
_VOICE_TRANSCRIPT_LOOKBACK = 50


class Messages(UnitOfWorkRepository):
    """Appending to a conversation's transcript, and reading it."""

    async def append_message(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None,
        draft: MessageDraft,
    ) -> MessageEntity:
        lock_result = await self.session.execute(
            select(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .with_for_update()
        )
        conversation = lock_result.scalar_one()
        # Archiving says "I am done with this"; a new message says otherwise,
        # whoever wrote it. Without this an archived conversation can still be
        # live -- a Slack thread is found by its origin and keeps receiving, a
        # run that was mid-flight when it was archived still answers -- and the
        # one place that would show you is the list it has been removed from.
        # The row is already locked FOR UPDATE here, and every writer in the
        # module goes through this method, so this is the one place it belongs.
        if conversation.is_archived:
            conversation.is_archived = False
        # Same lock: a person's message or a notification moves the history
        # list. The agent's own messages do not (`conversation_activity`).
        if is_activity(draft):
            _touch_activity(conversation)
        sequence_result = await self.session.execute(
            select(func.coalesce(func.max(MessageModel.sequence), -1)).where(
                MessageModel.conversation_id == conversation_id
            )
        )
        sequence = int(sequence_result.scalar_one()) + 1
        model = MessageModel(
            conversation_id=conversation.id,
            agent_run_id=agent_run_id,
            sequence=sequence,
            role=draft.role.value,
            kind=draft.kind.value,
            text=draft.text,
            tool_name=draft.tool_name,
            tool_call_id=draft.tool_call_id,
            tool_args=(
                to_json_value(draft.tool_args) if draft.tool_args is not None else None
            ),
            tool_result=(
                to_json_value(draft.tool_result)
                if draft.tool_result is not None
                else None
            ),
            message_metadata=draft.metadata,
            **MessageModel.state_values(draft.state),
        )
        self.session.add(model)
        await self.session.flush()
        if _opens_a_pause(draft) and agent_run_id is not None and draft.pause:
            # The pause a call opens is written with it, in this transaction:
            # there is no moment at which the call is on record and nothing
            # knows it is waiting. A wait's row is its tool's, written beside
            # its timer instead.
            await Pauses(self.uow).open_for_call(
                draft.pause,
                conversation_id=conversation.id,
                run_id=agent_run_id,
                pod_id=conversation.pod_id,
                tool_call_id=draft.tool_call_id or "",
                tool_name=draft.tool_name or "",
            )
        return model.to_entity()

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[MessageEntity], int | None]:
        stmt = select(MessageModel).where(
            MessageModel.conversation_id == conversation_id
        )
        if before_sequence is not None:
            stmt = stmt.where(MessageModel.sequence < before_sequence)
        if after_sequence is not None:
            stmt = stmt.where(MessageModel.sequence > after_sequence)
        stmt = stmt.order_by(MessageModel.sequence.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].sequence if has_more and rows else None
        return [row.to_entity() for row in reversed(rows)], next_cursor

    async def latest_message_sequence(self, conversation_id: UUID) -> int:
        """The sequence the newest message holds; -1 for an empty conversation."""
        return int(
            await self.session.scalar(
                select(func.coalesce(func.max(MessageModel.sequence), -1)).where(
                    MessageModel.conversation_id == conversation_id
                )
            )
        )

    async def find_user_message_by_client_id(
        self, *, conversation_id: UUID, client_message_id: str
    ) -> MessageEntity | None:
        """The message an earlier send with this client id stored, if any.

        ``uq_agent_message_client_id`` answers it. The caller holds the
        conversation lock, which is what makes "look, then store" one step for
        two copies of a send.
        """
        row = await self.session.scalar(
            select(MessageModel).where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.client_message_id == client_message_id,
            )
        )
        return row.to_entity() if row is not None else None

    async def find_existing_voice_transcript(
        self, conversation_id: UUID, paths: tuple[str, ...]
    ) -> str | None:
        """A transcript this conversation already holds for one of ``paths``.

        Inbound voice notes are transcribed once, at ingress, before the agent
        is asked anything -- their words arrive as the message text. The agent
        is told so, and told not to transcribe the file again, and sometimes it
        does anyway: on dev, five `listen` calls landed on files whose
        transcript was already sitting in the same conversation. Each one paid a
        speech provider to produce text the run had been handed for free.

        An instruction is the wrong shape for that. A model is free to ignore
        one, and the cost of it doing so is real money and a slower answer, so
        this makes the second transcription unnecessary rather than discouraged
        -- `listen` answers from here and never reaches the provider.

        ``paths`` is a tuple because the agent may name the file either way: the
        prompt block carries the stored path (``/{user}/whatsapp/audio.ogg``)
        while a person, and a model reading a listing, would write ``/me/...``.
        Both spellings are the same file and both must find the transcript.
        """
        if not paths:
            return None
        rows = await self.session.execute(
            select(MessageModel.message_metadata)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.message_metadata.has_key("voice_transcripts"),
            )
            .order_by(MessageModel.sequence.desc())
            .limit(_VOICE_TRANSCRIPT_LOOKBACK)
        )
        wanted = set(paths)
        for (metadata,) in rows.all():
            for item in (metadata or {}).get("voice_transcripts") or []:
                if not isinstance(item, dict) or item.get("failed"):
                    continue
                text = str(item.get("text") or "").strip()
                if text and str(item.get("path") or "") in wanted:
                    return text
        return None


def _opens_a_pause(draft: MessageDraft) -> bool:
    return (
        draft.kind is MessageKind.TOOL_CALL
        and draft.pause is not None
        and draft.pause.kind is not PauseKind.WAIT
        and bool(draft.tool_call_id)
    )
