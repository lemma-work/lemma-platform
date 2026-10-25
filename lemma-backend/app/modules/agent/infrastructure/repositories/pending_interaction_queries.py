"""Every pause waiting on a person, across one person's conversations.

One statement for the list and one predicate for "does this conversation have
one", so the inbox and the history list's badge cannot disagree about what
counts. The web client used to find these by listing WAITING conversations and
then asking each for its approvals -- a request per conversation, and blind to
an Agent Host permission request, whose conversation stays RUNNING.

A pause is waiting while nobody has decided it and no return has landed for
it: the same two facts `ConversationApprovalQueriesMixin` asks about one
conversation, asked here across many.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    exists,
    func,
    literal,
    or_,
    select,
    tuple_,
)
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.agent.domain.pending_interactions import (
    AGENT_HOST_PERMISSION_DECISION_WINDOW,
    WAITING_ON_A_PERSON_TOOLS,
    PendingInteraction,
    PendingInteractionCursor,
    PendingInteractionKind,
    PodPendingInteraction,
    clipped_summary,
)
from app.modules.agent.domain.value_objects import ConversationStatus, MessageKind
from app.modules.agent.infrastructure.models import (
    AgentApprovalDecisionModel,
    AgentModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.repositories.parked_pause_queries import (
    is_host_permission_call as _is_host_permission,
    in_a_run_suspended_while_parked,
)


def _summary_text() -> ColumnElement[str | None]:
    """The question asked, or the approval's title, read in place.

    Paths rather than the whole `tool_args`: a permission request carries the
    gated call's full input, and the list needs one line of it.
    """
    args = MessageModel.tool_args
    return func.coalesce(
        args[("questions", 0, "question")].astext,
        # `ask_user` as a hand-built or older call stored it, wrapped.
        args[("request", "questions", 0, "question")].astext,
        args["title"].astext,
    )


def pending_pause_conditions() -> list[ColumnElement[bool]]:
    """`MessageModel` is a pause waiting on the owner of `ConversationModel`.

    The status is half of it. A paused run leaves its conversation WAITING; a
    parked remote approval or an Agent Host permission request holds a run that
    is still RUNNING. A pause in a conversation that is neither was overtaken --
    the run failed or was stopped around it -- and is not one an answer can
    resume. A permission request is further bounded by the host's own timeout,
    unless Lemma ended its turn for it and left the conversation WAITING.
    """
    decided = exists().where(
        AgentApprovalDecisionModel.conversation_id == MessageModel.conversation_id,
        AgentApprovalDecisionModel.approval_id == MessageModel.tool_call_id,
    )
    # Aliased here rather than at import: aliasing configures the mappers, and
    # at import time not every model they relate to has been defined yet.
    returned_message = aliased(MessageModel)
    returned = exists().where(
        returned_message.conversation_id == MessageModel.conversation_id,
        returned_message.kind == MessageKind.TOOL_RETURN.value,
        returned_message.tool_call_id == MessageModel.tool_call_id,
    )
    running = ConversationModel.status == ConversationStatus.RUNNING.value
    waiting_or_running = ConversationModel.status.in_(
        [ConversationStatus.WAITING.value, ConversationStatus.RUNNING.value]
    )
    waiting = ConversationModel.status == ConversationStatus.WAITING.value
    still_answerable = (
        MessageModel.created_at > func.now() - AGENT_HOST_PERMISSION_DECISION_WINDOW
    )
    return [
        MessageModel.conversation_id == ConversationModel.id,
        MessageModel.kind == MessageKind.TOOL_CALL.value,
        MessageModel.tool_name.in_(WAITING_ON_A_PERSON_TOOLS),
        MessageModel.tool_call_id.is_not(None),
        ~decided,
        ~returned,
        or_(
            and_(~_is_host_permission(), waiting_or_running),
            and_(_is_host_permission(), running, still_answerable),
            # A turn Lemma ended for this request (the parked window) answers
            # it with a resume, not with the host, so the host's timeout no
            # longer bounds it.
            and_(_is_host_permission(), waiting, in_a_run_suspended_while_parked()),
        ),
    ]


def has_pending_interaction_column() -> ColumnElement[bool]:
    """Correlated on `ConversationModel`: does it hold a pause waiting on a person."""
    return exists().where(*pending_pause_conditions())


def _pending_page(
    *,
    user_id: UUID,
    in_pods: ColumnElement[bool],
    after: PendingInteractionCursor | None,
    limit: int,
) -> Select:
    """This person's pauses in the pods ``in_pods`` names, longest-waiting first.

    Their own conversations only, sub-agents' included: a child's question
    sits in a conversation they own and would otherwise be found by nobody.
    One statement for one pod and for many, so the two lists cannot disagree
    about what counts. One row past ``limit``, so the caller can tell a full
    page from the last one.
    """
    stmt = (
        select(
            MessageModel.id,
            MessageModel.tool_call_id,
            MessageModel.tool_name,
            MessageModel.created_at,
            _is_host_permission().label("is_host_permission"),
            _summary_text().label("summary"),
            ConversationModel.id.label("conversation_id"),
            ConversationModel.pod_id,
            ConversationModel.title,
            ConversationModel.status,
            ConversationModel.agent_id,
            AgentModel.name.label("agent_name"),
        )
        .select_from(MessageModel)
        .join(ConversationModel, ConversationModel.id == MessageModel.conversation_id)
        .outerjoin(AgentModel, AgentModel.id == ConversationModel.agent_id)
        .where(
            ConversationModel.user_id == user_id,
            in_pods,
            # Archiving is how a person says "not this one" -- Home's dismiss
            # does exactly that -- and a list that brought it straight back
            # would have no way to be cleared. A new message unarchives.
            ConversationModel.is_archived.is_(False),
            *pending_pause_conditions(),
        )
        .order_by(MessageModel.created_at, MessageModel.id)
        .limit(limit + 1)
    )
    if after is not None:
        stmt = stmt.where(
            tuple_(MessageModel.created_at, MessageModel.id)
            > tuple_(
                literal(after.created_at, MessageModel.created_at.type),
                literal(after.message_id, MessageModel.id.type),
            )
        )
    return stmt


async def list_pending_interactions(
    session: AsyncSession,
    *,
    user_id: UUID,
    pod_id: UUID,
    cursor: PendingInteractionCursor | None = None,
    limit: int = 50,
) -> tuple[list[PendingInteraction], PendingInteractionCursor | None]:
    """This person's pauses in this pod, longest-waiting first."""
    stmt = _pending_page(
        user_id=user_id,
        in_pods=ConversationModel.pod_id == pod_id,
        after=cursor,
        limit=limit,
    )
    rows = list((await session.execute(stmt)).all())
    page = [_pending(row) for row in rows[:limit]]
    next_cursor = (
        PendingInteractionCursor(
            created_at=page[-1].created_at, message_id=page[-1].message_id
        )
        if len(rows) > limit and page
        else None
    )
    return page, next_cursor


