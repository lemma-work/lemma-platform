"""Reads and writes for :class:`MemberReach` — how a pod reaches a person."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    MemberReach,
    ReachKind,
    ReachStatus,
    SurfaceTarget,
)
from app.modules.agent_surfaces.infrastructure.models import MemberReachModel


class MemberReachRepository:
    def __init__(self, uow: IUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def get(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        kind: ReachKind,
        surface_id: UUID | None,
    ) -> MemberReach | None:
        stmt = select(MemberReachModel).where(
            MemberReachModel.pod_id == pod_id,
            MemberReachModel.user_id == user_id,
            MemberReachModel.kind == kind.value,
        )
        # NULL surface_id identifies the APP reach and needs IS NULL, not `= NULL`.
        if surface_id is None:
            stmt = stmt.where(MemberReachModel.surface_id.is_(None))
        else:
            stmt = stmt.where(MemberReachModel.surface_id == surface_id)
        instance = (await self.session.execute(stmt)).scalars().first()
        return instance.to_entity() if instance else None

    async def list_for_user(
        self, *, pod_id: UUID, user_id: UUID
    ) -> list[MemberReach]:
        """Every reach we hold for this person in this pod, freshest chat first.

        Ordered by inbound recency because the channel someone used most
        recently is the one they are most likely to be looking at. The APP reach
        has no inbound activity and sorts last, which is right: it is the
        fallback, not the preference.
        """
        stmt = (
            select(MemberReachModel)
            .where(
                MemberReachModel.pod_id == pod_id,
                MemberReachModel.user_id == user_id,
            )
            .order_by(MemberReachModel.last_inbound_at.desc().nullslast())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def upsert(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        kind: ReachKind,
        surface_id: UUID | None = None,
        external_user_id: str | None = None,
        target: SurfaceTarget | None = None,
        last_inbound_at: datetime | None = None,
        window_expires_at: datetime | None = None,
    ) -> MemberReach:
        """Create or refresh a reach.

        Called on every inbound event, so the check-then-insert races itself the
        same way the external-user cache does (one person messaging from a DM and
        a channel, or two webhook deliveries). The insert is isolated in a
        SAVEPOINT so a unique violation rolls back to it instead of poisoning the
        surrounding transaction, then falls through to update the row the other
        writer created.
        """
        existing = await self.get(
            pod_id=pod_id, user_id=user_id, kind=kind, surface_id=surface_id
        )
        if existing is None:
            model = MemberReachModel(
                pod_id=pod_id,
                user_id=user_id,
                kind=kind.value,
                surface_id=surface_id,
                external_user_id=external_user_id,
                target=target.model_dump(mode="json") if target else None,
                status=ReachStatus.ACTIVE.value,
                last_inbound_at=last_inbound_at,
                window_expires_at=window_expires_at,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(model)
                    await self.session.flush()
                return model.to_entity()
            except IntegrityError:
                existing = await self.get(
                    pod_id=pod_id, user_id=user_id, kind=kind, surface_id=surface_id
                )
                if existing is None:
                    raise

        instance = await self.session.get(MemberReachModel, existing.id)
        if instance is None:
            return existing
        if target is not None:
            instance.target = target.model_dump(mode="json")
        if external_user_id:
            instance.external_user_id = external_user_id
        if last_inbound_at is not None:
            instance.last_inbound_at = last_inbound_at
        if window_expires_at is not None:
            instance.window_expires_at = window_expires_at
        # Hearing from someone revives a reach we had written off. Opt-out is
        # deliberately NOT cleared here — that is the person's decision, not a
        # side effect of them saying something.
        if instance.status != ReachStatus.ACTIVE.value:
            instance.status = ReachStatus.ACTIVE.value
        await self.session.flush()
        return instance.to_entity()

    async def ensure_app_reach(self, *, pod_id: UUID, user_id: UUID) -> MemberReach:
        """The reach that always exists, so delivery always has somewhere to go."""
        return await self.upsert(
            pod_id=pod_id, user_id=user_id, kind=ReachKind.APP, surface_id=None
        )

    async def set_status(
        self, *, reach_id: UUID, status: ReachStatus
    ) -> None:
        await self.session.execute(
            update(MemberReachModel)
            .where(MemberReachModel.id == reach_id)
            .values(status=status.value)
        )

    async def set_opt_out(
        self, *, reach_id: UUID, opted_out: bool
    ) -> None:
        await self.session.execute(
            update(MemberReachModel)
            .where(MemberReachModel.id == reach_id)
            .values(
                opted_out_at=datetime.now(timezone.utc) if opted_out else None
            )
        )

    async def mark_stale_for_user(self, *, user_id: UUID) -> int:
        """Retire cached platform reaches after an identity change.

        Mirrors ``ExternalSurfaceUserRepository.clear_resolved_user``: when the
        identity behind a reach stops being trustworthy the reach must stop being
        used, but it is marked rather than deleted so "we used to reach you here"
        stays answerable. The APP reach is never affected — it does not depend on
        a third-party identity.
        """
        result = await self.session.execute(
            update(MemberReachModel)
            .where(
                MemberReachModel.user_id == user_id,
                MemberReachModel.kind != ReachKind.APP.value,
                MemberReachModel.status == ReachStatus.ACTIVE.value,
            )
            .values(status=ReachStatus.STALE.value)
        )
        return int(result.rowcount or 0)
