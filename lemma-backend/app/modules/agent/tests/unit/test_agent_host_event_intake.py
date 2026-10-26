"""Losing the Redis stream must not make a run unable to report anything.

The host deletes an event from its own outbox once Lemma acks it, so a flushed
stream cannot be rebuilt from sequence 1 -- the host no longer has those events.
Demanding 1 anyway rejects every batch it has left, forever, and the run emits
nothing at all while reporting that the host never terminalized.

The rule lives in the stream's own atomic append script, so a hand-written fake
would only assert that a reimplementation of it agrees with itself. These run
the script itself, in fakeredis, which executes Lua against its emulation of
the stream commands -- the real rule, without a Redis server.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid7

import pytest
from fakeredis.aioredis import FakeRedis

from app.modules.agent.agent_host.domain.host import (
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.agent_host.control.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.agent_host.control.event_stream import (
    AgentHostEventStream,
)
from app.modules.agent.agent_host.control.event_intake import (
    bounded_event_payload,
)
from app.modules.agent.agent_host.control.repository_common import (
    AgentHostProtocolViolation,
    AgentHostSequenceGap,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def stream():
    # fakeredis runs the stream's own Lua append script in-process (with
    # `lupa`), so the watermark rules are exercised as written without writing
    # into whatever Redis answers on the developer's default port.
    redis = FakeRedis(decode_responses=True)
    yield AgentHostEventStream(client=redis)
    await redis.aclose()


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


class TestTrimmingFollowsTheConsumer:
    """The stream used to be capped by length, and a run left without a
    consumer for a few minutes -- a deploy -- lost its oldest unread events.
    It is trimmed only below what the consumer has persisted now."""

    async def test_a_trim_keeps_the_cursor_and_everything_after_it(
        self, stream
    ) -> None:
        run_id, host_id = uuid7(), uuid7()
        repo = _repo(_Lease(run_id, host_id), stream)
        await repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2, 3, 4]))
        read = await stream.read(run_id=run_id)

        await stream.trim_consumed(run_id=run_id, cursor=read[1].stream_id)

        kept = await stream.read(run_id=run_id)
        assert [event.sequence for event in kept] == [2, 3, 4]

    async def test_a_trim_to_the_newest_entry_is_not_a_lost_stream(
        self, stream
    ) -> None:
        """An empty stream reads to the append script as one Redis lost, and it
        would then accept a gap. The cursor's own entry is never trimmed."""
        run_id, host_id = uuid7(), uuid7()
        repo = _repo(_Lease(run_id, host_id), stream)
        await repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2]))
        read = await stream.read(run_id=run_id)

        await stream.trim_consumed(run_id=run_id, cursor=read[-1].stream_id)

        with pytest.raises(AgentHostSequenceGap):
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [5]))


def test_an_oversized_event_keeps_its_small_fields():
    """Refusing the batch would have the host resend it forever; dropping the
    event would leave a gap the stream then refuses."""
    payload = {"status": "completed", "output": "x" * 2_000, "error": None}

    bounded = bounded_event_payload(payload, limit=500)

    assert bounded["status"] == "completed"
    assert bounded["error"] is None
    assert "omitted" in bounded["output"]
    assert bounded_event_payload({"text": "small"}, limit=500) == {"text": "small"}
