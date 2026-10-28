"""Agent-run lookups used by the conversation repository.

Split out of ``repositories.py`` to keep that module under the architecture
ratchet's size limit, the same way ``file_status_counts`` was split out of
the datastore file repository.

These are the reads that answer questions *about* runs — which one is active,
which is newest, whether one is safe to replay — as opposed to the conversation
CRUD and the run lifecycle transitions next door. Grouping them here is also
what makes it obvious that they all resolve a single run: this module exists
because eager-loading whole run collections to answer them was the defect.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import aliased, selectinload

from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    Message as MessageEntity,
    MessageRole,
    RuntimeHistoryWindow,
)
from app.modules.agent.domain.queued_messages import (
    CLIENT_MESSAGE_ID,
    STEERED_INTO_RUN,
    WAITING_ON_ANSWER,
)
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    ConversationStatus,
    MessageKind,
)
from app.modules.agent.infrastructure.repositories.conversation_run_lifecycle import (
    lock_conversation_row,
)
from app.modules.agent.infrastructure.runtime_history_queries import (
    RuntimeHistoryQueries,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.domain.private_notes import PRIVATE_NOTE_KEY
from app.modules.agent.domain.conversation_metadata import (
    MANUAL_RETRY_SOURCE,
    SOURCE_KEY,
)
from app.modules.agent.infrastructure.queued_message_queries import (
    in_order,
    stamp,
    unclaimed_queued_messages,
)

_ACTIVE_AGENT_RUN_STATUS_VALUES = [status.value for status in ACTIVE_AGENT_RUN_STATUSES]

#: How far back to look for a transcript of the file `listen` was asked for.
#: Bounded because a long-running chat holds thousands of messages and the
#: answer, when there is one, is nearly always the message being answered.
_VOICE_TRANSCRIPT_LOOKBACK = 50


class ConversationRunQueriesMixin:
    """Run lookups mixed into ``ConversationRepository``.

    A mixin rather than a collaborator because callers hold one repository
    object and these share its session.
    """

    async def _latest_runs_for(
        self,
        conversation_ids: list[UUID],
    ) -> dict[UUID, AgentRunModel]:
        """The newest run of each conversation, in one query.

        ``DISTINCT ON`` is the one-statement form of "latest per group" in
        Postgres, and its ordering matches ``ix_agent_run_conversation_created``
        so the whole set comes back from the index rather than from N separate
        lookups or one collection-wide fan-out.
        """
        result = await self.session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.conversation_id.in_(conversation_ids))
            .distinct(AgentRunModel.conversation_id)
            .order_by(
                AgentRunModel.conversation_id,
                AgentRunModel.created_at.desc(),
                AgentRunModel.id.desc(),
            )
        )
        return {run.conversation_id: run for run in result.scalars()}

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

    async def list_agent_runs_with_messages(
        self,
        conversation_id: UUID,
    ) -> list[AgentRunEntity]:
        result = await self.session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.conversation_id == conversation_id)
            .options(selectinload(AgentRunModel.messages))
            .order_by(AgentRunModel.created_at.asc(), AgentRunModel.id.asc())
        )
        return [model.to_entity() for model in result.scalars()]

    async def list_agent_runs_with_messages_by_run_id(
        self,
        agent_run_id: UUID,
    ) -> list[AgentRunEntity]:
        conversation_id_result = await self.session.execute(
            select(AgentRunModel.conversation_id).where(
                AgentRunModel.id == agent_run_id
            )
        )
        conversation_id = conversation_id_result.scalar_one_or_none()
        if conversation_id is None:
            return []
        return await self.list_agent_runs_with_messages(conversation_id)

    async def load_runtime_history_digests_by_run_id(
        self, agent_run_id: UUID, *, limit: int
    ) -> RuntimeHistoryWindow:
        return await RuntimeHistoryQueries(
            self.session
        ).load_runtime_history_digests_by_run_id(agent_run_id, limit=limit)

    async def attach_runtime_history_messages(
        self, runs: list[AgentRunEntity], *, full_run_ids: set[UUID]
    ) -> list[AgentRunEntity]:
        return await RuntimeHistoryQueries(
            self.session
        ).attach_runtime_history_messages(runs, full_run_ids=full_run_ids)

    async def load_unattached_messages(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int | None,
        before_sequence: int | None,
        limit: int,
        folded_limit: int,
    ) -> list[MessageEntity]:
        return await RuntimeHistoryQueries(self.session).load_unattached_messages(
            conversation_id,
            after_sequence=after_sequence,
            before_sequence=before_sequence,
            limit=limit,
            folded_limit=folded_limit,
        )

    async def get_agent_run(self, agent_run_id: UUID) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
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
        on the run it retried -- so it is asked for by its ``source`` instead.
        Without that, a retry that failed too could never be retried again.
        """
        is_retry = (
            select(AgentRunModel.run_metadata[SOURCE_KEY].astext == MANUAL_RETRY_SOURCE)
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

    async def count_queued_user_messages(
        self,
        agent_run_id: UUID,
        *,
        notes: bool | None = None,
        after_sequence: int | None = None,
    ) -> int:
        """How many of this run's queued messages are still unanswered.

        Counted over ``ix_agent_message_run_sequence`` rather than loading the
        run's messages, and asked once when a run ends -- so the answer is
        normally zero and costs one indexed aggregate.

        A message handed to the host as a steer that never landed still counts:
        ``steer_dispatched_at`` is not a claim, so the follow-up turn is what
        answers it.
        """
        return int(
            await self.session.scalar(
                select(func.count()).where(
                    *unclaimed_queued_messages(
                        agent_run_id, notes=notes, after_sequence=after_sequence
                    )
                )
            )
            or 0
        )

    async def earliest_queued_is_note(
        self, agent_run_id: UUID, *, after_sequence: int | None = None
    ) -> bool | None:
        """Whether the oldest unanswered message queued behind this run is a
        private note; None when nothing is queued. A follow-up turn answers one
        kind, and the oldest message decides which goes first."""
        flag = await self.session.scalar(
            select(
                func.coalesce(
                    MessageModel.message_metadata[PRIVATE_NOTE_KEY].astext == "true",
                    False,
                )
            )
            .where(
                *unclaimed_queued_messages(agent_run_id, after_sequence=after_sequence)
            )
            .order_by(MessageModel.sequence)
            .limit(1)
        )
        return None if flag is None else bool(flag)

    async def claim_queued_user_messages(
        self,
        agent_run_id: UUID,
        *,
        into_run_id: UUID | None = None,
        message_ids: list[UUID] | None = None,
        notes: bool | None = None,
        as_run: bool = False,
        after_sequence: int | None = None,
    ) -> list[MessageEntity]:
        """Take the messages that arrived mid-run, and mark them taken.

        Claimed and read in one statement so a message can be delivered into the
        model exactly once. Whoever claims them owes the person an answer: if
        the run then dies without replying, the row stays claimed and the
        completion sweep will not pick it up either -- but the run is FAILED, so
        the person is told, which is the recovery that was already there.

        ``into_run_id`` is the run that will deliver them, when that is not the
        run they were queued behind: the follow-up turn claims its predecessor's
        queue, and the Agent Host harness reads that claim to know which
        messages its prompt has to carry.

        ``message_ids`` narrows the claim to messages the caller has in hand --
        an Agent Host dispatch claims the ones its prompt already carries, and
        must not claim one that arrived a moment later and is not in it.

        Returns them in the order they were appended, which the caller relies on
        to keep several bubbles of one message in sequence.
        """
        rows = (
            await self.session.execute(
                update(MessageModel)
                .where(
                    *unclaimed_queued_messages(
                        agent_run_id,
                        notes=notes,
                        as_run=as_run,
                        after_sequence=after_sequence,
                    ),
                    *(
                        (MessageModel.id.in_(message_ids),)
                        if message_ids is not None
                        else ()
                    ),
                )
                .values(
                    message_metadata=stamp(
                        **{STEERED_INTO_RUN: str(into_run_id or agent_run_id)}
                    )
                )
                .returning(MessageModel)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        return in_order(rows)

    async def mark_queued_waiting_on_answer(
        self, agent_run_id: UUID
    ) -> list[MessageEntity]:
        """Mark what is queued behind a run that ended on a question.

        Nothing answers those until the person answers the question, and
        without a mark they were drawn as plain sent messages with no reply
        coming. Returned so the caller can redraw them.
        """
        rows = (
            await self.session.execute(
                update(MessageModel)
                .where(
                    *unclaimed_queued_messages(agent_run_id),
                    MessageModel.message_metadata[WAITING_ON_ANSWER].astext.is_(None),
                )
                .values(
                    message_metadata=stamp(**{WAITING_ON_ANSWER: str(agent_run_id)})
                )
                .returning(MessageModel)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        return in_order(rows)

    async def find_user_message_by_client_id(
        self, *, conversation_id: UUID, client_message_id: str
    ) -> MessageEntity | None:
        """The message an earlier send with this client id stored, if any.

        Scoped to one conversation, so it reads that conversation's messages by
        their conversation index. The caller holds the conversation lock, which
        is what makes "look, then store" one step for two copies of a send.
        """
        metadata = MessageModel.message_metadata
        row = await self.session.scalar(
            select(MessageModel).where(
                MessageModel.conversation_id == conversation_id,
                metadata.has_key(CLIENT_MESSAGE_ID),
                metadata[CLIENT_MESSAGE_ID].astext == client_message_id,
            )
        )
        return row.to_entity() if row is not None else None

    async def latest_message_sequence(self, conversation_id: UUID) -> int:
        """The sequence the newest message holds; -1 for an empty conversation."""
        return int(
            await self.session.scalar(
                select(func.coalesce(func.max(MessageModel.sequence), -1)).where(
                    MessageModel.conversation_id == conversation_id
                )
            )
        )

    async def get_conversation_status(
        self, conversation_id: UUID
    ) -> ConversationStatus | None:
        """The status as stored now, never a copy this session already holds."""
        status = await self.session.scalar(
            select(ConversationModel.status).where(
                ConversationModel.id == conversation_id
            )
        )
        return ConversationStatus(status) if status is not None else None

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
