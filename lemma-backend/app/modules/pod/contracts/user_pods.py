"""The pods one user can see, and what they are to that user.

Both organization navigation endpoints need the same two facts — which pods a
member may see, and their roles in each — so the rule lives here once rather
than being re-derived per caller. Getting it wrong in a second place is how a
listing ends up showing someone a pod they were never added to.

Visibility mirrors ``PodService.list_pods_by_organization``: an organization
owner sees every pod in the organization, everyone else sees only the pods they
are a member of.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.models import RoleAssignmentModel, RoleModel
from app.modules.pod.domain.visibility import normalize_role_list
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember


@dataclass(frozen=True, slots=True)
class VisiblePod:
    """A pod as it appears in a listing, with the caller's standing in it."""

    id: UUID
    organization_id: UUID
    name: str
    description: str | None
    icon_url: str | None
    updated_at: datetime
    #: The caller's pod-member row, absent when they see the pod as org owner
    #: without having joined it. That distinction is why `roles` can be empty.
    pod_member_id: UUID | None = None
    roles: list[str] = field(default_factory=list)


async def list_visible_pods(
    *,
    session,
    organization_ids: list[UUID],
    organization_member_ids_by_org: dict[UUID, UUID],
    owned_organization_ids: set[UUID],
) -> list[VisiblePod]:
    """Every pod the caller can see across the given organizations, in one query.

    One query for any number of organizations, because the alternative — a query
    per organization — is the fan-out that made the sidebar slow in the first
    place.

    An organization owner sees all of its pods; everyone else sees only pods
    joined through their organization membership. The join is LEFT so an owner's
    un-joined pods still come back, carrying no ``pod_member_id`` and therefore
    no pod roles.
    """
    if not organization_ids:
        return []

    member_ids = [
        organization_member_ids_by_org[org_id]
        for org_id in organization_ids
        if org_id in organization_member_ids_by_org
    ]
    visibility = Pod.organization_id.in_(sorted(owned_organization_ids))
    if member_ids:
        visibility = visibility | PodMember.id.isnot(None)

    rows = (
        await session.execute(
            select(
                Pod.id,
                Pod.organization_id,
                Pod.name,
                Pod.description,
                Pod.icon_url,
                Pod.updated_at,
                PodMember.id,
            )
            .select_from(Pod)
            .outerjoin(
                PodMember,
                (PodMember.pod_id == Pod.id)
                & (PodMember.organization_member_id.in_(member_ids or [None])),
            )
            .where(
                Pod.organization_id.in_(organization_ids),
                Pod.is_deleted.is_(False),
                visibility,
            )
            .order_by(Pod.name, Pod.id)
        )
    ).all()

    pods = [
        VisiblePod(
            id=pod_id,
            organization_id=organization_id,
            name=name,
            description=description,
            icon_url=icon_url,
            updated_at=updated_at,
            pod_member_id=pod_member_id,
        )
        for (
            pod_id,
            organization_id,
            name,
            description,
            icon_url,
            updated_at,
            pod_member_id,
        ) in rows
    ]
    await _attach_roles(session=session, pods=pods)
    return pods


async def _attach_roles(*, session, pods: list[VisiblePod]) -> None:
    """Fill in each pod's roles for the caller, in one query for all of them.

    Same shape as ``PodMemberRepository._member_roles_by_id``, which resolves
    roles for a page of members; this resolves them for one member across a page
    of pods.
    """
    member_ids = [pod.pod_member_id for pod in pods if pod.pod_member_id is not None]
    if not member_ids:
        return

    rows = (
        await session.execute(
            select(RoleAssignmentModel.principal_id, RoleModel.name)
            .join(RoleModel, RoleModel.id == RoleAssignmentModel.role_id)
            .where(
                RoleAssignmentModel.principal_type == "POD_MEMBER",
                RoleAssignmentModel.principal_id.in_(member_ids),
            )
            .order_by(RoleModel.name)
        )
    ).all()

    by_member: dict[UUID, list[str]] = defaultdict(list)
    for principal_id, role_name in rows:
        by_member[principal_id].append(role_name)

    for pod in pods:
        if pod.pod_member_id is None:
            continue
        pod.roles.extend(normalize_role_list(by_member.get(pod.pod_member_id, [])))
