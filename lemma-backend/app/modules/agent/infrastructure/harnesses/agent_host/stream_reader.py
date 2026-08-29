"""Reading one dispatched run's event stream, and failing when it cannot be.

Split out of the harness because the read has state the consume loop should not
be carrying: where the run got to in the stream, and how many times in a row
Redis has refused to answer. Both only matter here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from redis.exceptions import RedisError

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.agent_host.event_stream import (
    AgentHostEventStream,
    StreamBatch,
)

logger = get_logger(__name__)

# Redis carries every event of this run. A few failed reads are a blip worth
# retrying; a steady stream of them means we are producing no output at all,
# and the run must say so rather than sit silent until its deadline.
MAX_CONSECUTIVE_STREAM_FAILURES = 5
STREAM_FAILURE_BACKOFF_SECONDS = 2.0

STREAM_UNAVAILABLE_MESSAGE = (
    "Lemma could not read this run's event stream; the run was stopped rather "
    "than left running with no output"
)


class StreamUnavailable(RuntimeError):
    """The run's event stream failed to read too many times in a row."""


@dataclass(slots=True)
class StreamReader:
    """Owns the run's stream cursor and its tolerance for read failures."""

    stream: AgentHostEventStream
    run_id: UUID
    block_ms: int
    cursor: str = "0-0"
    failures: int = 0

    async def next_batch(self) -> StreamBatch:
        """Read the next batch, or an empty one while a blip is ridden out.

        Raises :class:`StreamUnavailable` once the failures stop looking like a
        blip. Returning empty batches forever instead would leave the run
        producing no output for its whole deadline and then reporting only that
        the host never terminalized - blaming the host for our own outage.
        """
        try:
            batch = await self.stream.read(
                run_id=self.run_id,
                after_id=self.cursor,
                block_ms=self.block_ms,
            )
        except RedisError as exc:
            self.failures += 1
            logger.warning(
                "agent.harnesses.agent_host.event_stream_read.degraded",
                agent_run_id=str(self.run_id),
                attempt=self.failures,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            if self.failures >= MAX_CONSECUTIVE_STREAM_FAILURES:
                raise StreamUnavailable(STREAM_UNAVAILABLE_MESSAGE) from exc
            await asyncio.sleep(STREAM_FAILURE_BACKOFF_SECONDS)
            return StreamBatch([], cursor=self.cursor)
        self.failures = 0
        # The batch carries the cursor rather than its last event, so an entry
        # that had to be dropped still moves us past it.
        self.cursor = batch.cursor
        return batch
