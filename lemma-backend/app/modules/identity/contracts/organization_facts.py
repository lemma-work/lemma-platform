"""What the identity module announces when someone joins or leaves an organization.

``OrganizationMemberAdded`` is emitted in the transaction that makes the
membership, from the one place every way in passes through -- creating the
organization, joining it under its join policy, accepting an invitation, being
let into one of its pods from outside. What a newcomer is owed by being there --
the pods set to take in everyone who joins -- is work that commits with their
arrival.

``OrganizationMemberRemoved`` is emitted in the transaction that deletes the
membership, so whatever the person held *in that organization* -- their
connector accounts, and the pod accounts made from them -- is let go by work
that commits with the removal. Deleting the membership cascades their pod
memberships away in the database without a word, so before this a departure
left all of it in place unless they had first been removed from each pod.

Both carry ids only: the member's role, like the organization's size, is read
by whoever handles the fact, as it is then.

A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, org_namespace


class OrganizationMemberAdded(Fact):
    """Someone became a member of an organization, by whichever way in."""

    organization_id: UUID
    user_id: UUID

    def work_namespace(self) -> str:
        return org_namespace(self.organization_id)


class OrganizationMemberRemoved(Fact):
    """Someone stopped being a member of an organization."""

    organization_id: UUID
    user_id: UUID

    def work_namespace(self) -> str:
        return org_namespace(self.organization_id)


__all__ = ["OrganizationMemberAdded", "OrganizationMemberRemoved"]
