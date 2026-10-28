"""Which pods an organization shows a user, for the ``org`` live topic.

Core's ``org`` topic (``app/core/live/org_hub.py``) cannot read organizations
or pods; it asks this, declared on the module through ``LemmaModule.live``.
The answer is the organization's pod listing -- an owner sees every pod, anyone
else the pods they are in (``organization_navigation.organization_pod_ids``)
-- and core holds each pod to ``pod.read`` after.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.org_feeds import OrgAccess, OrgPods
from app.modules.identity.services.organization_navigation import (
    organization_pod_ids,
)


class OrganizationPodDirectory:
    async def pods_for(self, organization_id: UUID, user_id: UUID) -> OrgPods:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            pod_ids = await organization_pod_ids(
                session=uow.session, user_id=user_id, organization_id=organization_id
            )
        if pod_ids is None:
            return OrgPods(access=OrgAccess.FORBIDDEN)
        return OrgPods(access=OrgAccess.MEMBER, pod_ids=pod_ids)


__all__ = ["OrganizationPodDirectory"]
