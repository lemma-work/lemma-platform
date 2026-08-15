"""Approval resolution outcomes.

Its own module rather than a member of `value_objects`: that file is at the
architecture ratchet's size ceiling, and this is a distinct concept anyway --
the *result* of resolving an approval, as opposed to the decision a user made
(`AgentRunApprovalDecision`, which stays with the other value objects).
"""

from __future__ import annotations

from typing import NamedTuple

from app.modules.agent.domain.value_objects import AgentRunApprovalDecision


class ApprovalResolution(NamedTuple):
    """Approval status plus the authoritative (stored) decision.

    ``status`` is ``"resolved"`` when this call recorded the decision,
    ``"reconciled"`` when it only finished a prior half-done resume (the
    self-heal path), or ``"queued"`` when the decision is durable and a worker
    job owns the rest.
    """

    status: str
    decision: AgentRunApprovalDecision
