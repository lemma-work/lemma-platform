"""A run held open on a person, and the mark that ends it as WAITING.

A remote harness cannot pause mid tool call, so what a person is asked inside
an Agent Host turn is *parked* in that turn: an ``ask_user`` or
``request_approval`` Lemma recorded over MCP (``parked_in_run_id`` on the call),
or an ACP permission request the host holds open (the ``agent_host_permission``
marker in its arguments). These are the two reads and the one write the
parked window needs: how long the oldest such pause has gone unanswered, and
the mark that says its turn was ended for it.

Plain functions over a session: the Agent Host's parked window reads them
without a repository of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.agent.domain.agent_host_permissions import AGENT_HOST_PERMISSION_KEY
from app.modules.agent.domain.pausing_tools import (
    USER_PAUSING_TOOL_NAMES,
    ParkedSuspension,
)
from app.modules.agent.domain.value_objects import AgentRunStatus, MessageKind
from app.modules.agent.infrastructure.models import (
    AgentApprovalDecisionModel,
    AgentRunModel,
    MessageModel,
)


@dataclass(frozen=True, slots=True)
class ParkedPause:
    """The oldest unanswered pause a run is parked on, and the database's now.

    ``now`` comes from the same statement as ``asked_at``, so how long the
    pause has waited is measured on one clock however far this worker's own
    has drifted.
    """

    tool_call_id: str
    tool_name: str
    asked_at: datetime
    now: datetime


def is_host_permission_call() -> ColumnElement[bool]:
    """``MessageModel`` is an ACP permission request written as an approval.

    The marker must be an object, as ``agent_host_permission_request`` reads
    it; ``jsonb ? key`` alone would also match a bare string argument. The
    COALESCE keeps an ordinary call from reading as NULL -- neither a
    permission request nor not one -- which ``NOT`` would leave NULL too.
    """
    marker_type = func.jsonb_typeof(MessageModel.tool_args[AGENT_HOST_PERMISSION_KEY])
    return func.coalesce(marker_type, "absent") == "object"


def in_a_run_suspended_while_parked() -> ColumnElement[bool]:
    """Correlated on ``MessageModel``: its run was ended for a parked pause."""
    return exists().where(
        AgentRunModel.id == MessageModel.agent_run_id,
        AgentRunModel.suspended_while_parked.is_not(None),
    )


def _parked_in_run() -> ColumnElement[bool]:
    return MessageModel.parked_in_run_id.is_not(None)


def _unanswered() -> list[ColumnElement[bool]]:
    """Neither decided nor returned: the person has not acted on it."""
    returned = aliased(MessageModel)
    return [
        ~exists().where(
            AgentApprovalDecisionModel.conversation_id == MessageModel.conversation_id,
            AgentApprovalDecisionModel.approval_id == MessageModel.tool_call_id,
        ),
        ~exists().where(
            returned.conversation_id == MessageModel.conversation_id,
            returned.kind == MessageKind.TOOL_RETURN.value,
            returned.tool_call_id == MessageModel.tool_call_id,
        ),
    ]


async def oldest_parked_pause(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    agent_run_id: UUID,
) -> ParkedPause | None:
    """The run's oldest pause still waiting on a person, if it has one.

    Only pauses parked *inside* the run count. A ``wait_for`` already ends its
    run, and an in-process pause never leaves one running at all.
    """
    row = (
        await session.execute(
            select(
                MessageModel.tool_call_id,
                MessageModel.tool_name,
                MessageModel.created_at,
                func.now(),
            )
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.agent_run_id == agent_run_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
                MessageModel.tool_name.in_(USER_PAUSING_TOOL_NAMES),
                MessageModel.tool_call_id.is_not(None),
                or_(_parked_in_run(), is_host_permission_call()),
                *_unanswered(),
            )
            .order_by(MessageModel.sequence)
            .limit(1)
        )
    ).one_or_none()
    if row is None or row[0] is None or row[1] is None:
        return None
    return ParkedPause(
        tool_call_id=row[0], tool_name=row[1], asked_at=row[2], now=row[3]
    )


async def mark_suspended_while_parked(
    session: AsyncSession,
    *,
    agent_run_id: UUID,
    pause: ParkedPause,
) -> bool:
    """Record on a still-RUNNING run that its turn is being ended for ``pause``.

    False when the run has moved on -- a Stop already requested, or the run
    finished -- in which case nothing is ending it as WAITING: a Stop means
    stop, and a finished run needs nothing.
    """
    marked = await session.execute(
        update(AgentRunModel)
        .where(
            AgentRunModel.id == agent_run_id,
            AgentRunModel.status == AgentRunStatus.RUNNING.value,
        )
        .values(
            suspended_while_parked=ParkedSuspension(
                tool_call_id=pause.tool_call_id, tool_name=pause.tool_name
            ).to_json()
        )
        .returning(AgentRunModel.id)
        .execution_options(synchronize_session=False)
    )
    return marked.scalar_one_or_none() is not None


__all__ = [
    "ParkedPause",
    "is_host_permission_call",
    "mark_suspended_while_parked",
    "oldest_parked_pause",
    "in_a_run_suspended_while_parked",
]
