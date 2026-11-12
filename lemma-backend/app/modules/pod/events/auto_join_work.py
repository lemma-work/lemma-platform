"""Someone joined the organization: they join its default pods with it.

Subscribed to identity's ``OrganizationMemberAdded``, which every way into an
organization writes in the transaction that makes the membership. A pod whose
``auto_join_new_members`` is on, and that is open to the organization's
members, takes the newcomer in as a pod user -- the membership a self-join
makes -- the way a chat app's default channels take in everyone who arrives.
Owners are left out: they open every pod already. People who were in the
organization before a pod turned this on are not added; they can find it among
the pods open to them and join.

The organization's creator arrives before any of its pods exist, so for them
this finds nothing to do, as it does in every organization with no default pod.

A FAST item under the organization's namespace: main-database statements only,
committed with the item's removal, so it runs once -- and it is idempotent
besides, since a pod the person is already in is never offered.
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
)
from app.core.infrastructure.work import (
    registry as default_registry,
)
from app.modules.identity.contracts.organization_facts import OrganizationMemberAdded
from app.modules.identity.contracts.organizations import build_organization_membership
from app.modules.pod.infrastructure.open_pods import OpenPods
from app.modules.pod.infrastructure.pod_repositories import (
    PodJoinRequestRepository,
    PodMemberRepository,
    PodRepository,
)
from app.modules.pod.services.pod_join_request_service import PodJoinRequestService
from app.modules.pod.services.pod_role_service import PodRoleService

POD_AUTO_JOIN_ORG_MEMBER_ADDED = "pod.auto_join.org_member_added"


async def join_default_pods(ctx: FastContext, payload: OrganizationMemberAdded) -> None:
    await PodJoinRequestService(
        pod_join_request_repository=PodJoinRequestRepository(ctx.uow),
        pod_member_repository=PodMemberRepository(ctx.uow),
        pod_repository=PodRepository(ctx.uow),
        organization_repository=build_organization_membership(ctx.uow),
        pod_role_service=PodRoleService(ctx.uow),
        open_pods=OpenPods(ctx.uow),
    ).join_default_pods(
        organization_id=payload.organization_id, user_id=payload.user_id
    )


def register_auto_join_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=POD_AUTO_JOIN_ORG_MEMBER_ADDED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=OrganizationMemberAdded,
    )(join_default_pods)


register_auto_join_work(default_registry)
facts.subscribe(
    OrganizationMemberAdded,
    kind=POD_AUTO_JOIN_ORG_MEMBER_ADDED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"pod-auto-join:{fact.organization_id}:{fact.user_id}",
)
