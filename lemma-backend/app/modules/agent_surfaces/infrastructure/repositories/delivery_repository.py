"""``surface_deliveries`` rows: written with their content, claimed before a send.

The claim is a conditional UPDATE (``PENDING -> SENDING``) so two senders -- the
run's own observer and the destination pump -- can race for one row and only
one of them sends. Everything else is a plain write on the caller's session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.deliveries import DeliveryStatus
from app.modules.agent_surfaces.infrastructure.inbound_delivery_models import (
    SurfaceDeliveryModel,
)

_OPEN = (DeliveryStatus.PENDING.value, DeliveryStatus.SENDING.value)


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert(
        self,
        *,
        idempotency_key: str,
        destination_key: str,
        target: dict[str, Any],
        content: dict[str, Any],
        pod_id: UUID | None = None,
        conversation_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        notification_id: UUID | None = None,
        inbound_message_id: UUID | None = None,
        surface_id: UUID | None = None,
        status: DeliveryStatus = DeliveryStatus.PENDING,
        not_before: datetime | None = None,
        sending_until: datetime | None = None,
    ) -> UUID | None:
        """The new row's id, or ``None`` when the key already has one."""
        row_id = uuid7()
        values: dict[str, Any] = {
            "id": row_id,
            "idempotency_key": idempotency_key,
            "destination_key": destination_key,
            "target": target,
            "content": content,
            "pod_id": pod_id,
            "conversation_id": conversation_id,
            "agent_run_id": agent_run_id,
            "notification_id": notification_id,
            "inbound_message_id": inbound_message_id,
            "surface_id": surface_id,
            "status": status.value,
            "sending_until": sending_until,
            "attempts": 1 if status is DeliveryStatus.SENDING else 0,
        }
        if not_before is not None:
            values["not_before"] = not_before
        return await self.session.scalar(
            insert(SurfaceDeliveryModel)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_surface_deliveries_idempotency_key")
            .returning(SurfaceDeliveryModel.id)
        )

    async def get(self, row_id: UUID) -> SurfaceDeliveryModel | None:
        return await self.session.get(SurfaceDeliveryModel, row_id)

    async def by_key(self, idempotency_key: str) -> SurfaceDeliveryModel | None:
        return await self.session.scalar(
            select(SurfaceDeliveryModel).where(
                SurfaceDeliveryModel.idempotency_key == idempotency_key
            )
        )

    async def oldest_open(self, destination_key: str) -> SurfaceDeliveryModel | None:
        """The delivery this destination owes next, PENDING or still SENDING."""
        return await self.session.scalar(
            select(SurfaceDeliveryModel)
            .where(
                SurfaceDeliveryModel.destination_key == destination_key,
                SurfaceDeliveryModel.status.in_(_OPEN),
            )
            .order_by(SurfaceDeliveryModel.created_at, SurfaceDeliveryModel.id)
            .limit(1)
        )

    async def claim(
        self, row_id: UUID, *, lease: timedelta, content: dict[str, Any] | None = None
    ) -> bool:
        """``PENDING -> SENDING`` for this sender, or ``False`` if someone has it."""
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "status": DeliveryStatus.SENDING.value,
            "sending_until": now + lease,
            "attempts": SurfaceDeliveryModel.attempts + 1,
            "updated_at": now,
        }
        if content is not None:
            values["content"] = content
        claimed = await self.session.scalar(
            update(SurfaceDeliveryModel)
            .where(
                SurfaceDeliveryModel.id == row_id,
                SurfaceDeliveryModel.status == DeliveryStatus.PENDING.value,
            )
            .values(**values)
            .returning(SurfaceDeliveryModel.id)
        )
        return claimed is not None

    async def set_delivery_status(
        self,
        row_id: UUID,
        status: DeliveryStatus,
        *,
        error: str | None = None,
        provider_message_id: str | None = None,
        only_if: DeliveryStatus | None = DeliveryStatus.SENDING,
    ) -> bool:
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "status": status.value,
            "sending_until": None,
            "updated_at": now,
        }
        if error is not None:
            values["last_error"] = error[:1000]
        if provider_message_id is not None:
            values["provider_message_id"] = provider_message_id
        if status is DeliveryStatus.SENT:
            values["sent_at"] = now
        statement = update(SurfaceDeliveryModel).where(
            SurfaceDeliveryModel.id == row_id
        )
        if only_if is not None:
            statement = statement.where(SurfaceDeliveryModel.status == only_if.value)
        result = await self.session.scalar(
            statement.values(**values).returning(SurfaceDeliveryModel.id)
        )
        return result is not None

    async def retry_later(
        self, row_id: UUID, *, not_before: datetime, error: str | None
    ) -> bool:
        """``SENDING -> PENDING``: refused for now, not before ``not_before``."""
        values: dict[str, Any] = {
            "status": DeliveryStatus.PENDING.value,
            "sending_until": None,
            "not_before": not_before,
            "updated_at": datetime.now(UTC),
        }
        if error is not None:
            values["last_error"] = error[:1000]
        result = await self.session.scalar(
            update(SurfaceDeliveryModel)
            .where(
                SurfaceDeliveryModel.id == row_id,
                SurfaceDeliveryModel.status == DeliveryStatus.SENDING.value,
            )
            .values(**values)
            .returning(SurfaceDeliveryModel.id)
        )
        return result is not None

    async def update_content(self, row_id: UUID, content: dict[str, Any]) -> None:
        await self.session.execute(
            update(SurfaceDeliveryModel)
            .where(SurfaceDeliveryModel.id == row_id)
            .values(content=content, updated_at=datetime.now(UTC))
        )

    async def counts_by_status(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(SurfaceDeliveryModel.status, func.count()).group_by(
                SurfaceDeliveryModel.status
            )
        )
        return dict(rows.tuples().all())

    async def purge_finished(self, *, before: datetime, limit: int) -> int:
        doomed = (
            select(SurfaceDeliveryModel.id)
            .where(
                or_(
                    and_(
                        SurfaceDeliveryModel.status.notin_(_OPEN),
                        SurfaceDeliveryModel.updated_at < before,
                    ),
                )
            )
            .limit(limit)
            .scalar_subquery()
        )
        result = await self.session.execute(
            delete(SurfaceDeliveryModel)
            .where(SurfaceDeliveryModel.id.in_(doomed))
            .returning(SurfaceDeliveryModel.id)
        )
        return len(result.scalars().all())


async def queued_pump_run_at(session: AsyncSession, dedup_key: str) -> datetime | None:
    """When the queued pump item for a destination is due, if one is queued."""
    from app.core.infrastructure.work.models import WorkItem

    return await session.scalar(
        select(WorkItem.run_at).where(
            WorkItem.dedup_key == dedup_key, WorkItem.state == "queued"
        )
    )


__all__ = ["DeliveryRepository", "queued_pump_run_at"]
