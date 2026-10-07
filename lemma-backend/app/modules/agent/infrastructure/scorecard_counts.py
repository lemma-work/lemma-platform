"""What a teammate asked people, counted for its scorecard.

Two counts from the conversation tables: how people decided the teammate's
requests for approval, and how many of its questions are still waiting on
somebody. One aggregate statement each, so neither result grows with what it
reads.

**Scoped to conversations the caller may read** (``readable_by``, the history
list's own rule), and that is the whole authorization story. A conversation is
its owner's; nobody else can open it. Counting across the whole pod instead
would report other people's private conversations -- how often they were
asked, how they decided, what they left unanswered -- to whoever asked for the
week's numbers. So the scorecard reads the caller's share of the teammate's
work, which is the same view the caller gets everywhere else.

"Waiting on a person" means an unanswered ``ask_user``, ``request_approval`` or
``browser_sign_in`` -- ``USER_PAUSING_TOOL_NAMES``. Not a row in
``agent_conversation_waits``: every wait type there resolves on a timer, a
process or a sub-agent, with nobody involved, and a person's pause was kept out
of that table on purpose so it has one source of truth -- the pausing call, and
the decision or return that answers it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import ColumnElement

from app.modules.agent.domain.pausing_tools import (
    SUPERSEDED_BY_NEW_MESSAGE,
    USER_PAUSING_TOOL_NAMES,
)
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    MessageKind,
)
from app.modules.agent.infrastructure.models import (
    AgentApprovalDecisionModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.repositories.conversation_list_page import (
    readable_by,
)

_APPROVED = (
    AgentRunApprovalDecision.APPROVE_ONCE.value,
    AgentRunApprovalDecision.APPROVE_FOR_SESSION.value,
)
_DECIDED = (*_APPROVED, AgentRunApprovalDecision.DENY.value)
_REQUEST_APPROVAL = "request_approval"


@dataclass(frozen=True, slots=True)
class ApprovalTally:
    approved: int
    decided: int


@dataclass(frozen=True, slots=True)
class QuestionTally:
    #: Asked at least a day before the window closed, and unanswered when it did.
    still_waiting: int
    #: Asked inside the window, answered or not.
    asked: int


def _asked_for_approval(
    decision: type[AgentApprovalDecisionModel],
) -> ColumnElement[bool]:
    """Whether the call a decision answers was a ``request_approval``.

    The call says, not the decision row: its ``tool_name`` names the *wrapped*
    tool for an approval, and a sign-in records the same fallback an approval
    does, so the row alone cannot tell a verdict from a sign-in.
    """
    call = aliased(MessageModel)
    return exists().where(
        call.conversation_id == decision.conversation_id,
        call.tool_call_id == decision.approval_id,
        call.kind == MessageKind.TOOL_CALL.value,
        call.tool_name == _REQUEST_APPROVAL,
    )


async def tally_approvals(
    session: AsyncSession,
    *,
    pod_id: UUID,
    user_id: UUID,
    start: datetime,
    end: datetime,
) -> ApprovalTally:
    """Requests for approval a person decided in ``[start, end)``.

    Only ``request_approval``. An ``ask_user`` answer and a sign-in are stored
    in the same table, as decisions, but neither is a person judging the
    teammate's work.

    A DENY the platform recorded because the person moved on without answering
    (``SUPERSEDED_BY_NEW_MESSAGE``) is left out: nobody chose it, and counting
    it would score a request nobody looked at as a refusal.
    """
    decision = AgentApprovalDecisionModel
    # Containment rather than a cast of the value: a response is whatever the
    # person sent, and a cast is one malformed answer away from failing the
    # whole count.
    chosen = or_(
        decision.response.is_(None),
        not_(decision.response.contains({SUPERSEDED_BY_NEW_MESSAGE: True})),
    )
    row = (
        await session.execute(
            select(
                func.count().filter(decision.decision.in_(_APPROVED)),
                func.count(),
            )
            .select_from(decision)
            .join(ConversationModel, ConversationModel.id == decision.conversation_id)
            .where(
                readable_by(user_id=user_id, pod_id=pod_id),
                decision.created_at >= start,
                decision.created_at < end,
                decision.decision.in_(_DECIDED),
                chosen,
                _asked_for_approval(decision),
            )
        )
    ).one()
    approved, decided = row
    return ApprovalTally(approved=int(approved), decided=int(decided))


def _answered_before(end: datetime) -> ColumnElement[bool]:
    """Whether the asked call had an answer by ``end``.

    Either a decision or a tool return answers it -- the rule
    ``unresolved_pausing_call_ids`` applies. A decision is how a person answers
    a pause; a return with no decision is a call that never paused at all, as
    on a harness that cannot suspend mid-call and answers with guidance.
    Both are bounded by ``end`` so a week counted later reads what was true
    when it closed.
    """
    reply = aliased(MessageModel)
    decision = aliased(AgentApprovalDecisionModel)
    decided = exists().where(
        decision.conversation_id == MessageModel.conversation_id,
        decision.approval_id == MessageModel.tool_call_id,
        decision.created_at < end,
    )
    returned = exists().where(
        reply.conversation_id == MessageModel.conversation_id,
        reply.tool_call_id == MessageModel.tool_call_id,
        reply.kind == MessageKind.TOOL_RETURN.value,
        reply.created_at < end,
    )
    return or_(decided, returned)


async def tally_open_questions(
    session: AsyncSession,
    *,
    pod_id: UUID,
    user_id: UUID,
    start: datetime,
    end: datetime,
    patience: timedelta,
) -> QuestionTally:
    """Questions still waiting on a person when the window closed.

    A question counts against the week once it has waited ``patience`` with no
    answer, however long ago it was asked: one asked last month and never
    answered is still waiting, and is exactly what the measure is for. The
    total is the questions asked during the window, so a quiet week reads as
    one rather than as a perfect one.
    """
    asked_at = MessageModel.created_at
    still_waiting = and_(asked_at < end - patience, not_(_answered_before(end)))
    asked_in_window = and_(asked_at >= start, asked_at < end)
    row = (
        await session.execute(
            select(
                func.count().filter(still_waiting),
                func.count().filter(asked_in_window),
            )
            .select_from(MessageModel)
            .join(
                ConversationModel, ConversationModel.id == MessageModel.conversation_id
            )
            .where(
                readable_by(user_id=user_id, pod_id=pod_id),
                MessageModel.kind == MessageKind.TOOL_CALL.value,
                MessageModel.tool_name.in_(USER_PAUSING_TOOL_NAMES),
                MessageModel.tool_call_id.is_not(None),
                asked_at < end,
            )
        )
    ).one()
    waiting, asked = row
    return QuestionTally(still_waiting=int(waiting), asked=int(asked))


__all__ = [
    "ApprovalTally",
    "QuestionTally",
    "tally_approvals",
    "tally_open_questions",
]
