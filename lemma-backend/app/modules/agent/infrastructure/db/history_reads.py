"""The history a run's prompt is built from: a read model over runs and messages.

The newest runs of a conversation with their sizes, the messages attached to
whichever of them survive the cap, and the run-less messages that ride along.

The slice of runs a prompt can possibly carry
----------------------------------------------

``runtime_history`` caps every conversation at ``MAX_HISTORY_AGENT_RUNS`` runs.
The cap was applied with ``runs[-60:]`` -- after every run of the conversation had been
read, and after a ``GROUP BY`` over every message in all of them had produced a
digest for each. A four-hundred-turn conversation paid for four hundred runs on
every single turn to send sixty.

Asking for the newest sixty instead is nearly the same query, with two things
that have to come back alongside it.

The **total** is one. The notice telling the model that older exchanges exist is
built from how many runs were removed, and a windowed list cannot know what it
was cut from; without the count the notice silently stops being accurate the
moment the window does any work.

The **run being resumed** is the other, and it is the one that can fail rather
than mislead. The runner finds it in the list it loaded and raises
``ConversationNotFoundError`` when it is absent -- so windowing alone turns a
resumed older run in a long conversation from a missing notice into a failed
run. It is fetched by id, in or out of the window.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select, union_all

from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    Message as MessageEntity,
    MessageKind,
    MessageRole,
    RuntimeHistoryWindow,
)
from app.modules.agent.infrastructure.models import AgentRunModel, MessageModel
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository


class HistoryReads(UnitOfWorkRepository):
    """Reads that assemble the history a run's prompt carries."""

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
        result. See the module docstring for why the total and the resumed
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

    async def load_unattached_messages(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int | None,
        before_sequence: int | None,
        limit: int,
        folded_limit: int,
    ) -> list[MessageEntity]:
        """What was written into the conversation outside any run, and is carried.

        Reading messages by run id never finds these, and three kinds are owed
        to the model. A notification -- a reminder, a report delivered to the
        person's chat -- without which the person's "yes" to it reached an agent
        that could not see what was asked. What somebody outside the pod said
        while a person had them (``held_turns``), without which the agent
        answering them next read their newest message as if there were no
        earlier thread. And what a contact merge copied in from the person's
        conversation that lost its seat (``folded_turns``), without which the
        agent answering the one that kept it read nothing of the other. A
        queued turn is not run-less: it joins the run it waits on.

        Oldest first: the newest ``limit`` of the first two kinds, and apart
        from them the newest ``folded_limit`` copies, so a long chat folded in
        cannot crowd the notifications out. A copy keeps the kind and marks of
        what it copies, and is counted as a copy whatever those are.

        ``after_sequence`` is the oldest message the prompt carries; one older
        than that is older than the history and stays out. ``before_sequence``
        is the turn being answered: one written after it arrived mid-run and
        must not end up after the user turn the model is replying to.
        """
        newest = union_all(
            _newest_unattached_ids(
                conversation_id,
                folded=False,
                limit=limit,
                after_sequence=after_sequence,
                before_sequence=before_sequence,
            ),
            _newest_unattached_ids(
                conversation_id,
                folded=True,
                limit=folded_limit,
                after_sequence=after_sequence,
                before_sequence=before_sequence,
            ),
        )
        rows = (
            await self.session.scalars(
                select(MessageModel)
                .where(MessageModel.id.in_(newest))
                .order_by(MessageModel.sequence)
                .limit(limit + folded_limit)
            )
        ).all()
        return [row.to_entity() for row in rows]


def _newest_unattached_ids(
    conversation_id: UUID,
    *,
    folded: bool,
    limit: int,
    after_sequence: int | None,
    before_sequence: int | None,
) -> Select:
    """The ids of the newest ``limit`` run-less messages of one side: copies a
    merge folded in, or the rest of the kinds carried.

    Each side is its own ``ORDER BY sequence DESC LIMIT`` so Postgres walks the
    conversation's sequence index backwards and stops at the bound. Numbering
    every candidate with a window function instead read and sorted all of a
    conversation's run-less rows, full text included, on every run.
    """
    is_copy = MessageModel.folded_from.is_not(None)
    side = (
        is_copy
        if folded
        else and_(
            ~is_copy,
            or_(
                MessageModel.kind == MessageKind.NOTIFICATION.value,
                MessageModel.held_for_a_person,
            ),
        )
    )
    query = select(MessageModel.id).where(
        MessageModel.conversation_id == conversation_id,
        MessageModel.agent_run_id.is_(None),
        side,
    )
    if after_sequence is not None:
        query = query.where(MessageModel.sequence > after_sequence)
    if before_sequence is not None:
        query = query.where(MessageModel.sequence < before_sequence)
    return query.order_by(MessageModel.sequence.desc()).limit(limit)


def newest_runs(conversation_id: UUID, limit: int) -> Select:
    """The newest ``limit`` runs of a conversation.

    Ordered descending so the limit takes the newest, and reversed by the caller
    -- the trims downstream read a list as chronological and the elision notice
    is attached to its first element.
    """
    return (
        select(AgentRunModel)
        .where(AgentRunModel.conversation_id == conversation_id)
        .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
        .limit(limit)
    )


def run_count(conversation_id: UUID) -> Select:
    """How many runs the conversation has, without reading any of them."""
    return (
        select(func.count())
        .select_from(AgentRunModel)
        .where(AgentRunModel.conversation_id == conversation_id)
    )
