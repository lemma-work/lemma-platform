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

Both halves live in one file so the writer and the reader cannot drift apart —
the same reason ``agent_host_session_memory`` is shaped this way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.models import AgentRunModel

if TYPE_CHECKING:
    from app.modules.agent.infrastructure.harnesses.agent_host.events import (
        AgentHostEventNormalizer,
    )

logger = get_logger(__name__)

# Key under ``agent_runs.run_metadata``.
FINAL_ANSWER_METADATA_KEY = "final_answer"


async def store_final_answer(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
    record: JsonObject,
) -> None:
    """Record the run's final answer. Last call wins.

    Uses ``jsonb_set`` so a concurrent writer touching another ``run_metadata``
    key is never clobbered, and binds the value as ``JSONB`` directly — casting
    an already-serialized JSON string would store a JSON *string* scalar instead
    of the object.
    """
    async with uow_factory() as uow:
        await uow.session.execute(
            update(AgentRunModel)
            .where(AgentRunModel.id == agent_run_id)
            .values(
                run_metadata=func.jsonb_set(
                    func.coalesce(AgentRunModel.run_metadata, literal({}, JSONB)),
                    array([FINAL_ANSWER_METADATA_KEY]),
                    literal(record, JSONB),
                    True,
                )
            )
        )
        await uow.commit()


async def read_final_answer(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
) -> JsonObject | None:
    """The recorded final answer for a run, if the agent called the tool."""
    async with uow_factory() as uow:
        metadata = (
            await uow.session.execute(
                select(AgentRunModel.run_metadata).where(
                    AgentRunModel.id == agent_run_id
                )
            )
        ).scalar_one_or_none()
    if not isinstance(metadata, dict):
        return None
    stored = metadata.get(FINAL_ANSWER_METADATA_KEY)
    return stored if isinstance(stored, dict) else None


async def adopt_recorded_final_answer(
    uow_factory: UnitOfWorkFactory,
    normalizer: "AgentHostEventNormalizer",
    *,
    agent_run_id: UUID,
) -> None:
    """Let a run prefer the answer the tool recorded over what it inferred.

    Call this as a run goes terminal. Deliberately *not* on the hard-failure
    paths (stream unavailable, deadline): those abandon the run, and reporting a
    COMPLETED structured answer for a run we gave up on would be a lie.
    """
    if not normalizer.structured_expected:
        return
    try:
        record = await read_final_answer(uow_factory, agent_run_id=agent_run_id)
    except SQLAlchemyError:
        # The stream-inferred answer, if any, still stands.
        logger.debug("agent.agent_host.final_answer_read_failed.diagnostic")
        return
    normalizer.adopt_final_answer(record)


__all__ = [
    "FINAL_ANSWER_METADATA_KEY",
    "adopt_recorded_final_answer",
    "read_final_answer",
    "store_final_answer",
]
