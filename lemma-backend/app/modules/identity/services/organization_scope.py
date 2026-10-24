"""Who a member is in one organization, and every pod they may open there.

Every read that spans an organization's pods starts from the same three facts:
the caller's membership, the pods it lets them open, and what they may do in
each. Getting any of them wrong in a second place is how a listing shows a pod
somebody was never added to, so they are resolved here once -- in a fixed
handful of queries, whatever the pod count.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import Context
from app.core.authorization.listing_contexts import build_listing_contexts_for_pods
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.models.organization_models import (
    Organization,
    OrganizationMember,
)
from app.modules.pod.contracts import VisiblePod, list_visible_pods


@dataclass(frozen=True, slots=True)
class Membership:
    organization_id: UUID
    organization_member_id: UUID
    role: str
    name: str
    slug: str | None


async def memberships_of(
    *, session: AsyncSession, user_id: UUID, organization_id: UUID | None = None
) -> list[Membership]:
    """The caller's organization memberships, with the organization joined in."""
    query = (
        select(
            OrganizationMember.organization_id,
            OrganizationMember.id,
            OrganizationMember.role,
            Organization.name,
            Organization.slug,
        )
        .join(Organization, Organization.id == OrganizationMember.organization_id)
        .where(OrganizationMember.user_id == user_id)
    )
    if organization_id is not None:
        query = query.where(OrganizationMember.organization_id == organization_id)
    rows = (await session.execute(query.order_by(Organization.name))).all()
    return [
        Membership(
            organization_id=org_id,
            organization_member_id=member_id,
            role=str(role),
            name=name,
            slug=slug,
        )
        for org_id, member_id, role, name, slug in rows
    ]


def owned_organizations(memberships: list[Membership]) -> set[UUID]:
    return {
        membership.organization_id
        for membership in memberships
        if membership.role == OrganizationRole.ORG_OWNER
    }


@dataclass(frozen=True, slots=True)
class MemberScope:
    """One member's standing in one organization, ready to read pods with."""

    user_id: UUID
    membership: Membership
    #: Every pod they may open, by name.
    pods: list[VisiblePod]
    #: Their authorization in each of those pods, for projecting a listing.
    #: Built from the pods above, so a pod is in one exactly when it is in both.
    contexts: dict[UUID, Context]

    @property
    def pod_ids(self) -> list[UUID]:
        return [pod.id for pod in self.pods]

    @property
    def pod_names(self) -> dict[UUID, str]:
        return {pod.id: pod.name for pod in self.pods}

    @property
    def pod_member_ids(self) -> list[UUID]:
        """Their membership rows, in the pods they joined rather than only see."""
        return [pod.pod_member_id for pod in self.pods if pod.pod_member_id]


async def load_member_scope(
    *, session: AsyncSession, user_id: UUID, organization_id: UUID
) -> MemberScope | None:
    """The caller's scope in this organization. None when they are not a member.

    The pods are the ones ``pod.contracts.list_visible_pods`` lists -- every pod
    for an owner, the joined ones for everybody else -- and the contexts are
    built for exactly those in one go, never one ``build_user_context`` per pod.
    These contexts project listings; they are not for deciding a request
    (``listing_contexts``).
    """
    memberships = await memberships_of(
        session=session, user_id=user_id, organization_id=organization_id
    )
    if not memberships:
        return None
    membership = memberships[0]
    pods = await list_visible_pods(
        session=session,
        organization_ids=[organization_id],
        organization_member_ids_by_org={
            organization_id: membership.organization_member_id
        },
        owned_organization_ids=owned_organizations(memberships),
    )
    contexts = await build_listing_contexts_for_pods(
        session,
        user_id=user_id,
        organization_id=organization_id,
        organization_member_id=membership.organization_member_id,
        pod_member_ids_by_pod={pod.id: pod.pod_member_id for pod in pods},
    )
    return MemberScope(
        user_id=user_id, membership=membership, pods=pods, contexts=contexts
    )
