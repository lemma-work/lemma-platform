"""Where an Agent Host run's structured final answer is recorded and read back.

An ACP tool call arrives back through the event stream without a tool *name* —
``ToolCall`` carries a ``title`` the agent wrote and a ``toolCallId``, and
nothing that reliably says "this was ``lemma_final_answer``". Recognising it from
the stream is therefore a heuristic that varies by adapter.

So the tool records its own result here, and the run reads it back when it goes
terminal (``harness/harness_events.adopt_recorded_final_answer``).
The stream path stays as the fast path (it produces the answer without a
database round trip when the adapter does echo enough), but this is the
authority: last write wins, and a run that reaches terminal adopts whatever is
stored over whatever was inferred.

Both halves live in one file so the writer and the reader cannot drift apart —
the same reason ``session_memory`` is shaped this way.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.agent_host.control.run_records import (
    read_run_record,
    record_run_field,
)
from app.modules.agent.domain.value_objects import JsonObject


async def store_final_answer(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
    record: JsonObject,
) -> None:
    """Record the run's final answer. Last call wins."""
    async with uow_factory() as uow:
        await record_run_field(uow, agent_run_id, "final_answer", record)
        await uow.commit()


async def read_final_answer(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
) -> JsonObject | None:
    """The recorded final answer for a run, if the agent called the tool."""
    async with uow_factory() as uow:
        return await read_run_record(uow, agent_run_id, "final_answer")


__all__ = [
    "read_final_answer",
    "store_final_answer",
]
