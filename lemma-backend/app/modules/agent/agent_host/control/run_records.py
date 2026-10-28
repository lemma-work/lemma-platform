"""What an Agent Host run decided once, kept on its record.

docs/architecture/desktop-host-execution.md §2: where a run's commands execute
is chosen once and a run never moves. The context that carries it is rebuilt
whenever the run is -- a worker reclaiming it, an approved tool executing
after a pause -- so the choice has to live where those can read it back:
``agent_host_run_records``, one row per run, beside whether its agent's own
shell replaces Lemma's command tools and the final answer its tool recorded.
Each field is written alone (an upsert of that column), so one writer never
disturbs another's.

The same record is what a host sandbox's operations are routed by: a run's
own operations go to the host it recorded, and an operation no run is making
goes to the host the conversation's most recent host run chose
(``latest_host_execution``), so there is no table saying so separately.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.agent_host.control.models import AgentHostRunRecordModel
from app.modules.agent.domain.value_objects import RunTrigger
from app.modules.agent.infrastructure.models.conversation import AgentRunModel

#: A field of the record. ``execution`` is ``{"target": "vm"}`` or ``{"target":
#: "host", "host_id": ..., "sandbox_id": ..., "root": ...}``;
#: ``host_native_commands`` is ``{"enabled": bool, "host_id": ...}``.
RunRecordField = Literal["execution", "host_native_commands", "final_answer"]
HOST_TARGET = "host"


async def read_run_record(
    uow: SqlAlchemyUnitOfWork, run_id: UUID, field: RunRecordField
) -> dict[str, object] | None:
    value = await uow.session.scalar(
        select(getattr(AgentHostRunRecordModel, field)).where(
            AgentHostRunRecordModel.run_id == run_id
        )
    )
    return value if isinstance(value, dict) else None


async def record_run_field(
    uow: SqlAlchemyUnitOfWork,
    run_id: UUID,
    field: RunRecordField,
    value: dict[str, object],
) -> None:
    """Write one field of the run's record; last write wins."""
    statement = insert(AgentHostRunRecordModel).values(run_id=run_id, **{field: value})
    await uow.session.execute(
        statement.on_conflict_do_update(
            index_elements=["run_id"],
            set_={
                field: statement.excluded[field],
                "updated_at": statement.excluded.updated_at,
            },
        )
    )


async def read_run_execution(
    uow: SqlAlchemyUnitOfWork, run_id: UUID
) -> dict[str, object] | None:
    return await read_run_record(uow, run_id, "execution")


async def record_run_execution(
    uow: SqlAlchemyUnitOfWork, run_id: UUID, value: dict[str, object]
) -> None:
    await record_run_field(uow, run_id, "execution", value)


async def latest_host_execution(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> dict[str, object] | None:
    """The record of the conversation's most recent run that chose the host.

    Walks ``ix_agent_run_conversation_created`` newest first; a run that chose
    the VM, or has not chosen yet, is passed over.
    """
    execution = AgentHostRunRecordModel.execution
    value = (
        await uow.session.execute(
            select(execution)
            .join(AgentRunModel, AgentRunModel.id == AgentHostRunRecordModel.run_id)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                execution["target"].astext == HOST_TARGET,
            )
            .order_by(AgentRunModel.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return value if isinstance(value, dict) else None


#: How far back ``earlier_run_triggers`` looks. A chain of wait wakes longer
#: than this is treated as having no person at its start.
EARLIER_RUNS_LIMIT = 50


async def earlier_run_triggers(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, run_id: UUID
) -> list[RunTrigger]:
    """What started each run before ``run_id``, newest first.

    What a continuation (a wait waking) continues is the work of the runs
    before it; host execution asks who started that work.
    """
    this_run_created = await uow.session.scalar(
        select(AgentRunModel.created_at).where(AgentRunModel.id == run_id)
    )
    if this_run_created is None:
        return []
    rows = (
        await uow.session.execute(
            select(AgentRunModel.trigger)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.id != run_id,
                AgentRunModel.created_at <= this_run_created,
            )
            .order_by(AgentRunModel.created_at.desc())
            .limit(EARLIER_RUNS_LIMIT)
        )
    ).scalars()
    return list(rows)
