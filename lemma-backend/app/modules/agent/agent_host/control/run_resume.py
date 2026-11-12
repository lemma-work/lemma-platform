"""Where a reclaimed Agent Host run picks up, and what it picks up with.

A worker stopped mid-run (a deploy, a SIGTERM) leaves the run RUNNING, and the
next worker reclaims it and drives the same dispatch. It must neither dispatch
again nor re-read what the last worker already turned into messages. The lease
row -- one per run, already the dispatch fence -- carries the two things that
takes: the stream entry consumption got to, and the expiry of the credential
the host holds. Both halves of each live here so the writer and the reader
cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.agent_host.domain.host import AgentHostCommandKind
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostHarnessModel,
    AgentHostRunLeaseModel,
)

#: Read from the start: no entry has been consumed yet.
STREAM_START = "0-0"


@dataclass(frozen=True, slots=True)
class ResumePoint:
    """Everything a reclaimed run's consume loop needs that dispatch decided."""

    harness_key: str
    harness_capabilities: JsonObject
    stream_cursor: str
    credential_expires_at: datetime | None
    run_deadline: datetime | None


async def resume_point(
    uow: SqlAlchemyUnitOfWork, *, run_id: UUID
) -> ResumePoint | None:
    """The run's existing dispatch, or ``None`` when it has never had one."""
    lease = await uow.session.get(AgentHostRunLeaseModel, run_id)
    if lease is None:
        return None
    harness = await uow.session.get(AgentHostHarnessModel, lease.harness_id)
    start = (
        await uow.session.execute(
            select(AgentHostCommandModel.payload)
            .where(
                AgentHostCommandModel.run_id == run_id,
                AgentHostCommandModel.kind == AgentHostCommandKind.START_RUN.value,
                AgentHostCommandModel.lease_epoch == lease.lease_epoch,
            )
            .order_by(AgentHostCommandModel.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return ResumePoint(
        harness_key=harness.harness_key if harness is not None else "unknown",
        harness_capabilities=dict(harness.capabilities or {}) if harness else {},
        stream_cursor=lease.consumed_stream_id or STREAM_START,
        credential_expires_at=lease.credential_expires_at,
        run_deadline=_deadline(start),
    )


def _deadline(payload: object) -> datetime | None:
    raw = payload.get("run_deadline") if isinstance(payload, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


async def record_consumed(
    uow: SqlAlchemyUnitOfWork, *, run_id: UUID, stream_id: str
) -> None:
    """Remember the last entry whose messages are all persisted."""
    await uow.session.execute(
        update(AgentHostRunLeaseModel)
        .where(AgentHostRunLeaseModel.run_id == run_id)
        .values(consumed_stream_id=stream_id)
    )


async def record_credential_expiry(
    uow: SqlAlchemyUnitOfWork, *, run_id: UUID, expires_at: datetime | None
) -> None:
    """Remember when the credential just handed to the host expires."""
    if expires_at is None:
        return
    await uow.session.execute(
        update(AgentHostRunLeaseModel)
        .where(AgentHostRunLeaseModel.run_id == run_id)
        .values(credential_expires_at=expires_at)
    )


__all__ = [
    "STREAM_START",
    "ResumePoint",
    "record_consumed",
    "record_credential_expiry",
    "resume_point",
]
