"""The names an invitation listing shows, and what it costs to fill them in.

Split out of ``organization_service`` because that file is over the
architecture ratchet's per-file ceiling, and because this is a display concern
rather than a membership one: nothing here decides who may do what.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationEntity,
)


async def enrich_invitation_display_fields(
    invitations: Sequence[OrganizationInvitationEntity],
    *,
    organization_repository,
    pod_membership_port,
) -> list[OrganizationInvitationEntity]:
    """Fill in the names a listing shows, asking once per distinct id.

    Every invitation carries its organization's name, and every invitation in
    one listing belongs to the same organization -- so a page of a hundred read
    the same tenant row a hundred times to print one string.

    Pods stay per-invitation: a page can span them, and unlike the tenant they
    are genuinely different rows. Asking once per distinct pod is still what
    happens, so the common case of one pod costs one read rather than a
    hundred.
    """
    organizations = await organization_repository.get_many(
        {invitation.organization_id for invitation in invitations}
    )
    pods: dict[UUID, tuple[str, str | None, UUID] | None] = {}
    for invitation in invitations:
        organization = organizations.get(invitation.organization_id)
        if organization:
            invitation.organization_name = organization.name
        if invitation.pod_id is None or pod_membership_port is None:
            continue
        if invitation.pod_id not in pods:
            pods[
                invitation.pod_id
            ] = await pod_membership_port.get_pod_invitation_details(invitation.pod_id)
        pod_details = pods[invitation.pod_id]
        if pod_details:
            invitation.pod_name = pod_details[0]
            invitation.pod_description = pod_details[1]
    return list(invitations)
