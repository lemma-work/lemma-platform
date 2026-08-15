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

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    MessageRole,
)
from app.modules.agent.domain.value_objects import ACTIVE_AGENT_RUN_STATUSES
from app.modules.agent.infrastructure.models import AgentRunModel, MessageModel
from app.modules.agent.infrastructure.repository_status import (
    run_status_values_for_db as _run_status_values_for_db,
)
from app.modules.agent.infrastructure.run_projections import StaleAgentRunRef

_ACTIVE_AGENT_RUN_STATUS_VALUES = _run_status_values_for_db(ACTIVE_AGENT_RUN_STATUSES)


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

    async def load_runtime_history_by_run_id(
        self,
        agent_run_id: UUID,
        *,
        full_run_count: int,
    ) -> list[AgentRunEntity]:
        """Runs for the conversation, carrying only the messages history needs.

        The runtime prompt keeps every message of the most recent
        ``full_run_count`` runs and elides each older run to its first and last
        message. Loading the whole transcript to then discard most of it is what
        made a long conversation slow to start: messages present at run time
        measured p50 12, p90 271 and p99 13688, each row carrying its text and
        its tool argument and result JSON.

        So the shape is loaded instead of filtered. Older runs come back with
        two messages and a ``total_message_count`` saying how many there really
        were, which is what the elision notice counts and what the surface
        history budget measures.

        Choosing the full runs from the untrimmed list is safe because every
        trim the caller then applies -- the surface age window and the message
        budget -- removes runs from the front and always keeps the last, so the
        trimmed list is a suffix of this one and its most recent runs are a
        subset of these.
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

        run_ids = [run.id for run in runs]
        full_ids = set(run_ids[-full_run_count:]) if full_run_count > 0 else set()
        elided_ids = [run_id for run_id in run_ids if run_id not in full_ids]

        counts = dict(
            (
                await self.session.execute(
                    select(MessageModel.agent_run_id, func.count())
                    .where(MessageModel.agent_run_id.in_(run_ids))
                    .group_by(MessageModel.agent_run_id)
                )
            ).all()
        )

        messages: list[MessageModel] = []
        if full_ids:
            messages.extend(
                (
                    await self.session.execute(
                        select(MessageModel)
                        .where(MessageModel.agent_run_id.in_(full_ids))
                        .order_by(
                            MessageModel.agent_run_id, MessageModel.sequence.asc()
                        )
                    )
                )
                .scalars()
                .all()
            )
        if elided_ids:
            # First and last per run, both served by the
            # (agent_run_id, sequence) index rather than by reading the run.
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

        entities: list[AgentRunEntity] = []
        for run in runs:
            entity = run.to_entity()
            entity.messages = [
                model.to_entity()
                for model in sorted(
                    by_run.get(run.id, []), key=lambda model: model.sequence
                )
            ]
            entity.total_message_count = counts.get(run.id, 0)
            entities.append(entity)
        return entities

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

