"""The two reads a client needs to render organization navigation.

Both exist to replace a request waterfall. The frontend used to fetch the
organization list, then one pod list per organization, then — for anything
richer than a name — apps and agents per pod. A user in five organizations paid
six sequential round trips before the sidebar could draw, and the per-pod calls
grew from there.

So there are two shapes, deliberately not one:

``load_navigation`` is the whole sidebar in a single call: every organization
the caller belongs to and the pods within each, carrying names and ids and
nothing else. It stays small however many organizations someone is in.

``load_organization_home`` is one organization in detail — its pods with their
apps, agents and the caller's roles. Detail is per organization on purpose: a
single endpoint returning full detail for every organization would grow without
bound for exactly the users who have the most, which is the failure the small
shape above avoids.

Neither issues a query per pod. Both resolve the whole set at once through the
owning modules' contracts.

Lives at the application root rather than inside ``identity`` because it reads
across pod, apps and agent. Putting it in a module would make that module depend
on three others and close a dependency cycle; composition is where a view that
spans modules is allowed to be assembled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select

from app.modules.agent.contracts import (
    PodAgentSummary,
    list_agent_summaries_by_pod,
)
from app.modules.apps.contracts import PodAppSummary, list_app_summaries_by_pod
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.models.organization_models import (
    Organization,
    OrganizationMember,
)
from app.modules.pod.contracts import VisiblePod, list_visible_pods


@dataclass(frozen=True, slots=True)
class NavigationPod:
    id: UUID
    name: str
    icon_url: str | None


@dataclass(frozen=True, slots=True)
class NavigationOrganization:
    id: UUID
    name: str
    slug: str | None
    role: str
    pods: list[NavigationPod] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HomePod:
    id: UUID
    name: str
    description: str | None
    icon_url: str | None
    roles: list[str]
    apps: list[PodAppSummary]
    agents: list[PodAgentSummary]


@dataclass(frozen=True, slots=True)
class OrganizationHome:
    organization_id: UUID
    name: str
    slug: str | None
    role: str
    pods: list[HomePod]


@dataclass(frozen=True, slots=True)
class _Membership:
    organization_id: UUID
    organization_member_id: UUID
    role: str
    name: str
    slug: str | None


async def _memberships(
    *, session, user_id: UUID, organization_id: UUID | None = None
) -> list[_Membership]:
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
        _Membership(
            organization_id=org_id,
            organization_member_id=member_id,
            role=str(role),
            name=name,
            slug=slug,
        )
        for org_id, member_id, role, name, slug in rows
    ]


def _owned(memberships: list[_Membership]) -> set[UUID]:
    return {
        membership.organization_id
        for membership in memberships
        if membership.role == OrganizationRole.ORG_OWNER
    }


def _pods_by_organization(pods: list[VisiblePod]) -> dict[UUID, list[VisiblePod]]:
    grouped: dict[UUID, list[VisiblePod]] = {}
    for pod in pods:
        grouped.setdefault(pod.organization_id, []).append(pod)
    return grouped


async def load_navigation(*, session, user_id: UUID) -> list[NavigationOrganization]:
    """Every organization the caller belongs to, each with its pods. Two queries.

    Two regardless of how many organizations or pods there are — the point of
    the endpoint.
    """
    memberships = await _memberships(session=session, user_id=user_id)
    if not memberships:
        return []

    pods = await list_visible_pods(
        session=session,
        organization_ids=[m.organization_id for m in memberships],
        organization_member_ids_by_org={
            m.organization_id: m.organization_member_id for m in memberships
        },
        owned_organization_ids=_owned(memberships),
    )
    grouped = _pods_by_organization(pods)

    return [
        NavigationOrganization(
            id=membership.organization_id,
            name=membership.name,
            slug=membership.slug,
            role=membership.role,
            pods=[
                NavigationPod(id=pod.id, name=pod.name, icon_url=pod.icon_url)
                for pod in grouped.get(membership.organization_id, [])
            ],
        )
        for membership in memberships
    ]


async def load_organization_home(
    *, session, user_id: UUID, organization_id: UUID
) -> OrganizationHome | None:
    """One organization in full. None when the caller is not a member.

    Four queries: the membership, the pods with the caller's roles, the apps,
    and the agents. Fixed, not per pod.
    """
    memberships = await _memberships(
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
        owned_organization_ids=_owned(memberships),
    )
    pod_ids = [pod.id for pod in pods]
    apps = await list_app_summaries_by_pod(session=session, pod_ids=pod_ids)
    agents = await list_agent_summaries_by_pod(session=session, pod_ids=pod_ids)

    return OrganizationHome(
        organization_id=organization_id,
        name=membership.name,
        slug=membership.slug,
        role=membership.role,
        pods=[
            HomePod(
                id=pod.id,
                name=pod.name,
                description=pod.description,
                icon_url=pod.icon_url,
                roles=pod.roles,
                apps=apps.get(pod.id, []),
                agents=agents.get(pod.id, []),
            )
            for pod in pods
        ],
    )
