"""Grants held against an email until an account exists for it."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.cache import invalidate_role_snapshot_cache
from app.core.authorization.context import ResourceType
from app.core.authorization.grants import replace_resource_grantee_grant
from app.modules.identity.contracts import normalize_identity_email
from app.modules.pod.domain.pod_entities import (
    ResourceAccessInviteEntity,
    ResourceAccessInviteStatus,
)
from app.modules.pod.infrastructure.models.pod_models import ResourceAccessInvite


class ResourceAccessInviteService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def invite(
        self,
        *,
        pod_id: UUID,
        resource_type: ResourceType,
        resource_id: UUID,
        resource_name: str | None,
        email: str,
        permission_ids: list[str],
        invited_by_user_id: UUID,
    ) -> ResourceAccessInviteEntity:
        """Record (or update) the access owed to an address.

        Normalized on the way in so redemption can match on equality — the
        signup event carries whatever the person typed, and two spellings of one
        address must not become two different invitations.
        """
        normalized = normalize_identity_email(email)
        existing = await self._get_pending(
            pod_id=pod_id,
            resource_type=resource_type,
            resource_id=resource_id,
            email=normalized,
        )
        if existing is not None:
            # Re-inviting is how someone changes the access level they meant.
            existing.permission_ids = list(permission_ids)
            existing.invited_by_user_id = invited_by_user_id
            await self.session.flush()
            return existing.to_entity()

        row = ResourceAccessInvite(
            pod_id=pod_id,
            resource_type=resource_type.value,
            resource_id=resource_id,
            resource_name=resource_name,
            email=normalized,
            permission_ids=list(permission_ids),
            status=ResourceAccessInviteStatus.PENDING,
            invited_by_user_id=invited_by_user_id,
            invited_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        await self.session.flush()
        return row.to_entity()

    async def revoke(self, *, invite_id: UUID, pod_id: UUID) -> None:
        stmt = select(ResourceAccessInvite).where(
            ResourceAccessInvite.id == invite_id,
            ResourceAccessInvite.pod_id == pod_id,
            ResourceAccessInvite.status == ResourceAccessInviteStatus.PENDING,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return
        row.status = ResourceAccessInviteStatus.REVOKED
        await self.session.flush()

    async def list_for_resource(
        self,
        *,
        pod_id: UUID,
        resource_type: ResourceType,
        resource_id: UUID,
    ) -> list[ResourceAccessInviteEntity]:
        stmt = select(ResourceAccessInvite).where(
            ResourceAccessInvite.pod_id == pod_id,
            ResourceAccessInvite.resource_type == resource_type.value,
            ResourceAccessInvite.resource_id == resource_id,
            ResourceAccessInvite.status == ResourceAccessInviteStatus.PENDING,
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def redeem_for_user(self, *, user_id: UUID, email: str) -> int:
        """Turn every invite owed to this address into a real grant.

        Called when an account appears for the address. Returns how many were
        redeemed, so the caller can skip the cache invalidation when there is
        nothing to invalidate.
        """
        normalized = normalize_identity_email(email)
        stmt = select(ResourceAccessInvite).where(
            ResourceAccessInvite.email == normalized,
            ResourceAccessInvite.status == ResourceAccessInviteStatus.PENDING,
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return 0

        for row in rows:
            await replace_resource_grantee_grant(
                self.session,
                pod_id=row.pod_id,
                resource_type=ResourceType(row.resource_type),
                resource_id=row.resource_id,
                grantee_type="USER",
                grantee_id=user_id,
                permission_ids=list(row.permission_ids or []),
                created_by_user_id=row.invited_by_user_id,
            )
            entity = row.to_entity()
            entity.mark_redeemed(user_id=user_id)
            row.status = entity.status
            row.redeemed_at = entity.redeemed_at
            row.redeemed_by_user_id = entity.redeemed_by_user_id

        await self.session.flush()
        await invalidate_role_snapshot_cache(user_id=user_id)
        return len(rows)

    async def _get_pending(
        self,
        *,
        pod_id: UUID,
        resource_type: ResourceType,
        resource_id: UUID,
        email: str,
    ) -> ResourceAccessInvite | None:
        stmt = select(ResourceAccessInvite).where(
            ResourceAccessInvite.pod_id == pod_id,
            ResourceAccessInvite.resource_type == resource_type.value,
            ResourceAccessInvite.resource_id == resource_id,
            ResourceAccessInvite.email == email,
            ResourceAccessInvite.status == ResourceAccessInviteStatus.PENDING,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