async def list_pending_interactions_in_pods(
    session: AsyncSession,
    *,
    user_id: UUID,
    pod_ids: Sequence[UUID],
    limit: int,
) -> tuple[list[PodPendingInteraction], bool]:
    """This person's pauses across these pods, longest-waiting first.

    The pods are the caller's to choose -- the ones it has already found this
    person may open -- and the conversations are this person's own in any of
    them, so nothing here widens what the per-pod list shows. Whether more
    were waiting than ``limit`` comes back beside the page.
    """
    if not pod_ids:
        return [], False
    stmt = _pending_page(
        user_id=user_id,
        in_pods=ConversationModel.pod_id.in_(list(pod_ids)),
        after=None,
        limit=limit,
    )
    rows = list((await session.execute(stmt)).all())
    return [
        PodPendingInteraction(pod_id=row.pod_id, pending=_pending(row))
        for row in rows[:limit]
    ], len(rows) > limit


async def conversations_with_pending_interaction(
    session: AsyncSession, conversation_ids: Sequence[UUID]
) -> set[UUID]:
    """Which of these conversations hold a pause waiting on a person."""
    if not conversation_ids:
        return set()
    result = await session.execute(
        select(ConversationModel.id).where(
            ConversationModel.id.in_(list(conversation_ids)),
            has_pending_interaction_column(),
        )
    )
    return set(result.scalars())


def _pending(row: Row) -> PendingInteraction:
    return PendingInteraction(
        message_id=row.id,
        tool_call_id=row.tool_call_id,
        kind=PendingInteractionKind.of(
            tool_name=row.tool_name, is_host_permission=bool(row.is_host_permission)
        ),
        summary=clipped_summary(row.summary),
        created_at=row.created_at,
        conversation_id=row.conversation_id,
        conversation_title=row.title,
        conversation_status=ConversationStatus(row.status) if row.status else None,
        agent_id=row.agent_id,
        agent_name=row.agent_name,
    )


__all__ = [
    "conversations_with_pending_interaction",
    "has_pending_interaction_column",
    "list_pending_interactions",
    "list_pending_interactions_in_pods",
    "pending_pause_conditions",
]
