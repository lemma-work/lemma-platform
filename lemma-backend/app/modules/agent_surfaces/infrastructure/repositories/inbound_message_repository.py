"""Inbound message rows, message claims and receiver cursors, over one session.

Every write here runs on the caller's transaction: a webhook's rows commit with
their ``surface.ingest`` items, a receiver's cursor commits with the rows it
read, and an ingest's ROUTED commits with the message it routed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.inbound_messages import InboundStatus
from app.modules.agent_surfaces.infrastructure.inbound_delivery_models import (
    SurfaceInboundMessageModel,
    SurfaceMessageClaimModel,
    SurfaceReceiverStateModel,
)


class InboundMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert(
        self,
        *,
        receiver_scope: str,
        source_event_id: str,
        platform: str,
        source: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        thread_key: str,
        surface_id: UUID | None = None,
        pod_id: UUID | None = None,
        receiver_surface_ids: list[UUID] | None = None,
    ) -> UUID | None:
        """The new row's id, or ``None`` when this delivery is already stored."""
        row_id = uuid7()
        statement = (
            insert(SurfaceInboundMessageModel)
            .values(
                id=row_id,
                receiver_scope=receiver_scope,
                source_event_id=source_event_id,
                platform=platform,
                source=source,
                payload=payload,
                headers=headers,
                thread_key=thread_key,
                surface_id=surface_id,
                pod_id=pod_id,
                receiver_surface_ids=(
                    [str(item) for item in receiver_surface_ids]
                    if receiver_surface_ids is not None
                    else None
                ),
                status=InboundStatus.RECEIVED.value,
            )
            .on_conflict_do_nothing(
                constraint="uq_surface_inbound_messages_receiver_event"
            )
            .returning(SurfaceInboundMessageModel.id)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get(self, row_id: UUID) -> SurfaceInboundMessageModel | None:
        return await self.session.get(SurfaceInboundMessageModel, row_id)

    async def status_of(self, row_id: UUID) -> InboundStatus | None:
        status = await self.session.scalar(
            select(SurfaceInboundMessageModel.status).where(
                SurfaceInboundMessageModel.id == row_id
            )
        )
        return InboundStatus(status) if status else None

    async def mark(
        self,
        row_id: UUID,
        status: InboundStatus,
        *,
        reason: str | None = None,
        conversation_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        pod_id: UUID | None = None,
        only_unfinished: bool = True,
    ) -> bool:
        """Move a row on. A finished row stays as it is: it was already answered."""
        now = datetime.now(UTC)
        values: dict[str, Any] = {"status": status.value, "updated_at": now}
        if reason is not None:
            values["reason"] = reason[:500]
        if conversation_id is not None:
            values["conversation_id"] = conversation_id
        if agent_run_id is not None:
            values["agent_run_id"] = agent_run_id
        if pod_id is not None:
            values["pod_id"] = pod_id
        if status is InboundStatus.ROUTED:
            values["routed_at"] = now
        statement = update(SurfaceInboundMessageModel).where(
            SurfaceInboundMessageModel.id == row_id
        )
        if only_unfinished:
            statement = statement.where(
                SurfaceInboundMessageModel.status.in_(
                    [InboundStatus.RECEIVED.value, InboundStatus.INGESTING.value]
                )
            )
        result = await self.session.execute(
            statement.values(**values).returning(SurfaceInboundMessageModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def purge_finished(
        self, *, finished_before: datetime, failed_before: datetime, limit: int
    ) -> int:
        """Delete answered rows past the redelivery window, failed ones later."""
        doomed = (
            select(SurfaceInboundMessageModel.id)
            .where(
                or_(
                    and_(
                        SurfaceInboundMessageModel.status.in_(
                            [InboundStatus.ROUTED.value, InboundStatus.DROPPED.value]
                        ),
                        SurfaceInboundMessageModel.received_at < finished_before,
                    ),
                    and_(
                        SurfaceInboundMessageModel.status == InboundStatus.FAILED.value,
                        SurfaceInboundMessageModel.received_at < failed_before,
                    ),
                )
            )
            .limit(limit)
            .scalar_subquery()
        )
        result = await self.session.execute(
            delete(SurfaceInboundMessageModel)
            .where(SurfaceInboundMessageModel.id.in_(doomed))
            .returning(SurfaceInboundMessageModel.id)
        )
        return len(result.scalars().all())


class MessageClaimRepository:
    """One claim per external message, held by whoever took it first.

    ``owner`` is the inbound row that took it, so that row's own retry finds
    the claim its own and carries on, where anything else is a duplicate.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self, key: str, *, owner: str | None) -> bool:
        inserted = await self.session.scalar(
            insert(SurfaceMessageClaimModel)
            .values(claim_key=key, owner=owner)
            .on_conflict_do_nothing(index_elements=["claim_key"])
            .returning(SurfaceMessageClaimModel.claim_key)
        )
        if inserted is not None:
            return True
        if owner is None:
            return False
        holder = await self.session.scalar(
            select(SurfaceMessageClaimModel.owner).where(
                SurfaceMessageClaimModel.claim_key == key
            )
        )
        return holder == owner

    async def release(self, key: str, *, owner: str | None) -> None:
        statement = delete(SurfaceMessageClaimModel).where(
            SurfaceMessageClaimModel.claim_key == key
        )
        if owner is not None:
            statement = statement.where(SurfaceMessageClaimModel.owner == owner)
        await self.session.execute(statement)

    async def purge(self, *, before: datetime, limit: int) -> int:
        doomed = (
            select(SurfaceMessageClaimModel.claim_key)
            .where(SurfaceMessageClaimModel.created_at < before)
            .limit(limit)
            .scalar_subquery()
        )
        result = await self.session.execute(
            delete(SurfaceMessageClaimModel)
            .where(SurfaceMessageClaimModel.claim_key.in_(doomed))
            .returning(SurfaceMessageClaimModel.claim_key)
        )
        return len(result.scalars().all())


class ReceiverStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self, receiver_key: str) -> str | None:
        return await self.session.scalar(
            select(SurfaceReceiverStateModel.cursor).where(
                SurfaceReceiverStateModel.receiver_key == receiver_key
            )
        )

    async def exists(self, receiver_key: str) -> bool:
        found = await self.session.scalar(
            select(SurfaceReceiverStateModel.receiver_key).where(
                SurfaceReceiverStateModel.receiver_key == receiver_key
            )
        )
        return found is not None

    async def store(self, receiver_key: str, cursor: str | None) -> None:
        now = datetime.now(UTC)
        await self.session.execute(
            insert(SurfaceReceiverStateModel)
            .values(receiver_key=receiver_key, cursor=cursor, updated_at=now)
            .on_conflict_do_update(
                index_elements=["receiver_key"],
                set_={"cursor": cursor, "updated_at": now},
            )
        )


__all__ = [
    "InboundMessageRepository",
    "MessageClaimRepository",
    "ReceiverStateRepository",
]
