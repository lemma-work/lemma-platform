"""Losing the Redis stream must not make a run unable to report anything.

The host deletes an event from its own outbox once Lemma acks it, so a flushed
stream cannot be rebuilt from sequence 1 -- the host no longer has those events.
Demanding 1 anyway rejects every batch it has left, forever, and the run emits
nothing at all while reporting that the host never terminalized.

These use a fake stream rather than Redis: what is under test is the acceptance
rule, and the empty-vs-lost distinction has to be exercised deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid7

import pytest

from app.modules.agent.domain.agent_host import (
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    AgentHostProtocolViolation,
)


pytestmark = pytest.mark.asyncio


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
        return self._lease

    async def flush(self) -> None:  # pragma: no cover - never reached
        raise AssertionError("event intake must not write rows")


class _Uow:
    def __init__(self, session: _Session) -> None:
        self.session = session


class _FakeStream:
    """Holds sequences in memory; a flush is just clearing the list."""

    def __init__(self, sequences: list[int] | None = None) -> None:
        self.sequences = list(sequences or [])

    async def last_sequence(self, *, run_id: UUID) -> int:
        return self.sequences[-1] if self.sequences else 0

    async def append(self, *, run_id: UUID, events: list[dict]) -> None:
        self.sequences.extend(event["sequence"] for event in events)


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


def _repo(lease: _Lease, stream: _FakeStream) -> AgentHostDispatchRepository:
    return AgentHostDispatchRepository(_Uow(_Session(lease)), event_stream=stream)


class TestStreamLoss:
    async def test_a_flushed_stream_adopts_the_host_s_oldest_surviving_event(
        self,
    ) -> None:
        """The host acked through 56 and deleted those; 57 is all it has left."""
        run_id, host_id = uuid7(), uuid7()
        stream = _FakeStream()

        ack = await _repo(_Lease(run_id, host_id), stream).append_events(
            host_id=host_id,
            batch=_batch(run_id, [57, 58, 59]),
        )

        assert ack.acked_through == 59
        assert stream.sequences == [57, 58, 59]

    async def test_the_run_continues_normally_after_resyncing(self) -> None:
        run_id, host_id = uuid7(), uuid7()
        stream = _FakeStream()
        repo = _repo(_Lease(run_id, host_id), stream)

        await repo.append_events(host_id=host_id, batch=_batch(run_id, [57]))
        ack = await repo.append_events(host_id=host_id, batch=_batch(run_id, [58, 59]))

        assert ack.acked_through == 59


class TestGapDetectionSurvives:
    async def test_a_gap_in_a_live_stream_is_still_refused(self) -> None:
        """Loss in flight is a real error; only an empty stream is forgiven."""
        run_id, host_id = uuid7(), uuid7()
        stream = _FakeStream([1, 2])

        with pytest.raises(AgentHostProtocolViolation, match="sequence gap"):
            await _repo(_Lease(run_id, host_id), stream).append_events(
                host_id=host_id,
                batch=_batch(run_id, [7, 8]),
            )

    async def test_a_gap_after_a_resync_is_refused_again(self) -> None:
        """Adoption is a one-time recovery, not a standing licence to skip."""
        run_id, host_id = uuid7(), uuid7()
        stream = _FakeStream()
        repo = _repo(_Lease(run_id, host_id), stream)

        await repo.append_events(host_id=host_id, batch=_batch(run_id, [57, 58]))
        with pytest.raises(AgentHostProtocolViolation, match="sequence gap"):
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [60, 61]))

    async def test_a_first_batch_starting_at_one_is_unaffected(self) -> None:
        run_id, host_id = uuid7(), uuid7()
        stream = _FakeStream()

        ack = await _repo(_Lease(run_id, host_id), stream).append_events(
            host_id=host_id,
            batch=_batch(run_id, [1, 2]),
        )

        assert ack.acked_through == 2
