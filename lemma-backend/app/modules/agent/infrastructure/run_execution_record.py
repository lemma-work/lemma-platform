"""Where a run's commands execute, written on the run itself.

docs/architecture/desktop-host-execution.md §2: the choice is made once and a
run never moves. The context that carries it is rebuilt whenever the run is --
a worker reclaiming it after a crash, an approved tool being executed after a
pause -- so the choice has to live somewhere those can read it back. It lives
under one key of the run's own metadata, written with ``jsonb_set`` so no
other key is disturbed.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, array

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import AgentRunModel

#: The run-metadata key. Its value is ``{"target": "vm"}`` or ``{"target":
#: "host", "sandbox_id": ..., "root": ...}``.
EXECUTION_METADATA_KEY = "execution"


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
