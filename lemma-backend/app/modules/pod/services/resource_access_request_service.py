"""Asking for one resource, and granting it.

The right-sized counterpart to ``PodJoinRequestService``. Approving a join
request mints pod membership with a default role; approving one of these writes
a single resource grant and leaves the requester a non-member — which is what
the sharer meant when they sent a link to one document.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.cache import invalidate_role_snapshot_cache
from app.core.authorization.context import ResourceType
from app.core.authorization.grants import replace_resource_grantee_grant
from app.core.authorization.sql_actions import read_action_for_resource
from app.modules.pod.domain.errors import PodAccessDeniedError, PodConflictError
from app.modules.pod.domain.pod_entities import (
    ResourceAccessRequestEntity,
    ResourceAccessRequestStatus,
)
from app.modules.pod.infrastructure.models.pod_models import ResourceAccessRequest


class ResourceAccessRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def request_access(
        self,
        *,
        pod_id: UUID,
        resource_type: ResourceType,
        resource_id: UUID,
        resource_name: str | None,
        requester_user_id: UUID,
        message: str | None = None,
    ) -> ResourceAccessRequestEntity:
        """Record an ask, or return the one already pending.

        Idempotent by design: the guest view offers this button on a page people
        refresh and re-open, and a queue of identical asks is a worse experience
        for the person who has to read it than for the one who sent them.
        """
        existing = await self._get_pending(
            pod_id=pod_id,
            resource_type=resource_type,
            resource_id=resource_id,
            requester_user_id=requester_user_id,
        )
        if existing is not None:
            return existing.to_entity()

        # Read is the only thing a guest can ask for. Anything beyond it is a
        # conversation with the owner, not a button.
        requested = [read_action_for_resource(resource_type)]
        row = ResourceAccessRequest(
            pod_id=pod_id,
            resource_type=resource_type.value,
            resource_id=resource_id,
            resource_name=resource_name,
            requester_user_id=requester_user_id,
            requested_permission_ids=requested,
            status=ResourceAccessRequestStatus.PENDING,
            message=(message or "").strip()[:500] or None,
            requested_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        await self.session.flush()
        return row.to_entity()

    async def get_my_request(
        self,
        *,
        pod_id: UUID,
        resource_type: ResourceType,
        resource_id: UUID,
        requester_user_id: UUID,
    ) -> ResourceAccessRequestEntity | None:
        row = await self._get_pending(
            pod_id=pod_id,
            resource_type=resource_type,
            resource_id=resource_id,
            requester_user_id=requester_user_id,
        )
        return row.to_entity() if row is not None else None

    async def list_requests(
        self,
        *,
        pod_id: UUID,
        status: ResourceAccessRequestStatus | None = ResourceAccessRequestStatus.PENDING,
    ) -> list[ResourceAccessRequestEntity]:
        stmt = select(ResourceAccessRequest).where(
            ResourceAccessRequest.pod_id == pod_id
        )
        if status is not None:
            stmt = stmt.where(ResourceAccessRequest.status == status)
        stmt = stmt.order_by(ResourceAccessRequest.requested_at.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def approve(
        self,
        *,
        request_id: UUID,
        pod_id: UUID,
        decided_by_user_id: UUID,
    ) -> ResourceAccessRequestEntity:
        row = await self._get_pending_by_id(request_id=request_id, pod_id=pod_id)

        # A grant to the *user*, not to a pod member row — the requester has no
        # membership and is not being given one.
        await replace_resource_grantee_grant(
            self.session,
            pod_id=pod_id,
            resource_type=ResourceType(row.resource_type),
            resource_id=row.resource_id,
            grantee_type="USER",
            grantee_id=row.requester_user_id,
            permission_ids=list(row.requested_permission_ids or []),
            created_by_user_id=decided_by_user_id,
        )

        entity = row.to_entity()
        entity.mark_approved(decided_by_user_id=decided_by_user_id)
        row.status = entity.status
        row.decided_at = entity.decided_at
        row.decided_by_user_id = entity.decided_by_user_id
        await self.session.flush()

        # The requester's cached snapshot predates the grant; without this they
        # keep seeing the denial until the TTL elapses.
        await invalidate_role_snapshot_cache(user_id=row.requester_user_id)
        return entity

    async def reject(
        self,
        *,
        request_id: UUID,
        pod_id: UUID,
        decided_by_user_id: UUID,
    ) -> ResourceAccessRequestEntity:
        row = await self._get_pending_by_id(request_id=request_id, pod_id=pod_id)
        entity = row.to_entity()
        entity.mark_rejected(decided_by_user_id=decided_by_user_id)
        row.status = entity.status
        row.decided_at = entity.decided_at
        row.decided_by_user_id = entity.decided_by_user_id
        await self.session.flush()
        return entity

    async def _get_pending_by_id(
        self, *, request_id: UUID, pod_id: UUID
    ) -> ResourceAccessRequest:
        stmt = select(ResourceAccessRequest).where(
            ResourceAccessRequest.id == request_id,
            ResourceAccessRequest.pod_id == pod_id,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise PodAccessDeniedError("Access request not found")
        if row.status != ResourceAccessRequestStatus.PENDING:
            raise PodConflictError("This request has already been decided")
        return row

    async def _get_pending(
        self,
        *,
        pod_id: UUID,
        resource_type: ResourceType,
        resource_id: UUID,
        requester_user_id: UUID,
    ) -> ResourceAccessRequest | None:
        stmt = select(ResourceAccessRequest).where(
            ResourceAccessRequest.pod_id == pod_id,
            ResourceAccessRequest.resource_type == resource_type.value,
            ResourceAccessRequest.resource_id == resource_id,
            ResourceAccessRequest.requester_user_id == requester_user_id,
            ResourceAccessRequest.status == ResourceAccessRequestStatus.PENDING,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
