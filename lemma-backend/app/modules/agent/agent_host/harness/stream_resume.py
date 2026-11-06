"""Consuming a run's event stream so that another worker can take over.

A worker driving an Agent Host run can be stopped mid-run -- a deploy, a
SIGTERM -- and the run is then reclaimed by another, which drives the same
dispatch. Everything the first worker read off the stream is either already a
persisted message or was still accumulating in its normalizer, and the second
has to tell the two apart. It used to read from the first entry with a fresh
normalizer, and every message already in the conversation was appended again.

Two halves:

* **The cursor.** The worker remembers the last entry after which its
  normalizer held nothing unpersisted -- between the agent's words, after a
  step was flushed -- and writes it on the run's lease once the messages that
  entry produced are persisted. The next worker resumes reading there: what it
  reads again is exactly the text still accumulating, which it has to read to
  rebuild. Only entries before the cursor are ever trimmed from the stream.
* **The ledger.** Tool calls opened before the cursor are on the record but
  not in a fresh normalizer, so they are restored from the persisted messages:
  open ones get closed when the run ends, closed ones are not answered twice.
  The final answer the tool recorded is adopted the same way.

What this cannot cover is a worker killed between persisting a message and
writing the cursor, two consecutive awaits; the cursor write is shielded from
cancellation, so only a hard kill lands there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import anyio
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.agent_host.domain.host import AgentHostToolSource
from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.agent_host.control.event_stream import (
    AgentHostEventStream,
)
from app.modules.agent.agent_host.control.final_answer import (
    read_final_answer,
)
from app.modules.agent.agent_host.control.run_resume import record_consumed
from app.modules.agent.agent_host.harness.events import (
    AgentHostEventNormalizer,
)
from app.modules.agent.agent_host.harness.tool_calls import (
    OpenToolCall,
)
from app.modules.agent.infrastructure.models import MessageModel

logger = get_logger(__name__)

#: How long the cursor write may hold off a cancellation. One UPDATE.
_SAVE_GRACE_SECONDS = 5.0


@dataclass(slots=True)
class ConsumedCursor:
    """How far this run's stream is persisted, and the duty to record it."""

    uow_factory: UnitOfWorkFactory
    stream: AgentHostEventStream
    run_id: UUID
    saved: str
    safe: str = field(init=False)

    def __post_init__(self) -> None:
        self.safe = self.saved

    def passed(self, stream_id: str, *, quiescent: bool, persisted: bool) -> bool:
        """Note one entry fully handed on; True when it must be recorded now.

        Recorded at once when the entry put a message on the record, because
        a worker resuming from an older cursor would put it there again;
        otherwise on the consume loop's slower cadence.
        """
        if quiescent:
            self.safe = stream_id
        return persisted and self.safe != self.saved

    async def save(self) -> None:
        """Record the cursor on the lease, then trim what is below it."""
        if self.safe == self.saved:
            return
        cursor = self.safe
        try:
            with anyio.move_on_after(_SAVE_GRACE_SECONDS, shield=True):
                async with self.uow_factory() as uow:
                    await record_consumed(uow, run_id=self.run_id, stream_id=cursor)
                    await uow.commit()
        except SQLAlchemyError:
            # The next save carries it; a worker resuming in between rereads a
            # little more than it had to, and the ledger skips what it can.
            logger.warning(
                "agent.harnesses.agent_host.stream_cursor_save.degraded",
                agent_run_id=str(self.run_id),
                exc_info=True,
            )
            return
        self.saved = cursor
        await self.stream.trim_consumed(run_id=self.run_id, cursor=cursor)


async def restore_normalizer(
    uow_factory: UnitOfWorkFactory,
    normalizer: AgentHostEventNormalizer,
    *,
    agent_run_id: UUID,
) -> None:
    """Give a fresh normalizer what an earlier worker's had built up."""
    async with uow_factory() as uow:
        rows = (
            await uow.session.execute(
                select(
                    MessageModel.kind,
                    MessageModel.tool_call_id,
                    MessageModel.tool_name,
                    MessageModel.tool_args,
                    MessageModel.message_metadata,
                )
                .where(
                    MessageModel.agent_run_id == agent_run_id,
                    MessageModel.kind.in_(
                        [MessageKind.TOOL_CALL.value, MessageKind.TOOL_RETURN.value]
                    ),
                )
                .order_by(MessageModel.sequence)
            )
        ).all()
    returned = {
        row.tool_call_id for row in rows if row.kind == MessageKind.TOOL_RETURN.value
    }
    for row in rows:
        metadata = (
            row.message_metadata if isinstance(row.message_metadata, dict) else {}
        )
        # Only the calls this harness announced. Lemma's own pausing calls are
        # recorded under Lemma's ids and never pass through the ledger.
        if (
            row.kind != MessageKind.TOOL_CALL.value
            or not row.tool_call_id
            or "agent_host_sequence" not in metadata
        ):
            continue
        normalizer.tool_calls.open(
            row.tool_call_id,
            OpenToolCall(
                name=row.tool_name or "unknown_tool",
                input=row.tool_args,
                metadata=dict(metadata),
                source=_source(metadata.get("tool_source")),
            ),
        )
        if row.tool_call_id in returned:
            normalizer.tool_calls.close(row.tool_call_id)
    try:
        record = await read_final_answer(uow_factory, agent_run_id=agent_run_id)
    except SQLAlchemyError:
        logger.warning(
            "agent.agent_host.final_answer_read_failed.degraded", exc_info=True
        )
        return
    normalizer.adopt_final_answer(record)


def _source(value: object) -> AgentHostToolSource | None:
    try:
        return AgentHostToolSource(value) if isinstance(value, str) else None
    except ValueError:
        return None


__all__ = ["ConsumedCursor", "restore_normalizer"]
