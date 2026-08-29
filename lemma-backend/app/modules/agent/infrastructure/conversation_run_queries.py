"""Agent-run lookups used by the conversation repository.

Split out of ``repositories.py`` to keep that module under the architecture
ratchet's size limit, the same way ``file_recovery_queries`` was split out of
the datastore file repository.

These are the reads that answer questions *about* runs — which one is active,
which is newest, whether one is safe to replay — as opposed to the conversation
CRUD and the run lifecycle transitions next door. Grouping them here is also
what makes it obvious that they all resolve a single run: this module exists
because eager-loading whole run collections to answer them was the defect.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    Message as MessageEntity,
    MessageRole,
)
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    AgentRunStatus,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.repositories.conversation_status_repair import (
    list_conversations_stranded_by_a_finished_run,
)
from app.modules.agent.infrastructure.repository_status import (
    run_status_values_for_db as _run_status_values_for_db,
)
from app.modules.agent.infrastructure.run_projections import (
    ResumableAgentRunRef,
    StaleAgentRunRef,
    StrandedConversationRef,
)

_ACTIVE_AGENT_RUN_STATUS_VALUES = _run_status_values_for_db(ACTIVE_AGENT_RUN_STATUSES)

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

    async def claim_resumable_run(self, agent_run_id: UUID) -> bool:
        """Flip an interrupted run back to RUNNING, if the slot is free.

        The partial unique index on (conversation_id) where the status is active
        is the real lock. Checking first only turns the common case -- the person
        gave up waiting and started something else -- into a clean "no" instead
        of an integrity error, and the index still decides a genuine race.
        """
        run = (
            await self.session.execute(
                select(AgentRunModel.conversation_id, AgentRunModel.status).where(
                    AgentRunModel.id == agent_run_id
                )
            )
        ).one_or_none()
        if run is None or run.status != AgentRunStatus.INTERRUPTED.value:
            return False
        holder = (
            await self.session.execute(
                select(AgentRunModel.id)
                .where(
                    AgentRunModel.conversation_id == run.conversation_id,
                    AgentRunModel.status.in_(_ACTIVE_AGENT_RUN_STATUS_VALUES),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if holder is not None:
            return False
        result = await self.session.execute(
            update(AgentRunModel)
            .where(
                AgentRunModel.id == agent_run_id,
                AgentRunModel.status == AgentRunStatus.INTERRUPTED.value,
            )
            .values(status=AgentRunStatus.RUNNING.value)
        )
        await self.session.flush()
        return bool(result.rowcount)

    async def list_resumable_runs(
        self, *, limit: int = 200
    ) -> list[ResumableAgentRunRef]:
        """Runs a worker parked on its way out, oldest first."""
        result = await self.session.execute(
            select(
                AgentRunModel.id,
                AgentRunModel.conversation_id,
                ConversationModel.user_id,
                ConversationModel.pod_id,
                AgentRunModel.run_metadata,
            )
            .join(
                ConversationModel,
                ConversationModel.id == AgentRunModel.conversation_id,
            )
            .where(AgentRunModel.status == AgentRunStatus.INTERRUPTED.value)
            .order_by(AgentRunModel.started_at.asc())
            .limit(limit)
        )
        return [
            ResumableAgentRunRef(
                id=row.id,
                conversation_id=row.conversation_id,
                user_id=row.user_id,
                pod_id=row.pod_id,
                resume_attempts=int((row.run_metadata or {}).get("resume_attempts", 0)),
            )
            for row in result.all()
        ]

    async def record_resume_attempt(self, agent_run_id: UUID) -> None:
        """Count one resume, so a run that cannot survive a restart stops trying.

        Kept in `run_metadata` rather than a column: this is bookkeeping for a
        rare path, and a migration to count retries is a migration to maintain.
        """
        model = (
            await self.session.execute(
                select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
            )
        ).scalar_one_or_none()
        if model is None:
            return
        metadata = dict(model.run_metadata or {})
        metadata["resume_attempts"] = int(metadata.get("resume_attempts", 0)) + 1
        model.run_metadata = metadata
        await self.session.flush()

    async def list_stale_active_runs(
        self,
        *,
        cutoff_seconds: int,
        limit: int = 200,
    ) -> list[StaleAgentRunRef]:
        """List identities of active runs older than the post-timeout cutoff.

        Only IDs are selected: reconciliation does not execute the run, and stale
        legacy runtime JSON must not block a safe terminal status transition.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)
        result = await self.session.execute(
            select(AgentRunModel.id, AgentRunModel.conversation_id)
            .where(
                AgentRunModel.status.in_(_ACTIVE_AGENT_RUN_STATUS_VALUES),
                AgentRunModel.started_at < cutoff,
            )
            .order_by(AgentRunModel.started_at.asc())
            .limit(limit)
        )
        return [StaleAgentRunRef(*row) for row in result.all()]

    async def list_conversations_stranded_by_a_finished_run(
        self,
        *,
        cutoff_seconds: int,
        limit: int = 200,
    ) -> list[StrandedConversationRef]:
        """Conversations still active whose most recent run already finished.

        Implemented next to the write that settles them; see
        `repositories.conversation_status_repair`.
        """
        return await list_conversations_stranded_by_a_finished_run(
            self.session, cutoff_seconds=cutoff_seconds, limit=limit
        )

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
        self,
        agent_run_id: UUID,
    ) -> list[AgentRunEntity]:
        """Every run of the conversation, with sizes and timings but no messages.

        The runtime prompt keeps recent runs whole and elides older ones, but
        *which* runs are recent is decided only after the caller's trims have
        run -- and the surface age window keeps a run whose newest message is
        recent even when runs created after it are dropped, so it is a filter
        rather than a truncation and the surviving list is not a suffix.

        Deciding what to load from position alone therefore drops messages from
        a run the trim then keeps in full, without an elision notice, because
        the shortened list never reaches the elision branch. So the caller gets
        the shape first, decides, and asks for messages second.
        """
        conversation_id = (
            await self.session.execute(
                select(AgentRunModel.conversation_id).where(
                    AgentRunModel.id == agent_run_id
                )
            )
        ).scalar_one_or_none()
        if conversation_id is None:
            return []

        runs = list(
            (
                await self.session.execute(
                    select(AgentRunModel)
                    .where(AgentRunModel.conversation_id == conversation_id)
                    .order_by(AgentRunModel.created_at.asc(), AgentRunModel.id.asc())
                )
            ).scalars()
        )
        if not runs:
            return []

        digests = dict(
            (
                (row[0], (row[1], row[2]))
                for row in (
                    await self.session.execute(
                        select(
                            MessageModel.agent_run_id,
                            func.count(),
                            func.max(MessageModel.created_at),
                        )
                        .where(MessageModel.agent_run_id.in_([run.id for run in runs]))
                        .group_by(MessageModel.agent_run_id)
                    )
                ).all()
            )
        )

        entities: list[AgentRunEntity] = []
        for run in runs:
            entity = run.to_entity()
            count, newest = digests.get(run.id, (0, None))
            entity.messages = []
            entity.total_message_count = count
            entity.newest_message_at = newest
            entities.append(entity)
        return entities

    async def attach_runtime_history_messages(
        self,
        runs: list[AgentRunEntity],
        *,
        full_run_ids: set[UUID],
    ) -> list[AgentRunEntity]:
        """Fill in messages: whole for ``full_run_ids``, first and last for the rest.

        Three reads serve the elided runs -- two ``DISTINCT ON`` for each run's
        first and last message, and one for every user message in them, since
        those are never elided. All are answered by the (agent_run_id, sequence)
        index rather than by reading the runs.
        """
        if not runs:
            return runs
        elided_ids = [run.id for run in runs if run.id not in full_run_ids]

        messages: list[MessageModel] = []
        if full_run_ids:
            messages.extend(
                (
                    await self.session.execute(
                        select(MessageModel)
                        .where(MessageModel.agent_run_id.in_(full_run_ids))
                        .order_by(
                            MessageModel.agent_run_id, MessageModel.sequence.asc()
                        )
                    )
                )
                .scalars()
                .all()
            )
        if elided_ids:
            # The user's own messages are never elided, however old the run. An
            # agent that cannot see what it was asked drifts onto a different
            # task and then reports that task as the one requested -- which is
            # exactly how a request for one video became an hour spent building
            # another. Answered by (agent_run_id, sequence) like its neighbours.
            messages.extend(
                (
                    await self.session.execute(
                        select(MessageModel)
                        .where(
                            MessageModel.agent_run_id.in_(elided_ids),
                            MessageModel.role == MessageRole.USER.value,
                        )
                        .order_by(
                            MessageModel.agent_run_id, MessageModel.sequence.asc()
                        )
                    )
                )
                .scalars()
                .all()
            )
            for order in (MessageModel.sequence.asc(), MessageModel.sequence.desc()):
                messages.extend(
                    (
                        await self.session.execute(
                            select(MessageModel)
                            .where(MessageModel.agent_run_id.in_(elided_ids))
                            .distinct(MessageModel.agent_run_id)
                            .order_by(MessageModel.agent_run_id, order)
                        )
                    )
                    .scalars()
                    .all()
                )

        by_run: dict[UUID, list[MessageModel]] = {}
        seen: set[UUID] = set()
        for message in messages:
            # A one-message run is its own first and last; keep it once.
            if message.id in seen:
                continue
            seen.add(message.id)
            by_run.setdefault(message.agent_run_id, []).append(message)

        for run in runs:
            run.messages = [
                model.to_entity()
                for model in sorted(
                    by_run.get(run.id, []), key=lambda model: model.sequence
                )
            ]
        return runs

    async def get_agent_run(self, agent_run_id: UUID) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def run_has_only_user_messages(self, agent_run_id: UUID) -> bool:
        """Whether a run holds at least one message and none of them are replies.

        This is the message half of ``AgentRun.is_safely_retryable`` — a run is
        safe to retry only if the model never got far enough to say anything,
        so replaying it cannot duplicate assistant output or tool effects.

        Answered as one aggregate over ``ix_agent_message_run_sequence`` instead
        of loading the run's messages. Callers should check the status half
        first: a run that did not fail is not retryable regardless, so the
        common path never reaches this query at all.
        """
        row = (
            await self.session.execute(
                select(
                    func.count().label("total"),
                    func.count()
                    .filter(MessageModel.role != MessageRole.USER.value)
                    .label("non_user"),
                ).where(MessageModel.agent_run_id == agent_run_id)
            )
        ).one()
        return bool(row.total) and not row.non_user

    def _unclaimed_queued_messages(self, agent_run_id: UUID):
        """Messages that arrived after this run started and nobody has read yet.

        ``start`` stamps ``during_active_run`` on a message it appends to a run
        already in flight, because that run loaded its history before the
        message existed. ``steered_into_run`` is stamped back by whoever
        delivered it into a model request, so the same predicate answers both
        questions that matter: what to steer in next, and what is still owed an
        answer once the run ends.

        Compared as text rather than cast to boolean, because the column is free
        JSONB -- a cast would raise on a row where something else wrote a
        non-boolean under that key, and a miscount is the better failure.
        """
        return (
            MessageModel.agent_run_id == agent_run_id,
            MessageModel.role == MessageRole.USER.value,
            MessageModel.message_metadata["during_active_run"].astext == "true",
            MessageModel.message_metadata["steered_into_run"].astext.is_(None),
        )

    async def count_queued_user_messages(self, agent_run_id: UUID) -> int:
        """How many of this run's queued messages are still unanswered.

        Counted over ``ix_agent_message_run_sequence`` rather than loading the
        run's messages, and asked once when a run ends -- so the answer is
        normally zero and costs one indexed aggregate.
        """
        return int(
            await self.session.scalar(
                select(func.count()).where(
                    *self._unclaimed_queued_messages(agent_run_id)
                )
            )
            or 0
        )

    async def claim_queued_user_messages(
        self, agent_run_id: UUID
    ) -> list[MessageEntity]:
        """Take the messages that arrived mid-run, and mark them taken.

        Claimed and read in one statement so a message can be delivered into the
        model exactly once. Whoever claims them owes the person an answer: if
        the run then dies without replying, the row stays claimed and the
        completion sweep will not pick it up either -- but the run is FAILED, so
        the person is told, which is the recovery that was already there.

        Returns them in the order they were appended, which the caller relies on
        to keep several bubbles of one message in sequence.
        """
        stamped = MessageModel.message_metadata.op("||")(
            func.jsonb_build_object("steered_into_run", str(agent_run_id))
        )
        rows = (
            await self.session.execute(
                update(MessageModel)
                .where(*self._unclaimed_queued_messages(agent_run_id))
                .values(message_metadata=stamped)
                .returning(MessageModel)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        return sorted(
            (row.to_entity() for row in rows), key=lambda message: message.sequence
        )

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
