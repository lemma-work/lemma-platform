"""What one viewer may read: their conversation with an agent, and its messages.

Split out of ``ConversationRepository`` for the reason its other query mixins
were -- that class is at the architecture ratchet's per-file limit. These two
belong together: both answer a question asked on behalf of a particular person
rather than about the conversation in the abstract.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.orm import aliased

from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
from app.modules.agent.domain.entities import (
    Conversation as ConversationEntity,
    Message as MessageEntity,
)
from app.modules.agent.domain.value_objects import (
    ConversationType,
    MessageRole,
    WORKING_MESSAGE_KINDS,
)
from app.modules.agent.infrastructure.conversation_participant_store import (
    list_participants,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)

_DEFAULT_POD_AGENT_ID_SQL = literal_column(f"'{DEFAULT_POD_AGENT_ID}'::uuid")

#: Compared against the stored column, which holds the enum's value.
_WORKING_KIND_VALUES = tuple(kind.value for kind in WORKING_MESSAGE_KINDS)


class ConversationViewerQueriesMixin:
    async def find_persistent_conversation(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
    ) -> ConversationEntity | None:
        """The conversation this person is already having with this agent.

        Newest live root CHAT conversation for the key, or None when there has
        never been one. Reads through ``ix_agent_conv_user_pod_agent_roots``,
        whose columns are exactly this key -- the index has always described
        this question, it was just never asked.

        Deliberately not backed by a unique constraint. Every account already
        has many rows per key, so a unique index cannot be added without
        merging or discarding history, and neither is a migration's decision to
        make. The cost of leaving it out is that two simultaneous first
        messages could each create one; the consequence is a spare empty
        conversation that the next resolve does not pick, rather than a lost or
        mixed-up one.

        TASK and PROJECT conversations are excluded on purpose. A task is a
        unit of work that ends, and a project is a pinned group -- neither is
        the thing somebody means by "the conversation I am having with this
        agent".
        """
        agent_scope_id = func.coalesce(
            ConversationModel.agent_id, _DEFAULT_POD_AGENT_ID_SQL
        )
        model = await self.session.scalar(
            select(ConversationModel)
            .where(
                ConversationModel.user_id == user_id,
                ConversationModel.pod_id == pod_id,
                agent_scope_id == (agent_id or DEFAULT_POD_AGENT_ID),
                ConversationModel.parent_id.is_(None),
                ConversationModel.is_archived.is_(False),
                ConversationModel.conversation_type == ConversationType.CHAT.value,
                ConversationModel.origin_id.is_(None),
            )
            .order_by(ConversationModel.updated_at.desc(), ConversationModel.id.desc())
            .limit(1)
        )
        if model is None:
            return None
        entity = model.to_entity()
        entity.participants = await list_participants(self.session, model.id)
        return entity

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        viewer_id: UUID | None = None,
        owner_user_id: UUID | None = None,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[MessageEntity], int | None]:
        """Messages in sequence order, bounded to what this viewer may see.

        ``viewer_id`` is optional because not every caller is a reader: the
        approval scan and the runtime both need the whole log, and a filter
        applied there would hide a pending tool call from the machinery that
        has to resolve it. Passing None keeps the unfiltered behaviour.

        The filter is in the query rather than over the result, so a page of
        `limit` is a page of what the viewer can see. Filtering afterwards
        would return short pages whose length leaked how much was hidden.
        """
        # Always joined, not only when filtering: every message has to be able
        # to say which agent produced it, and that lives on the run.
        run = aliased(AgentRunModel)
        stmt = (
            select(MessageModel, run.agent_id)
            .outerjoin(run, MessageModel.agent_run_id == run.id)
            .where(MessageModel.conversation_id == conversation_id)
        )
        if viewer_id is not None:
            visible = [
                MessageModel.kind.notin_(_WORKING_KIND_VALUES),
                run.triggered_by_user_id == viewer_id,
            ]
            # A run with no recorded trigger predates the column, and the
            # backfill resolved those to the owner. Anyone else asking gets the
            # answer without the working, which is the safe direction.
            if owner_user_id is not None and viewer_id == owner_user_id:
                visible.append(run.triggered_by_user_id.is_(None))
            stmt = stmt.where(or_(*visible))
        if before_sequence is not None:
            stmt = stmt.where(MessageModel.sequence < before_sequence)
        if after_sequence is not None:
            stmt = stmt.where(MessageModel.sequence > after_sequence)
        stmt = stmt.order_by(MessageModel.sequence.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result)
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1][0].sequence if has_more and rows else None

        def _entity(row) -> MessageEntity:
            model, agent_id = row
            entity = model.to_entity()
            # The run's agent produced the *answer*, not the question. A user
            # message shares the run, so taking the join's value unconditionally
            # attributed a person's own words to whichever agent replied to them.
            if entity.role is not MessageRole.USER:
                entity.agent_id = agent_id
            return entity

        return [_entity(row) for row in reversed(rows)], next_cursor
