"""Approval resolution outcomes.

Its own module rather than a member of `value_objects`: that file is at the
architecture ratchet's size ceiling, and this is a distinct concept anyway --
the *result* of resolving an approval, as opposed to the decision a user made
(`AgentRunApprovalDecision`, which stays with the other value objects).
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple

from app.modules.agent.domain.value_objects import AgentRunApprovalDecision


class ApprovalResolutionStatus(str, Enum):
    """What resolving an approval did. Lowercase: it is the API's ``status``."""

    #: This call recorded the decision.
    RESOLVED = "resolved"
    #: The decision was already recorded; this call only finished a prior
    #: half-done resume (the self-heal path).
    RECONCILED = "reconciled"
    #: The decision is durable and a worker job owns the rest.
    QUEUED = "queued"


class ApprovalResolution(NamedTuple):
    """Approval status plus the authoritative (stored) decision."""

    status: ApprovalResolutionStatus
    decision: AgentRunApprovalDecision


#: In a DENY's response: nobody decided before the question's time ran out --
#: the run is told so, rather than that a person turned it down.
EXPIRED_UNANSWERED = "expired_unanswered"


def expired_unanswered(response: dict[str, object]) -> bool:
    return response.get(EXPIRED_UNANSWERED) is True


#: On the DENY recorded for a pause somebody moved past: nobody decided it
#: (``application/pauses/supersede``).
SUPERSEDED_KEY = "superseded_by_new_message"
