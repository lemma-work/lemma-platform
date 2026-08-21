"""Losing the Redis stream must not make a run unable to report anything.

The host deletes an event from its own outbox once Lemma acks it, so a flushed
stream cannot be rebuilt from sequence 1 -- the host no longer has those events.
Demanding 1 anyway rejects every batch it has left, forever, and the run emits
nothing at all while reporting that the host never terminalized.

These run against real Redis. They used to use an in-memory fake, which was
reasonable while the acceptance rule lived in Python: the empty-vs-lost
distinction had to be exercised deterministically. The rule now lives in the
stream's own atomic append script, so a fake would only assert that a
reimplementation of it agrees with itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid7

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.redis.client import close_redis_clients
from app.modules.agent.domain.agent_host import (
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_event_stream import (
    AgentHostEventStream,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    AgentHostProtocolViolation,
)


pytestmark = pytest.mark.asyncio


async def _redis_available() -> bool:
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        await client.aclose()
        return True
    except RedisError, OSError:
        return False


@pytest.fixture
async def stream():
    if not await _redis_available():
        pytest.skip("redis is not reachable")
    yield AgentHostEventStream()
    await close_redis_clients()


@dataclass
class _Lease:
    run_id: UUID
    host_id: UUID
    lease_epoch: int = 1
    state: str = AgentHostRunState.RUNNING.value


class _Session:
    def __init__(self, lease: _Lease) -> None:
        self._lease = lease

    async def get(self, _model, _pk, with_for_update: bool = False):
        assert not with_for_update, "event intake must not lock the lease row"
        return self._lease

    async def flush(self) -> None:  # pragma: no cover - never reached
        raise AssertionError("event intake must not write rows")


class _Uow:
    def __init__(self, session: _Session) -> None:
        self.session = session


def _batch(run_id: UUID, sequences: list[int]) -> AgentHostEventBatch:
    return AgentHostEventBatch(
        events=[
            AgentHostEvent(
                run_id=run_id,
                lease_epoch=1,
                sequence=sequence,
                type=AgentHostEventType.AGENT_MESSAGE_CHUNK,
                payload={"text": f"chunk-{sequence}"},
            )
            for sequence in sequences
        ]
    )


def _repo(lease: _Lease, stream: AgentHostEventStream) -> AgentHostDispatchRepository:
    return AgentHostDispatchRepository(_Uow(_Session(lease)), event_stream=stream)


class TestStreamLoss:
    async def test_a_flushed_stream_adopts_the_host_s_oldest_surviving_event(
        self, stream
    ) -> None:
        """The host acked through 56 and deleted those; 57 is all it has left."""
        run_id, host_id = uuid7(), uuid7()

        ack = await _repo(_Lease(run_id, host_id), stream).append_events(
            host_id=host_id,
            batch=_batch(run_id, [57, 58, 59]),
        )

        assert ack.acked_through == 59
        assert [event.sequence for event in await stream.read(run_id=run_id)] == [
            57,
            58,
            59,
        ]

    async def test_the_run_continues_normally_after_resyncing(self, stream) -> None:
        run_id, host_id = uuid7(), uuid7()
        repo = _repo(_Lease(run_id, host_id), stream)

        await repo.append_events(host_id=host_id, batch=_batch(run_id, [57]))
        ack = await repo.append_events(host_id=host_id, batch=_batch(run_id, [58, 59]))

        assert ack.acked_through == 59


class TestGapDetectionSurvives:
    async def test_a_gap_in_a_live_stream_is_still_refused(self, stream) -> None:
        """Loss in flight is a real error; only an empty stream is forgiven."""
        run_id, host_id = uuid7(), uuid7()
        repo = _repo(_Lease(run_id, host_id), stream)
        await repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2]))

        with pytest.raises(AgentHostProtocolViolation, match="sequence gap"):
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [7, 8]))

    async def test_a_gap_after_a_resync_is_refused_again(self, stream) -> None:
        """Adoption is a one-time recovery, not a standing licence to skip."""
        run_id, host_id = uuid7(), uuid7()
        repo = _repo(_Lease(run_id, host_id), stream)

        await repo.append_events(host_id=host_id, batch=_batch(run_id, [57, 58]))
        with pytest.raises(AgentHostProtocolViolation, match="sequence gap"):
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [60, 61]))

    async def test_a_refused_batch_leaves_the_watermark_alone(self, stream) -> None:
        """Deciding and writing happen in one atomic step, so a rejected batch
        must leave nothing behind for the next one to be measured against."""
        run_id, host_id = uuid7(), uuid7()
        repo = _repo(_Lease(run_id, host_id), stream)
        await repo.append_events(host_id=host_id, batch=_batch(run_id, [1]))

        with pytest.raises(AgentHostProtocolViolation, match="sequence gap"):
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [4, 5]))

        assert await stream.last_sequence(run_id=run_id) == 1
        # And the run can still carry on from where it really was.
        ack = await repo.append_events(host_id=host_id, batch=_batch(run_id, [2]))
        assert ack.acked_through == 2

    async def test_a_first_batch_starting_at_one_is_unaffected(self, stream) -> None:
        run_id, host_id = uuid7(), uuid7()

        ack = await _repo(_Lease(run_id, host_id), stream).append_events(
            host_id=host_id,
            batch=_batch(run_id, [1, 2]),
        )

        assert ack.acked_through == 2


class TestConcurrentBatches:
    async def test_the_same_batch_twice_at_once_is_applied_once(self, stream) -> None:
        """What used to serialize concurrent batches was a row lock on the
        lease, held across both Redis calls. Delivery is at-least-once and
        several API replicas poll the same host, so two copies of one batch can
        genuinely arrive together; the stream itself now has to reject the
        duplicate rather than a lock upstream preventing the overlap.
        """
        run_id, host_id = uuid7(), uuid7()
        repo = _repo(_Lease(run_id, host_id), stream)

        acks = await asyncio.gather(
            repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2, 3])),
            repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2, 3])),
        )

        assert [ack.acked_through for ack in acks] == [3, 3]
        assert [event.sequence for event in await stream.read(run_id=run_id)] == [
            1,
            2,
            3,
        ]
