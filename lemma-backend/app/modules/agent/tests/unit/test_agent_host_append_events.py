"""append_events writes to the run's stream and touches no database row.

The lease is read under a row lock only to serialize concurrent batches and
validate the epoch. These tests use a stub session so a mutation of the lease
would be visible as a changed attribute rather than silently accepted.
"""

from __future__ import annotations

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
    AgentHostNotFound,
    AgentHostProtocolViolation,
)


pytestmark = pytest.mark.asyncio


async def _redis_available() -> bool:
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        await client.aclose()
        return True
    except (RedisError, OSError):
        return False


@dataclass
class _Lease:
    run_id: UUID
    host_id: UUID
    lease_epoch: int = 1
    state: str = AgentHostRunState.RUNNING.value
    accepted_at: object = None
    lease_expires_at: object = None
    updated_at: object = None


class _Session:
    """Minimal stand-in for the SQLAlchemy session used by append_events."""

    def __init__(self, lease: _Lease | None) -> None:
        self._lease = lease
        self.flushes = 0

    async def get(self, _model, _pk, with_for_update: bool = False):
        return self._lease

    async def flush(self) -> None:
        self.flushes += 1


class _Uow:
    def __init__(self, session: _Session) -> None:
        self.session = session


def _repo(lease: _Lease | None, stream: AgentHostEventStream):
    session = _Session(lease)
    repo = AgentHostDispatchRepository(_Uow(session), event_stream=stream)
    return repo, session


def _batch(run_id: UUID, sequences: list[int], *, epoch: int = 1) -> AgentHostEventBatch:
    return AgentHostEventBatch(
        events=[
            AgentHostEvent(
                run_id=run_id,
                lease_epoch=epoch,
                sequence=sequence,
                type=AgentHostEventType.AGENT_MESSAGE_CHUNK,
                payload={"text": f"chunk-{sequence}"},
            )
            for sequence in sequences
        ]
    )


@pytest.fixture
async def stream():
    if not await _redis_available():
        pytest.skip("redis is not reachable")
    yield AgentHostEventStream()
    await close_redis_clients()


class TestHappyPath:
    async def test_first_batch_acks_through_its_last_sequence(self, stream) -> None:
        run_id, host_id = uuid7(), uuid7()
        repo, session = _repo(_Lease(run_id=run_id, host_id=host_id), stream)
        try:
            ack = await repo.append_events(
                host_id=host_id, batch=_batch(run_id, [1, 2, 3])
            )
            assert ack.acked_through == 3
            assert ack.run_id == run_id
            assert [e.sequence for e in await stream.read(run_id=run_id, block_ms=50)] == [
                1,
                2,
                3,
            ]
        finally:
            await stream.delete(run_id=run_id)

    async def test_appending_costs_no_database_write(self, stream) -> None:
        """The whole point of moving events off PostgreSQL."""
        run_id, host_id = uuid7(), uuid7()
        lease = _Lease(run_id=run_id, host_id=host_id)
        repo, session = _repo(lease, stream)
        try:
            before = (lease.state, lease.lease_expires_at, lease.updated_at)
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2]))
            assert session.flushes == 0
            assert (lease.state, lease.lease_expires_at, lease.updated_at) == before
        finally:
            await stream.delete(run_id=run_id)

    async def test_consecutive_batches_continue_the_sequence(self, stream) -> None:
        run_id, host_id = uuid7(), uuid7()
        repo, _ = _repo(_Lease(run_id=run_id, host_id=host_id), stream)
        try:
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2]))
            ack = await repo.append_events(host_id=host_id, batch=_batch(run_id, [3, 4]))
            assert ack.acked_through == 4
        finally:
            await stream.delete(run_id=run_id)


class TestReplay:
    async def test_a_fully_replayed_batch_is_idempotent(self, stream) -> None:
        """A lost acknowledgement makes the host resend; it must not duplicate."""
        run_id, host_id = uuid7(), uuid7()
        repo, _ = _repo(_Lease(run_id=run_id, host_id=host_id), stream)
        try:
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2, 3]))
            ack = await repo.append_events(
                host_id=host_id, batch=_batch(run_id, [1, 2, 3])
            )
            assert ack.acked_through == 3
            assert len(await stream.read(run_id=run_id, block_ms=50)) == 3
        finally:
            await stream.delete(run_id=run_id)

    async def test_partial_replay_appends_only_the_new_tail(self, stream) -> None:
        run_id, host_id = uuid7(), uuid7()
        repo, _ = _repo(_Lease(run_id=run_id, host_id=host_id), stream)
        try:
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2]))
            ack = await repo.append_events(
                host_id=host_id, batch=_batch(run_id, [1, 2, 3, 4])
            )
            assert ack.acked_through == 4
            assert [
                e.sequence for e in await stream.read(run_id=run_id, block_ms=50)
            ] == [1, 2, 3, 4]
        finally:
            await stream.delete(run_id=run_id)


class TestFencing:
    async def test_a_sequence_gap_is_rejected(self, stream) -> None:
        """A gap means events were lost in flight; accepting it would corrupt
        the transcript silently."""
        run_id, host_id = uuid7(), uuid7()
        repo, _ = _repo(_Lease(run_id=run_id, host_id=host_id), stream)
        try:
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [1]))
            with pytest.raises(AgentHostProtocolViolation, match="sequence gap"):
                await repo.append_events(
                    host_id=host_id, batch=_batch(run_id, [3, 4])
                )
        finally:
            await stream.delete(run_id=run_id)

    async def test_stale_lease_epoch_is_rejected(self, stream) -> None:
        run_id, host_id = uuid7(), uuid7()
        repo, _ = _repo(_Lease(run_id=run_id, host_id=host_id, lease_epoch=2), stream)
        with pytest.raises(AgentHostProtocolViolation, match="epoch"):
            await repo.append_events(
                host_id=host_id, batch=_batch(run_id, [1], epoch=1)
            )

    async def test_events_for_another_host_are_rejected(self, stream) -> None:
        run_id = uuid7()
        repo, _ = _repo(_Lease(run_id=run_id, host_id=uuid7()), stream)
        with pytest.raises(AgentHostNotFound):
            await repo.append_events(host_id=uuid7(), batch=_batch(run_id, [1]))

    async def test_missing_lease_is_rejected(self, stream) -> None:
        repo, _ = _repo(None, stream)
        with pytest.raises(AgentHostNotFound):
            await repo.append_events(host_id=uuid7(), batch=_batch(uuid7(), [1]))

    async def test_terminal_run_cannot_accept_new_events(self, stream) -> None:
        run_id, host_id = uuid7(), uuid7()
        lease = _Lease(
            run_id=run_id,
            host_id=host_id,
            state=AgentHostRunState.SUCCEEDED.value,
        )
        repo, _ = _repo(lease, stream)
        with pytest.raises(AgentHostProtocolViolation, match="terminal"):
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [1]))

    async def test_terminal_run_still_tolerates_a_pure_replay(self, stream) -> None:
        """Replays are dropped before the terminal check, so a late resend of
        already-acked events does not turn into an error."""
        run_id, host_id = uuid7(), uuid7()
        lease = _Lease(run_id=run_id, host_id=host_id)
        repo, _ = _repo(lease, stream)
        try:
            await repo.append_events(host_id=host_id, batch=_batch(run_id, [1, 2]))
            lease.state = AgentHostRunState.SUCCEEDED.value
            ack = await repo.append_events(
                host_id=host_id, batch=_batch(run_id, [1, 2])
            )
            assert ack.acked_through == 2
        finally:
            await stream.delete(run_id=run_id)
