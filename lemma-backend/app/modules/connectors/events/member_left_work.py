"""A member left the pod, or the organization: their accounts leave with them.

Subscribed to the pod's ``PodMemberRemoved``. Every pod account they own there
is deleted, with its grants, and a SHARED connector that ran as one of them is
left with no account -- NEEDS_ACCOUNT -- until somebody still in the pod shares
one. Before pod accounts existed, a departed person's account kept powering
whatever agent it had been pinned to (audit M11).

And to identity's ``OrganizationMemberRemoved``. Leaving the organization
cascades every pod membership away in the database without a
``PodMemberRemoved``, so this does the same per-pod cleanup for each pod they
had an account in, then disconnects their org accounts themselves
(``services/member_departure.py``).

JOBs although they mostly write the main database: the fast-handler guard keeps
FAST items out of the connectors module entirely, which holds the provider
clients -- and the organization one does call providers, to revoke.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
)
from app.core.infrastructure.work import (
    registry as default_registry,
)
from app.modules.identity.contracts.organization_facts import OrganizationMemberRemoved
from app.modules.pod.contracts.lifecycle import PodMemberRemoved

CONNECTORS_MEMBER_LEFT = "connectors.member_left"
CONNECTORS_ORG_MEMBER_LEFT = "connectors.org_member_left"


class ConnectorsMemberLeftPayload(BaseModel):
    pod_id: UUID
    user_id: UUID


class ConnectorsOrgMemberLeftPayload(BaseModel):
    organization_id: UUID
    user_id: UUID


async def member_left(ctx: JobContext, payload: ConnectorsMemberLeftPayload) -> None:
    from app.modules.connectors.api.dependencies import get_pod_connector_service

    async with ctx.complete_work() as uow:
        await get_pod_connector_service(uow).member_left(
            pod_id=payload.pod_id, user_id=payload.user_id
        )


async def org_member_left(
    ctx: JobContext, payload: ConnectorsOrgMemberLeftPayload
) -> None:
    from app.modules.connectors.api.dependencies import (
        get_connector_service,
        get_pod_connector_service,
    )
    from app.modules.connectors.services import member_departure

    async with ctx.checkpoint() as uow:
        departure = await member_departure.plan(
            get_connector_service(uow),
            organization_id=payload.organization_id,
            user_id=payload.user_id,
        )
    # Providers first, with nothing checked out; a retry after a failed delete
    # asks them again, which revoking an already revoked grant tolerates.
    await member_departure.revoke(departure)
    async with ctx.complete_work() as uow:
        await get_pod_connector_service(uow).organization_member_left(
            user_id=payload.user_id, account_ids=departure.account_ids
        )


def register_member_left_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=CONNECTORS_MEMBER_LEFT,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ConnectorsMemberLeftPayload,
    )(member_left)
    registry.handler(
        kind=CONNECTORS_ORG_MEMBER_LEFT,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ConnectorsOrgMemberLeftPayload,
    )(org_member_left)


register_member_left_work(default_registry)
facts.subscribe(
    PodMemberRemoved,
    kind=CONNECTORS_MEMBER_LEFT,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"connectors-member-left:{fact.pod_id}:{fact.user_id}",
)
facts.subscribe(
    OrganizationMemberRemoved,
    kind=CONNECTORS_ORG_MEMBER_LEFT,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: (
        f"connectors-org-member-left:{fact.organization_id}:{fact.user_id}"
    ),
)
