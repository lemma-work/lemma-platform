"""Where an Agent Host run's structured final answer is recorded and read back.

An ACP tool call arrives back through the event stream without a tool *name* —
``ToolCall`` carries a ``title`` the agent wrote and a ``toolCallId``, and
nothing that reliably says "this was ``lemma_final_answer``". Recognising it from
the stream is therefore a heuristic that varies by adapter.

So the tool records its own result here, and the run reads it back when it goes
terminal. The stream path stays as the fast path (it produces the answer without
a database round trip when the adapter does echo enough), but this is the
authority: last write wins, and a run that reaches terminal adopts whatever is
stored over whatever was inferred.

Both halves live in one file so the writer and the reader cannot drift apart.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.repositories import ConversationRepository

# Key under ``agent_runs.run_metadata``.
FINAL_ANSWER_METADATA_KEY = "final_answer"


async def store_final_answer(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
    record: JsonObject,
) -> None:
    """Record the run's final answer. Last call wins."""
    async with uow_factory() as uow:
        await ConversationRepository(uow).set_agent_run_metadata_key(
            agent_run_id,
            FINAL_ANSWER_METADATA_KEY,
            record,
        )
        await uow.commit()


async def read_final_answer(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
) -> JsonObject | None:
    """The recorded final answer for a run, if the agent called the tool."""
    async with uow_factory() as uow:
        stored = await ConversationRepository(uow).get_agent_run_metadata_key(
            agent_run_id,
            FINAL_ANSWER_METADATA_KEY,
        )
    return stored if isinstance(stored, dict) else None


__all__ = [
    "FINAL_ANSWER_METADATA_KEY",
    "read_final_answer",
    "store_final_answer",
]
