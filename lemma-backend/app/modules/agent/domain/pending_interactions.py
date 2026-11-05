"""What is waiting on a person, across their conversations.

A question, an approval (a spent budget's "keep going?" among them) and an
Agent Host permission request stop and wait for somebody. The first two end
their run and leave the conversation WAITING; a permission request pauses
*inside* a run that stays RUNNING, which is why a "waiting on you" view keyed
on WAITING never showed it.

Nothing here pushes or reminds: a pause is listed where the person looks until
it is answered, the person moves on, or it expires -- a question after 72
hours, an approval after 7 days, either as a DENY (``domain/pauses``,
PS-AGENT-020). A permission request has a tighter bound of the Agent Host's
own: it denies an unanswered request itself after
`AGENT_HOST_PERMISSION_DECISION_WINDOW`, so one older than that is no longer
one the person can answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID

from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import ConversationStatus

#: The host's `PERMISSION_DECISION_TIMEOUT` (desktop/agent-host). Not Lemma's
#: choice: past it the host has already denied the request.
AGENT_HOST_PERMISSION_DECISION_WINDOW = timedelta(minutes=30)

#: How much of a question or a title the list carries. The conversation has the
#: whole of it; this is what fits on one line of an inbox.
SUMMARY_CHARACTERS = 200


class PendingInteractionKind(str, Enum):
    """What the person is being asked for."""

    QUESTION = "QUESTION"
    APPROVAL = "APPROVAL"
    PERMISSION = "PERMISSION"

    @classmethod
    def of(cls, kind: PauseKind) -> PendingInteractionKind:
        if kind is PauseKind.QUESTION:
            return cls.QUESTION
        return cls.PERMISSION if kind is PauseKind.HOST_PERMISSION else cls.APPROVAL


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """One unanswered pause, and enough of its conversation to point at it."""

    message_id: UUID
    tool_call_id: str
    kind: PendingInteractionKind
    summary: str | None
    created_at: datetime
    conversation_id: UUID
    conversation_title: str | None
    conversation_status: ConversationStatus | None
    #: Absent for the pod's own assistant on rows written before it had one.
    agent_id: UUID | None
    agent_name: str | None


@dataclass(frozen=True, slots=True)
class PendingInteractionCursor:
    """Where the next page starts: after this pause, oldest first."""

    created_at: datetime
    message_id: UUID


def clipped_summary(text: str | None) -> str | None:
    """One line of the question or title, or ``None`` when there is none."""
    if text is None:
        return None
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    if len(collapsed) <= SUMMARY_CHARACTERS:
        return collapsed
    return collapsed[: SUMMARY_CHARACTERS - 1].rstrip() + "…"


__all__ = [
    "AGENT_HOST_PERMISSION_DECISION_WINDOW",
    "PendingInteraction",
    "PendingInteractionCursor",
    "PendingInteractionKind",
    "SUMMARY_CHARACTERS",
    "clipped_summary",
]
