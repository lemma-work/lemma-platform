"""Leaving the organization is leaving each of its pods, announced per pod.

Deleting the organization membership cascades the pod memberships away in the
database, and a cascade says nothing: every module that lets go of a departed
pod member -- their channel seats, the contacts they looked after, their pod
accounts -- listens for the pod's ``PodMemberRemoved``, and never heard. So the
pod module removes them from each pod first, through its own removal path,
before the membership goes.
"""

from __future__ import annotations

from app.modules.identity.domain.organization_entities import OrganizationMemberEntity
from app.modules.identity.domain.ports import PodMembershipPort


async def leave_every_pod(
    pod_membership_port: PodMembershipPort | None, member: OrganizationMemberEntity
) -> None:
    if pod_membership_port is not None:
        await pod_membership_port.leave_organization_pods(
            organization_member_id=member.id, user_id=member.user_id
        )
