from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import ExternalSurfaceUserEntity
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceExternalUser,
)


class ExternalSurfaceUserRepository:
    """The cache of who a platform sender is, one row per sender per platform.

    Two methods here depend on
    ``ix_agent_surface_external_user_platform_tenant_external`` being unique
    *and* being declared ``NULLS NOT DISTINCT``, and neither of them says so at
    the call site, so the argument lives here.

    ``get_by_identity`` reads with ``scalar_one_or_none``, and ``_upsert``
    inserts inside a savepoint and retries on ``IntegrityError`` -- a pattern
    that only resolves a concurrent insert if the database refuses the second
    one. The index is unique over ``(platform, tenant_id, external_user_id)``.

    **The NULLs are the whole problem.** Telegram writes ``tenant_id`` NULL --
    see ``platforms/telegram/parser.py``, where WhatsApp passes its ``waba_id``
    and Telegram has nothing to pass -- and Postgres treats NULLs as distinct by
    default. So for every Telegram sender the uniqueness never applied. Two
    inbound messages from one person racing, which is exactly the case the
    savepoint was written for, leave two cache rows. Every later message from
    that person then raises ``MultipleResultsFound``, which is not a
    ``DomainError`` and so arrives as a 500 -- permanently, until somebody
    deletes a row by hand. The two rows can also disagree about
    ``resolved_user_id``, which makes identity resolution answer differently
    depending on which one is read.

    ``NULLS NOT DISTINCT`` rather than backfilling ``tenant_id`` to ``''``: it
    changes no data, and it says the thing that was meant -- one cache row per
    sender per platform, whether or not that platform has a tenant.
    """

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

    async def list_by_resolved_users(
        self,
        *,
        platform: str,
        resolved_user_ids: Sequence[UUID],
    ) -> list[ExternalSurfaceUserEntity]:
        """Reverse lookup: cached external identities for Lemma users.

        Used by ``surface.send`` and notification delivery to reach a pod member
        on a platform without a prior inbound event in hand.

        Plural in both directions, and that is the point. One person can hold
        several identities on one platform — Slack ids are per workspace, Teams
        ids per tenant — and this used to return only the most recently *seen*
        one. That is not the same as the one that can reach them on the surface
        in hand: a pod with two Slack workspaces had one of them silently
        unreachable, reported as "they have never messaged us". The caller holds
        the surface and does the picking; this only supplies the candidates,
        freshest first so that picking any of them is a defensible default.
        """
        if not resolved_user_ids:
            return []
        stmt = (
            select(AgentSurfaceExternalUser)
            .where(
                AgentSurfaceExternalUser.platform == platform,
                AgentSurfaceExternalUser.resolved_user_id.in_(resolved_user_ids),
            )
            .order_by(AgentSurfaceExternalUser.last_seen_at.desc().nullslast())
        )
        result = await self.session.execute(stmt)
        return [instance.to_entity() for instance in result.scalars().all()]

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

    async def clear_resolved_user(self, resolved_user_id) -> int:
        """Clear cached platform identities after a Lemma profile phone change."""
        result = await self.session.execute(
            update(AgentSurfaceExternalUser)
            .where(AgentSurfaceExternalUser.resolved_user_id == resolved_user_id)
            .values(resolved_user_id=None)
        )
        return int(result.rowcount or 0)
