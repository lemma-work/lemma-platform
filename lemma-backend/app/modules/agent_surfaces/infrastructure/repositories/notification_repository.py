"""Reads and writes for the in-app inbox."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import Notification
from app.modules.agent_surfaces.infrastructure.models import NotificationModel


class NotificationRepository:
    def __init__(self, uow: IUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def create(self, notification: Notification) -> Notification:
        model = NotificationModel(
            id=notification.id,
            pod_id=notification.pod_id,
            user_id=notification.user_id,
            conversation_id=notification.conversation_id,
            agent_id=notification.agent_id,
            title=notification.title,
            body=notification.body,
            origin_type=(
                notification.origin_type.value if notification.origin_type else None
            ),
            origin_id=notification.origin_id,
            read_at=notification.read_at,
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        pod_id: UUID | None = None,
        unread_only: bool = False,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[Notification]:
        stmt = select(NotificationModel).where(NotificationModel.user_id == user_id)
        if pod_id is not None:
            stmt = stmt.where(NotificationModel.pod_id == pod_id)
        if unread_only:
            stmt = stmt.where(NotificationModel.read_at.is_(None))
        if before is not None:
            stmt = stmt.where(NotificationModel.created_at < before)
        stmt = stmt.order_by(NotificationModel.created_at.desc()).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def unread_count(
        self, *, user_id: UUID, pod_id: UUID | None = None
    ) -> int:
        stmt = select(func.count(NotificationModel.id)).where(
            NotificationModel.user_id == user_id,
            NotificationModel.read_at.is_(None),
        )
        if pod_id is not None:
            stmt = stmt.where(NotificationModel.pod_id == pod_id)
        return int(await self.session.scalar(stmt) or 0)

    async def mark_read(
        self, *, notification_id: UUID, user_id: UUID
    ) -> bool:
        """Mark one notification read. Scoped by user so an id alone is not
        enough to touch someone else's inbox."""
        result = await self.session.execute(
            update(NotificationModel)
            .where(
                NotificationModel.id == notification_id,
                NotificationModel.user_id == user_id,
                NotificationModel.read_at.is_(None),
            )
            .values(read_at=datetime.now(timezone.utc))
        )
        return bool(result.rowcount)

    async def mark_all_read(
        self, *, user_id: UUID, pod_id: UUID | None = None
    ) -> int:
        stmt = (
            update(NotificationModel)
            .where(
                NotificationModel.user_id == user_id,
                NotificationModel.read_at.is_(None),
            )
            .values(read_at=datetime.now(timezone.utc))
        )
        if pod_id is not None:
            stmt = stmt.where(NotificationModel.pod_id == pod_id)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)
