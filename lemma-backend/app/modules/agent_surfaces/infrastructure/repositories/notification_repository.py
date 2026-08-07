from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.infrastructure.models import NotificationModel

_MUTABLE_FIELDS = (
    "status",
    "delivery_status",
    "delivery_surface_id",
    "delivery_conversation_id",
    "delivery_platform",
    "delivery_error",
    "response_summary",
    "response_data",
    "expires_at",
    "delivered_at",
    "read_at",
    "responded_at",
)


class NotificationRepository:
    def __init__(self, uow: IUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def create(self, entity: NotificationEntity) -> NotificationEntity:
        """Insert, or return the row an equal ``idempotency_key`` already claimed.

        A worker retry re-running a tool call must not put a second copy of the
        same message on someone's phone. There is no outbound dedup store — the
        surface one claims inbound only — so the unique constraint is the guard,
        and losing the race is a success, not an error.
        """
        model = NotificationModel(
            id=entity.id,
            pod_id=entity.pod_id,
            recipient_user_id=entity.recipient_user_id,
            recipient_pod_member_id=entity.recipient_pod_member_id,
            actor_user_id=entity.actor_user_id,
            actor_agent_id=entity.actor_agent_id,
            origin_kind=entity.origin_kind.value,
            origin_id=entity.origin_id,
            origin_conversation_id=entity.origin_conversation_id,
            title=entity.title,
            body=entity.body,
            background_instruction=entity.background_instruction,
            expects_response=entity.expects_response,
            action=entity.action,
            status=entity.status.value,
            delivery_status=entity.delivery_status.value,
            delivery_surface_id=entity.delivery_surface_id,
            delivery_conversation_id=entity.delivery_conversation_id,
            delivery_platform=entity.delivery_platform,
            delivery_error=entity.delivery_error,
            response_summary=entity.response_summary,
            response_data=entity.response_data,
            idempotency_key=entity.idempotency_key,
            expires_at=entity.expires_at,
            delivered_at=entity.delivered_at,
            read_at=entity.read_at,
            responded_at=entity.responded_at,
        )
        # A savepoint, not a plain flush: losing the idempotency race raises, and
        # rolling the whole unit of work back to recover would discard whatever
        # the caller did before calling us.
        try:
            async with self.session.begin_nested():
                self.session.add(model)
                await self.session.flush()
        except IntegrityError:
            if entity.idempotency_key is None:
                raise
            existing = await self.get_by_idempotency_key(
                pod_id=entity.pod_id, idempotency_key=entity.idempotency_key
            )
            if existing is None:
                raise
            return existing
        return model.to_entity()

    async def get(self, notification_id: UUID) -> NotificationEntity | None:
        model = await self.session.get(NotificationModel, notification_id)
        return model.to_entity() if model else None

    async def get_by_idempotency_key(
        self, *, pod_id: UUID, idempotency_key: str
    ) -> NotificationEntity | None:
        result = await self.session.execute(
            select(NotificationModel).where(
                NotificationModel.pod_id == pod_id,
                NotificationModel.idempotency_key == idempotency_key,
            )
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def update(self, entity: NotificationEntity) -> NotificationEntity:
        """Write back the fields a lifecycle transition can touch.

        Content fields (body, background_instruction, action, recipient) are
        deliberately absent: a notification's text is what was delivered, and
        rewriting it after the fact would make the inbox disagree with the
        message on someone's phone.
        """
        model = await self.session.get(NotificationModel, entity.id)
        if model is None:
            raise ValueError(f"Notification {entity.id} not found")
        for field in _MUTABLE_FIELDS:
            value = getattr(entity, field)
            if isinstance(value, (NotificationStatus, NotificationDeliveryStatus)):
                value = value.value
            setattr(model, field, value)
        await self.session.flush()
        return model.to_entity()

    async def list_for_recipient(
        self,
        *,
        pod_id: UUID,
        recipient_user_id: UUID,
        statuses: list[NotificationStatus] | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> tuple[list[NotificationEntity], UUID | None]:
        """Newest first. Cursor is the last id seen — ids are uuid7, so
        ordering by id is ordering by creation time without a second column."""
        stmt = select(NotificationModel).where(
            NotificationModel.pod_id == pod_id,
            NotificationModel.recipient_user_id == recipient_user_id,
        )
        if statuses:
            stmt = stmt.where(
                NotificationModel.status.in_([s.value for s in statuses])
            )
        if cursor is not None:
            stmt = stmt.where(NotificationModel.id < cursor)
        stmt = stmt.order_by(NotificationModel.id.desc()).limit(limit + 1)

        result = await self.session.execute(stmt)
        models = list(result.scalars().all())
        next_cursor = models[limit - 1].id if len(models) > limit else None
        return [m.to_entity() for m in models[:limit]], next_cursor

    async def count_unread(self, *, pod_id: UUID, recipient_user_id: UUID) -> int:
        """Unread, not open: a notification you have read but not answered has
        stopped being new, and a badge that only clears on an answer is a badge
        people learn to ignore."""
        result = await self.session.execute(
            select(func.count())
            .select_from(NotificationModel)
            .where(
                NotificationModel.pod_id == pod_id,
                NotificationModel.recipient_user_id == recipient_user_id,
                NotificationModel.read_at.is_(None),
            )
        )
        return int(result.scalar_one())

    async def mark_all_read(
        self, *, pod_id: UUID, recipient_user_id: UUID
    ) -> int:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(NotificationModel).where(
                NotificationModel.pod_id == pod_id,
                NotificationModel.recipient_user_id == recipient_user_id,
                NotificationModel.read_at.is_(None),
            )
        )
        models = list(result.scalars().all())
        for model in models:
            model.read_at = now
        await self.session.flush()
        return len(models)

    async def list_open_for_conversation(
        self, conversation_id: UUID
    ) -> list[NotificationEntity]:
        """What the recipient's agent must be told about when they reply.

        Ordered oldest first: if two questions are outstanding in one thread,
        the reply most likely answers the one that has been waiting longest.
        """
        result = await self.session.execute(
            select(NotificationModel)
            .where(
                NotificationModel.delivery_conversation_id == conversation_id,
                NotificationModel.status == NotificationStatus.OPEN.value,
            )
            .order_by(NotificationModel.id.asc())
        )
        return [m.to_entity() for m in result.scalars().all()]

    async def list_open_for_origin(
        self, *, origin_kind: NotificationOriginKind, origin_id: UUID
    ) -> list[NotificationEntity]:
        """Everything still open that one run/node produced.

        Used to close them out when the originating work is cancelled — a
        question whose asker has gone away should not sit in someone's inbox
        forever waiting for an answer nobody will read.
        """
        result = await self.session.execute(
            select(NotificationModel).where(
                NotificationModel.origin_kind == origin_kind.value,
                NotificationModel.origin_id == origin_id,
                NotificationModel.status == NotificationStatus.OPEN.value,
            )
        )
        return [m.to_entity() for m in result.scalars().all()]

    async def list_past_due(
        self, *, limit: int = 100, now: datetime | None = None
    ) -> list[NotificationEntity]:
        result = await self.session.execute(
            select(NotificationModel)
            .where(
                NotificationModel.status == NotificationStatus.OPEN.value,
                NotificationModel.expires_at.is_not(None),
                NotificationModel.expires_at <= (now or datetime.now(timezone.utc)),
            )
            .order_by(NotificationModel.expires_at.asc())
            .limit(limit)
        )
        return [m.to_entity() for m in result.scalars().all()]

    async def list_by_ids(
        self, *, pod_id: UUID, notification_ids: list[UUID]
    ) -> list[NotificationEntity]:
        """Powers ``check_messages``. Pod-scoped so a stray id from another pod
        reads as absent rather than leaking that it exists."""
        if not notification_ids:
            return []
        result = await self.session.execute(
            select(NotificationModel).where(
                NotificationModel.pod_id == pod_id,
                NotificationModel.id.in_(notification_ids),
            )
        )
        return [m.to_entity() for m in result.scalars().all()]
