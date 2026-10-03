"""The ``agent_surface_outbound_messages`` table: written on send, read by id.

See ``infrastructure/outbound_models.py`` for what the rows are for.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.events.retention import delete_batch
from app.modules.agent_surfaces.domain.outbound_messages import SurfaceOutboundMessage
from app.modules.agent_surfaces.infrastructure.outbound_models import (
    OUTBOUND_PART_SUFFIX,
    OUTBOUND_STATUS_FAILED,
    OUTBOUND_STATUS_SENT,
    AgentSurfaceOutboundMessageModel,
)

#: How long a send stays traceable. Statuses arrive within hours; a quote of a
#: message older than this is answered without its text.
OUTBOUND_RETENTION = timedelta(days=30)
_PRUNE_BATCH = 1000
_PRUNE_BUDGET_SECONDS = 30.0
#: A body is kept for a quote to show, and a quote is context, not a document.
_BODY_LIMIT = 4000


def _entity(row: AgentSurfaceOutboundMessageModel) -> SurfaceOutboundMessage:
    return SurfaceOutboundMessage(
        id=row.id,
        surface_id=row.surface_id,
        conversation_id=row.conversation_id,
        notification_id=row.notification_id,
        platform=row.platform,
        external_message_id=row.external_message_id,
        recipient=row.recipient,
        kind=row.kind,
        body=row.body,
        status=row.status,
        error=row.error,
    )


class SurfaceOutboundMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_sent(
        self,
        *,
        surface_id: UUID,
        platform: str,
        external_message_ids: Sequence[str],
        kind: str,
        conversation_id: UUID | None = None,
        notification_id: UUID | None = None,
        recipient: str | None = None,
        body: str | None = None,
    ) -> None:
        """One row per id; an id already on record keeps the row it has.

        A long reply goes out as several messages. Only the first carries
        ``kind`` and the rest are marked as its parts, so a failure reported for
        each of them is acted on once -- the person gets one email for one
        reply, not one per chunk.
        """
        ids = [
            message_id
            for message_id in dict.fromkeys(external_message_ids)
            if message_id
        ]
        if not ids:
            return
        now = datetime.now(timezone.utc)
        await self.session.execute(
            pg_insert(AgentSurfaceOutboundMessageModel)
            .values(
                [
                    {
                        "id": uuid4(),
                        "created_at": now,
                        "updated_at": now,
                        "surface_id": surface_id,
                        "conversation_id": conversation_id,
                        "notification_id": notification_id,
                        "platform": platform,
                        "external_message_id": message_id[:255],
                        "recipient": (recipient or None) and recipient[:255],
                        "kind": kind if index == 0 else f"{kind}{OUTBOUND_PART_SUFFIX}",
                        "body": (body or None) and body[:_BODY_LIMIT],
                        "status": OUTBOUND_STATUS_SENT,
                    }
                    for index, message_id in enumerate(ids)
                ]
            )
            .on_conflict_do_nothing(index_elements=["platform", "external_message_id"])
        )

    async def get_by_external_id(
        self, *, platform: str, external_message_id: str
    ) -> SurfaceOutboundMessage | None:
        row = await self.session.scalar(
            select(AgentSurfaceOutboundMessageModel)
            .where(
                AgentSurfaceOutboundMessageModel.platform == platform,
                AgentSurfaceOutboundMessageModel.external_message_id
                == external_message_id,
            )
            .limit(1)
        )
        return _entity(row) if row is not None else None

    async def mark_failed(self, message_id: UUID, *, error: str | None) -> bool:
        """``True`` the first time; a status reported twice is acted on once."""
        result = await self.session.execute(
            update(AgentSurfaceOutboundMessageModel)
            .where(
                AgentSurfaceOutboundMessageModel.id == message_id,
                AgentSurfaceOutboundMessageModel.status != OUTBOUND_STATUS_FAILED,
            )
            .values(
                status=OUTBOUND_STATUS_FAILED,
                error=(error or None) and error[:1000],
                updated_at=datetime.now(timezone.utc),
            )
        )
        return bool(getattr(result, "rowcount", 0))


async def prune_outbound_messages(
    session_maker: Callable[[], AsyncSession], *, now: datetime | None = None
) -> int:
    """Delete sends past the retention window, a bounded batch at a time."""
    cutoff = (now or datetime.now(timezone.utc)) - OUTBOUND_RETENTION
    started = time.monotonic()
    removed = 0
    while True:
        async with session_maker() as session, session.begin():
            batch = await delete_batch(
                session,
                AgentSurfaceOutboundMessageModel,
                AgentSurfaceOutboundMessageModel.created_at < cutoff,
                batch_size=_PRUNE_BATCH,
            )
        removed += batch
        if batch < _PRUNE_BATCH:
            return removed
        if time.monotonic() - started >= _PRUNE_BUDGET_SECONDS:
            return removed


__all__ = [
    "OUTBOUND_RETENTION",
    "SurfaceOutboundMessageRepository",
    "prune_outbound_messages",
]
