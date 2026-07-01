from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import ExternalSurfaceUserEntity
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceExternalUser,
)


class ExternalSurfaceUserRepository:
    def __init__(self, uow: IUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def get_by_identity(
        self,
        *,
        platform: str,
        tenant_id: str | None,
        external_user_id: str,
    ) -> ExternalSurfaceUserEntity | None:
        stmt = select(AgentSurfaceExternalUser).where(
            AgentSurfaceExternalUser.platform == platform,
            AgentSurfaceExternalUser.external_user_id == external_user_id,
        )
        if tenant_id is None:
            stmt = stmt.where(AgentSurfaceExternalUser.tenant_id.is_(None))
        else:
            stmt = stmt.where(AgentSurfaceExternalUser.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        instance = result.scalar_one_or_none()
        return instance.to_entity() if instance else None

    async def get_by_resolved_user(
        self,
        *,
        platform: str,
        resolved_user_id,
    ) -> ExternalSurfaceUserEntity | None:
        """Reverse lookup: the cached external identity for a Lemma user.

        Used by ``surface.send`` to reach a pod member on a platform without a
        prior inbound event in hand. Returns the most recently seen record.
        """
        stmt = (
            select(AgentSurfaceExternalUser)
            .where(
                AgentSurfaceExternalUser.platform == platform,
                AgentSurfaceExternalUser.resolved_user_id == resolved_user_id,
            )
            .order_by(AgentSurfaceExternalUser.last_seen_at.desc().nullslast())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def upsert(
        self,
        *,
        platform: str,
        tenant_id: str | None,
        external_user_id: str,
        email: str | None,
        phone: str | None,
        display_name: str | None,
        raw_profile: dict | None,
        resolved_user_id=None,
    ) -> ExternalSurfaceUserEntity:
        existing = await self.get_by_identity(
            platform=platform,
            tenant_id=tenant_id,
            external_user_id=external_user_id,
        )
        if existing is None:
            model = AgentSurfaceExternalUser(
                platform=platform,
                tenant_id=tenant_id,
                external_user_id=external_user_id,
                email=email.lower() if email else None,
                phone=phone,
                display_name=display_name,
                raw_profile=raw_profile or {},
                resolved_user_id=resolved_user_id,
                last_seen_at=datetime.now(timezone.utc),
            )
            try:
                # SAVEPOINT: a concurrent ingress for the same identity (the same
                # user messaging via DM and a channel, or two webhook deliveries)
                # races this check-then-insert and violates the
                # (platform, tenant, external_user) unique constraint. Isolate the
                # failed insert so it rolls back to the savepoint instead of
                # poisoning the surrounding transaction, then fall through to load
                # and update the row the other writer created.
                async with self.session.begin_nested():
                    self.session.add(model)
                    await self.session.flush()
                return model.to_entity()
            except IntegrityError:
                existing = await self.get_by_identity(
                    platform=platform,
                    tenant_id=tenant_id,
                    external_user_id=external_user_id,
                )
                if existing is None:
                    raise

        instance = await self.session.get(AgentSurfaceExternalUser, existing.id)
        if instance is None:
            return existing
        instance.email = email.lower() if email else instance.email
        instance.phone = phone or instance.phone
        instance.display_name = display_name or instance.display_name
        instance.raw_profile = raw_profile or instance.raw_profile
        instance.last_seen_at = datetime.now(timezone.utc)
        if resolved_user_id is not None:
            instance.resolved_user_id = resolved_user_id
        await self.session.flush()
        return instance.to_entity()

    async def get_by_email(
        self, *, platform: str, email: str
    ) -> ExternalSurfaceUserEntity | None:
        stmt = select(AgentSurfaceExternalUser).where(
            AgentSurfaceExternalUser.platform == platform,
            func.lower(AgentSurfaceExternalUser.email) == email.lower(),
        )
        result = await self.session.execute(stmt)
        instance = result.scalar_one_or_none()
        return instance.to_entity() if instance else None
