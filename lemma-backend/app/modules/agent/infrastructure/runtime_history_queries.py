"""Runtime-history reads, split from the run queries to keep both under the size ratchet.

What a prompt is built from: the newest runs of a conversation with their sizes,
the messages attached to whichever of them survive the cap, and the run-less
notifications that ride along. Delegated to by ``ConversationRepository``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    Message as MessageEntity,
    MessageKind,
    MessageRole,
    RuntimeHistoryWindow,
)
from app.modules.agent.infrastructure.models import AgentRunModel, MessageModel
from app.modules.agent.infrastructure.runtime_history_window import (
    newest_runs,
    run_count,
)


class RuntimeHistoryQueries:
    """Reads that assemble the history a run's prompt carries.

    A collaborator over the repository's session: the repository delegates to it
    so callers still hold one object.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_runtime_history_digests_by_run_id(
        self,
        agent_run_id: UUID,
        *,
        limit: int,
    ) -> RuntimeHistoryWindow:
        """The newest ``limit`` runs of a conversation, with their sizes.

        No messages: the runtime prompt keeps recent runs whole and elides older
        ones, and which is which is decided after the runs are capped. So the
        caller gets the shape first, decides, and asks for messages second.

        ``limit`` is the caller's own ceiling, applied here rather than to the
        result. See `runtime_history_window` for why the total and the resumed
        run come back with it.
        """
        conversation_id = (
            await self.session.execute(
                select(AgentRunModel.conversation_id).where(
                    AgentRunModel.id == agent_run_id
                )
            )
        ).scalar_one_or_none()
        if conversation_id is None:
            return RuntimeHistoryWindow(runs=[], total_runs=0, current_run=None)

        rows = list(
            (await self.session.execute(newest_runs(conversation_id, limit))).scalars()
        )
        rows.reverse()
        total_runs = int(
            (await self.session.execute(run_count(conversation_id))).scalar_one()
        )
        entities = await self._with_message_digests(rows)
        current_run = next(
            (entity for entity in entities if entity.id == agent_run_id), None
        )
        if current_run is None:
            # Older than the window. It is still the run being executed, and the
            # runner refuses the whole request when it cannot find it, so it is
            # fetched on its own -- one extra statement on the rare path rather
            # than a wider window on every one.
            resumed = (
                (
                    await self.session.execute(
                        select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
                    )
                )
                .scalars()
                .first()
            )
            if resumed is not None:
                current_run = (await self._with_message_digests([resumed]))[0]
        return RuntimeHistoryWindow(
            runs=entities, total_runs=total_runs, current_run=current_run
        )

    async def _with_message_digests(
        self, rows: list[AgentRunModel]
    ) -> list[AgentRunEntity]:
        """Hydrate runs carrying their message count.

        The count has to come from here rather than from ``len(run.messages)``:
        the loader deliberately fetches older runs down to two messages, so the
        list is not the size.
        """
        if not rows:
            return []
        digests = {
            row[0]: row[1]
            for row in (
                await self.session.execute(
                    select(MessageModel.agent_run_id, func.count())
                    .where(MessageModel.agent_run_id.in_([row.id for row in rows]))
                    .group_by(MessageModel.agent_run_id)
                )
            ).all()
        }
        entities: list[AgentRunEntity] = []
        for row in rows:
            entity = row.to_entity()
            entity.messages = []
            entity.total_message_count = digests.get(row.id, 0)
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

    async def load_unattached_notifications(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int | None,
        before_sequence: int | None,
        limit: int,
    ) -> list[MessageEntity]:
        """Notifications written into the conversation outside any run.

        A proactive message -- a reminder, a notification delivered to the
        person's chat -- belongs to no run, so reading messages by run id never
        finds it. The person's "yes" to it then arrives at an agent that cannot
        see what was asked. Only notifications: other run-less messages are
        queued turns, which the steering path already owns.

        ``after_sequence`` is the oldest message the prompt carries; a
        notification older than that is older than the history and stays out.
        ``before_sequence`` is the turn being answered: one written after it
        arrived mid-run and must not end up after the user turn the model is
        replying to.
        """
        query = select(MessageModel).where(
            MessageModel.conversation_id == conversation_id,
            MessageModel.agent_run_id.is_(None),
            MessageModel.kind == MessageKind.NOTIFICATION.value,
        )
        if after_sequence is not None:
            query = query.where(MessageModel.sequence > after_sequence)
        if before_sequence is not None:
            query = query.where(MessageModel.sequence < before_sequence)
        rows = (
            (
                await self.session.execute(
                    query.order_by(MessageModel.sequence.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [row.to_entity() for row in reversed(rows)]
