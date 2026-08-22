"""Rules that guard who is in an organization, and on what terms.

An invitation may carry a pod as well as a role. Accepting it used to add the
member first and *then* look the pod up, so an invitation naming a pod that had
since been deleted half-applied in silence: the person joined the organization,
the pod grant was dropped, and nothing said so. Resolving first means an
acceptance that cannot be honoured whole is refused whole, with the invitation
still pending and the refusal naming the pod. See PS-ONB-021.

The last-owner guard lives here for the same reason: demotion and removal are
two doors onto one rule, and stating it once is what stops the second being
left unlocked -- which is how self-removal stranded organizations with no owner
at all. See PS-ONB-041.

Both live beside the service rather than inside it because
``organization_service`` is already over the file-size ratchet, and because
each is a question worth naming.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.identity.domain.errors import (
    IdentityConflictError,
    IdentityValidationError,
    OrganizationConflictError,
)
from app.modules.identity.domain.organization_entities import (
    OrganizationMemberEntity,
    OrganizationRole,
)

DEFAULT_POD_ROLE = "POD_USER"


@dataclass(frozen=True)
class PodGrant:
    """A pod membership an acceptance has undertaken to create."""

    pod_id: UUID
    pod_role: str


async def resolve_pod_grant(
    *,
    pod_membership_port: object | None,
    pod_id: UUID | None,
    pod_role: str | None,
    organization_id: UUID,
) -> PodGrant | None:
    """The grant this invitation implies, or ``None`` if it names no pod.

    Raises rather than returning ``None`` when a pod is named but unusable --
    the caller must not proceed to write a membership it cannot complete.
    """
    if pod_id is None:
        return None
    if pod_membership_port is None:
        raise IdentityConflictError(
            "This invitation names a pod, but pod membership cannot be "
            "granted right now"
        )
    pod_organization_id = await pod_membership_port.get_pod_organization_id(pod_id)
    if pod_organization_id is None:
        raise IdentityConflictError(
            f"The pod named by this invitation no longer exists "
            f"({pod_id}); it cannot be granted"
        )
    if pod_organization_id != organization_id:
        raise IdentityValidationError(
            "The pod named by this invitation does not belong to the "
            "inviting organization"
        )
    return PodGrant(pod_id=pod_id, pod_role=pod_role or DEFAULT_POD_ROLE)


async def refuse_if_last_owner(
    repository: object, member: OrganizationMemberEntity, *, verb: str
) -> None:
    """Refuse when ``member`` is the only owner ``repository`` still counts.

    An organization with no owner cannot mint one -- every path that grants
    ORG_OWNER requires an existing owner to walk it -- so zero owners is
    permanent. Demotion and removal both reach it, and removal includes the
    self-removal that reads as harmless. See PS-ONB-041.

    The count locks the owner rows. A guard that reads a count and then writes
    is only a guard against one caller at a time; two owners leaving together
    would each be told the other is still there. See
    ``count_members_with_role_for_update``.
    """
    if member.role != OrganizationRole.ORG_OWNER:
        return
    owner_count = await repository.count_members_with_role_for_update(
        member.organization_id, OrganizationRole.ORG_OWNER
    )
    if owner_count <= 1:
        raise OrganizationConflictError(
            f"Cannot {verb} the last owner of the organization",
            code=OrganizationConflictError.LAST_OWNER,
        )
