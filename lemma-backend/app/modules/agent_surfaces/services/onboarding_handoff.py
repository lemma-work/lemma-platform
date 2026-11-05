"""Handing a ready onboarding's held message to the run pipeline, as work.

The transaction that marks an onboarding READY writes the ``surface.ingest``
item that replays its held message, so "ready" and "will be answered" commit
together. It replaced ``SurfaceOnboardingReadyEvent`` and its
``agent-surfaces.onboarding`` consumer group.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import SYSTEM_NAMESPACE, WorkWriter
from app.modules.agent_surfaces.domain.work_payloads import IngestPayload
from app.modules.agent_surfaces.events.work_kinds import SURFACE_INGEST


def onboarding_ingest_key(pending_id: UUID) -> str:
    return f"onboarding:{pending_id}"


async def hand_off_when_ready(uow: SqlAlchemyUnitOfWork, pending_id: UUID) -> None:
    await WorkWriter.of(uow).add(
        SURFACE_INGEST,
        SYSTEM_NAMESPACE,
        IngestPayload(onboarding_pending_id=pending_id),
        dedup_key=onboarding_ingest_key(pending_id),
        concurrency_key=f"thread:onboarding:{pending_id}",
    )


__all__ = ["hand_off_when_ready", "onboarding_ingest_key"]
