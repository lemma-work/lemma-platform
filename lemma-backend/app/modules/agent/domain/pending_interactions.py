"""What is waiting on a person, across their conversations.

Three things stop and wait for somebody: an ``ask_user`` question, a
``request_approval``, and an Agent Host permission request -- the last written
as a ``request_approval`` call carrying the host's marker. The first two end
their run and leave the conversation WAITING; the third pauses *inside* a run
that stays RUNNING, which is why a "waiting on you" view keyed on WAITING never
showed it.

Nothing here pushes or reminds: a pause is listed where the person looks, and
stays listed until it is answered or the person moves on. Pauses never expire
(PS-AGENT-020). The one bound is the Agent Host's own: it denies an unanswered
permission request itself after `AGENT_HOST_PERMISSION_DECISION_WINDOW`, and the
agent carries on without it, so a request older than that is no longer one the
person can answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID

from app.modules.agent.domain.value_objects import ConversationStatus
from app.modules.agent.domain.pausing_tools import PausingKind

#: The host's `PERMISSION_DECISION_TIMEOUT` (desktop/agent-host). Not Lemma's
#: choice: past it the host has already denied the request.
AGENT_HOST_PERMISSION_DECISION_WINDOW = timedelta(minutes=30)

#: The pausing calls a person answers. `browser_sign_in` also waits, but on
#: somebody going somewhere to sign in, which its own link already says.
WAITING_ON_A_PERSON_TOOLS = (
    PausingKind.ASK_USER.value,
    PausingKind.REQUEST_APPROVAL.value,
)

#: How much of a question or a title the list carries. The conversation has the
#: whole of it; this is what fits on one line of an inbox.
SUMMARY_CHARACTERS = 200


class PendingInteractionKind(str, Enum):
    """What the person is being asked for."""

    QUESTION = "QUESTION"
    APPROVAL = "APPROVAL"
    PERMISSION = "PERMISSION"

    @classmethod
    def of(cls, *, tool_name: str, is_host_permission: bool) -> PendingInteractionKind:
        if tool_name == PausingKind.ASK_USER:
            return cls.QUESTION
        return cls.PERMISSION if is_host_permission else cls.APPROVAL


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
    "WAITING_ON_A_PERSON_TOOLS",
    "clipped_summary",
]
