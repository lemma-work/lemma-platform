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
    RuntimeHistoryWindow,
)
from app.modules.agent.domain.queued_messages import STEERED_INTO_RUN
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ACTIVE_AGENT_RUN_STATUSES,
)
from app.modules.agent.infrastructure.runtime_history_queries import (
    RuntimeHistoryQueries,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    MessageModel,
)
from app.modules.agent.infrastructure.queued_message_queries import (
    in_order,
    stamp,
    unclaimed_queued_messages,
)
from app.modules.agent.infrastructure.repositories.conversation_status_repair import (
    list_conversations_stranded_by_a_finished_run,
)
from app.modules.agent.infrastructure.repository_status import (
    run_status_values_for_db as _run_status_values_for_db,
)
from app.modules.agent.domain.run_projections import (
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

    async def list_active_runs_pending_liveness(
        self,
        *,
        cutoff_seconds: int,
        decided_after_seconds: int,
        limit: int = 200,
    ) -> list[StaleAgentRunRef]:
        """Active runs whose age alone settles nothing.

        Older than ``cutoff_seconds``, so anything still executing has had
        several chances to renew its job heartbeat, and younger than
        ``decided_after_seconds``, where `list_stale_active_runs` takes over
        and fails them on age alone. A band rather than an open-ended window so
        every run is decided in exactly one place, and so the caller never asks
        the job layer about a run it has already reclaimed.
        """
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(AgentRunModel.id, AgentRunModel.conversation_id)
            .where(
                AgentRunModel.status.in_(_ACTIVE_AGENT_RUN_STATUS_VALUES),
                AgentRunModel.started_at < now - timedelta(seconds=cutoff_seconds),
                AgentRunModel.started_at
                >= now - timedelta(seconds=decided_after_seconds),
            )
            .order_by(AgentRunModel.started_at.asc())
            .limit(limit)
        )
        return [StaleAgentRunRef(*row) for row in result.all()]

    async def list_runs_stuck_stopping(
        self,
        *,
        cutoff_seconds: int,
        limit: int = 200,
    ) -> list[StaleAgentRunRef]:
        """Stops that nobody picked up.

        STOP_REQUESTED is an active status, so such a run keeps the one active
        run slot: a new message attaches to the dying run and starts nothing,
        and Retry refuses. A live worker now acts on a stop within a second, so
        one still sitting here means the worker never will -- and until this,
        the only thing that freed the conversation was the orphan sweep, an hour
        after the run *started*.

        Keyed on `updated_at`, which is when the stop was written, rather than
        `started_at`: how long the run had been going says nothing about how
        long the stop has been ignored.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)
        result = await self.session.execute(
            select(AgentRunModel.id, AgentRunModel.conversation_id)
            .where(
                AgentRunModel.status == AgentRunStatus.STOP_REQUESTED.value,
                AgentRunModel.updated_at < cutoff,
            )
            .order_by(AgentRunModel.updated_at.asc())
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

    async def load_unattached_notifications(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int | None,
        before_sequence: int | None,
        limit: int,
    ) -> list[MessageEntity]:
        return await RuntimeHistoryQueries(self.session).load_unattached_notifications(
            conversation_id,
            after_sequence=after_sequence,
            before_sequence=before_sequence,
            limit=limit,
        )

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

    async def count_queued_user_messages(self, agent_run_id: UUID) -> int:
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
                select(func.count()).where(*unclaimed_queued_messages(agent_run_id))
            )
            or 0
        )

    async def claim_queued_user_messages(
        self,
        agent_run_id: UUID,
        *,
        into_run_id: UUID | None = None,
        message_ids: list[UUID] | None = None,
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
                    *unclaimed_queued_messages(agent_run_id),
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
