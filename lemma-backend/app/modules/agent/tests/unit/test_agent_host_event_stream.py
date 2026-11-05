"""Contract tests for the per-run Agent Host event stream.

The stream's ordering and watermark guarantees live in a Lua script and in
Redis's stream commands, so these run that script for real -- in fakeredis,
which executes it rather than reimplementing it, and keeps the unit lane off
any Redis the machine happens to be running.
"""

from __future__ import annotations

from uuid import uuid7

import pytest
from fakeredis.aioredis import FakeRedis

from app.modules.agent.agent_host.control.event_stream import (
    AgentHostEventStream,
    run_events_stream_key,
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


def _event(
    sequence: int, *, type_: str = "agent_message_chunk", text: str = ""
) -> dict:
    return {
        "sequence": sequence,
        "type": type_,
        "object_id": None,
        "payload": {"text": text},
    }


class TestAppendAndRead:
    async def test_events_read_back_in_append_order(self, stream) -> None:
        run_id = uuid7()
        try:
            await stream.append(
                run_id=run_id,
                events=[_event(i, text=str(i)) for i in range(1, 6)],
            )
            events = await stream.read(run_id=run_id, block_ms=50)
            assert [e.sequence for e in events] == [1, 2, 3, 4, 5]
            assert [e.payload["text"] for e in events] == ["1", "2", "3", "4", "5"]
        finally:
            await stream.delete(run_id=run_id)

    async def test_read_resumes_after_a_cursor(self, stream) -> None:
        """The consumer's whole restart story depends on this."""
        run_id = uuid7()
        try:
            await stream.append(run_id=run_id, events=[_event(i) for i in (1, 2, 3)])
            first = await stream.read(run_id=run_id, block_ms=50)
            cursor = first[1].stream_id  # resume after sequence 2

            rest = await stream.read(run_id=run_id, after_id=cursor, block_ms=50)
            assert [e.sequence for e in rest] == [3]
        finally:
            await stream.delete(run_id=run_id)

    async def test_appending_nothing_is_a_noop(self, stream) -> None:
        run_id = uuid7()
        await stream.append(run_id=run_id, events=[])
        assert await stream.read(run_id=run_id, block_ms=50) == []

    async def test_read_of_unknown_run_returns_empty(self, stream) -> None:
        assert await stream.read(run_id=uuid7(), block_ms=50) == []

    async def test_fields_survive_the_round_trip(self, stream) -> None:
        run_id = uuid7()
        try:
            await stream.append(
                run_id=run_id,
                events=[
                    {
                        "sequence": 7,
                        "type": "tool_call",
                        "object_id": "call-1",
                        "payload": {"name": "exec", "nested": {"a": [1, 2]}},
                    }
                ],
            )
            (event,) = await stream.read(run_id=run_id, block_ms=50)
            assert event.sequence == 7
            assert event.type == "tool_call"
            assert event.object_id == "call-1"
            assert event.payload["nested"] == {"a": [1, 2]}
        finally:
            await stream.delete(run_id=run_id)


class TestWatermark:
    async def test_last_sequence_tracks_the_newest_entry(self, stream) -> None:
        run_id = uuid7()
        try:
            assert await stream.last_sequence(run_id=run_id) == 0
            await stream.append(run_id=run_id, events=[_event(i) for i in (1, 2, 3)])
            assert await stream.last_sequence(run_id=run_id) == 3
            await stream.append(run_id=run_id, events=[_event(4)])
            assert await stream.last_sequence(run_id=run_id) == 4
        finally:
            await stream.delete(run_id=run_id)

    async def test_watermark_of_deleted_stream_is_zero(self, stream) -> None:
        """A Redis flush must read as 'nothing acked', so the host resends."""
        run_id = uuid7()
        await stream.append(run_id=run_id, events=[_event(9)])
        await stream.delete(run_id=run_id)
        assert await stream.last_sequence(run_id=run_id) == 0


class TestLifecycle:
    async def test_delete_removes_the_stream(self, stream) -> None:
        run_id = uuid7()
        await stream.append(run_id=run_id, events=[_event(1)])
        await stream.delete(run_id=run_id)
        assert await stream.read(run_id=run_id, block_ms=50) == []

    async def test_delete_is_idempotent(self, stream) -> None:
        run_id = uuid7()
        await stream.delete(run_id=run_id)
        await stream.delete(run_id=run_id)

        assert await stream.read(run_id=run_id, block_ms=50) == []

    async def test_append_sets_a_ttl_so_an_abandoned_run_cannot_leak(
        self, stream
    ) -> None:
        run_id = uuid7()
        try:
            await stream.append(run_id=run_id, events=[_event(1)])
            client = stream._client()
            ttl = await client.ttl(run_events_stream_key(run_id))
            assert ttl > 0
        finally:
            await stream.delete(run_id=run_id)


class TestMalformedEntries:
    async def test_unparseable_entry_is_skipped_not_fatal(self, stream) -> None:
        """A poison entry must not stall the run's whole event feed."""
        run_id = uuid7()
        try:
            client = stream._client()
            key = run_events_stream_key(run_id)
            await client.xadd(key, {"event": "not json"})
            await stream.append(run_id=run_id, events=[_event(2, text="ok")])

            events = await stream.read(run_id=run_id, block_ms=50)
            assert [e.sequence for e in events] == [2]
        finally:
            await stream.delete(run_id=run_id)
