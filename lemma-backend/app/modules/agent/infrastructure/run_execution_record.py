"""Where a run's commands execute, written on the run itself.

docs/architecture/desktop-host-execution.md §2: the choice is made once and a
run never moves. The context that carries it is rebuilt whenever the run is --
a worker reclaiming it after a crash, an approved tool being executed after a
pause -- so the choice has to live somewhere those can read it back. It lives
under one key of the run's own metadata, written with ``jsonb_set`` so no
other key is disturbed.

The same record is what a host sandbox's operations are routed by: the host a
conversation's most recent host run chose is the host its sandbox is on
(``latest_host_execution``), so there is no table saying so separately.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, array

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import AgentRunModel

#: The run-metadata key. Its value is ``{"target": "vm"}`` or ``{"target":
#: "host", "host_id": ..., "sandbox_id": ..., "root": ...}``.
EXECUTION_METADATA_KEY = "execution"
HOST_TARGET = "host"


async def read_run_execution(
    uow: SqlAlchemyUnitOfWork, run_id: UUID
) -> dict[str, object] | None:
    metadata = (
        await uow.session.execute(
            select(AgentRunModel.run_metadata).where(AgentRunModel.id == run_id)
        )
    ).scalar_one_or_none()
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(EXECUTION_METADATA_KEY)
    return value if isinstance(value, dict) else None


async def record_run_execution(
    uow: SqlAlchemyUnitOfWork, run_id: UUID, value: dict[str, object]
) -> None:
    await uow.session.execute(
        update(AgentRunModel)
        .where(AgentRunModel.id == run_id)
        .values(
            run_metadata=func.jsonb_set(
                func.coalesce(AgentRunModel.run_metadata, literal({}, JSONB)),
                array([EXECUTION_METADATA_KEY]),
                literal(value, JSONB),
                True,
            )
        )
    )


async def latest_host_execution(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> dict[str, object] | None:
    """The record of the conversation's most recent run that chose the host.

    Walks ``ix_agent_run_conversation_created`` newest first; a run that chose
    the VM, or has not chosen yet, is passed over.
    """
    execution = AgentRunModel.run_metadata[EXECUTION_METADATA_KEY]
    value = (
        await uow.session.execute(
            select(execution)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                execution["target"].astext == HOST_TARGET,
            )
            .order_by(AgentRunModel.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return value if isinstance(value, dict) else None


#: How far back ``earlier_run_sources`` looks. A chain of wait wakes longer
#: than this is treated as having no person at its start.
EARLIER_RUNS_LIMIT = 50


async def earlier_run_sources(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, run_id: UUID
) -> list[str | None]:
    """The ``source`` of each run started before ``run_id``, newest first.

    What a continuation (a wait waking) continues is the work of the runs
    before it; host execution asks who started that work.
    """
    this_run_created = (
        select(AgentRunModel.created_at)
        .where(AgentRunModel.id == run_id)
        .scalar_subquery()
    )
    rows = (
        await uow.session.execute(
            select(AgentRunModel.run_metadata)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.id != run_id,
                AgentRunModel.created_at <= this_run_created,
            )
            .order_by(AgentRunModel.created_at.desc())
            .limit(EARLIER_RUNS_LIMIT)
        )
    ).scalars()
    sources: list[str | None] = []
    for metadata in rows:
        source = metadata.get("source") if isinstance(metadata, dict) else None
        sources.append(source if isinstance(source, str) else None)
    return sources
