"""What a person is told after tapping a decision on a native card.

The tap is a request, not the outcome. The pause it answers may already have
been settled -- most often denied on the person's behalf because they sent a
new message instead of tapping -- and the decision that stands is the stored
one. Acknowledging the tap rather than the outcome told somebody who pressed
Approve that it was "Done" when the action had been cancelled and never ran.
"""

from __future__ import annotations

from app.modules.agent.contracts.channels import (
    AgentRunApprovalDecision,
    ApprovalResolution,
)

DONE = "Done"
RUNNING_NOW = "Approved — running it now."
ALREADY_CANCELLED = (
    "This was already cancelled because you sent a new message — ask again."
)
ALREADY_APPROVED = "This was already approved, so it went ahead."
CONVERSATION_GONE = "That conversation is gone. Please ask again."


def _approves(decision: AgentRunApprovalDecision) -> bool:
    # Once and for-the-session are the same answer to "did it run"; telling
    # somebody who tapped one that the other "already" happened would be noise.
    return decision is not AgentRunApprovalDecision.DENY


def acknowledgement_for(
    tapped: AgentRunApprovalDecision, resolution: ApprovalResolution | None
) -> str:
    """The words for a tap, given the decision that actually stands."""
    if resolution is None:
        return CONVERSATION_GONE
    stands = AgentRunApprovalDecision(resolution.decision)
    if _approves(tapped) != _approves(stands):
        return ALREADY_APPROVED if _approves(stands) else ALREADY_CANCELLED
    if resolution.status == "queued":
        # The decision is durable but the action has not run: a worker job owns
        # it, and "Done" would be a promise that job has not kept yet.
        return RUNNING_NOW
    return DONE


__all__ = ["DONE", "RUNNING_NOW", "acknowledgement_for"]
