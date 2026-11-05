"""What the identity module announces when someone leaves an organization.

``OrganizationMemberRemoved`` is emitted in the transaction that deletes the
membership, so whatever the person held *in that organization* -- their
connector accounts, and the pod accounts made from them -- is let go by work
that commits with the removal. Deleting the membership cascades their pod
memberships away in the database without a word, so before this a departure
left all of it in place unless they had first been removed from each pod.

A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, org_namespace


class OrganizationMemberRemoved(Fact):
    """Someone stopped being a member of an organization."""

    organization_id: UUID
    user_id: UUID

    def work_namespace(self) -> str:
        return org_namespace(self.organization_id)


__all__ = ["OrganizationMemberRemoved"]
