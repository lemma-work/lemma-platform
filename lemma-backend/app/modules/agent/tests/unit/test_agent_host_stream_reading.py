"""Two ways the run consumer used to spin or sit silent for its whole deadline.

A malformed entry that never advanced the cursor turned the consume loop into a
hot loop: the next read returned the same poison entry, yielded nothing after
dropping it, and came straight back with no block and no sleep. And a Redis
outage was swallowed into an empty read, so the run produced nothing for two
hours and then blamed the host for not terminalizing.
"""

from __future__ import annotations

import json
from uuid import uuid7

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.modules.agent.infrastructure.agent_host_event_stream import (
    AgentHostEventStream,
    StreamBatch,
    run_events_stream_key,
)
from app.modules.agent.infrastructure.harnesses.agent_host_stream_reader import (
    MAX_CONSECUTIVE_STREAM_FAILURES,
    StreamReader,
    StreamUnavailable,
)


pytestmark = pytest.mark.asyncio


class _FakeRedis:
    """Serves one canned XREAD reply, recording the id it was asked to read after."""

    def __init__(self, entries: list[tuple[str, dict]]) -> None:
        self.entries = entries
        self.requested_after: list[str] = []

    async def xread(self, streams: dict, count: int, block: int):
        (key, after_id), = streams.items()
        self.requested_after.append(after_id)
        pending = [
            (stream_id, fields)
            for stream_id, fields in self.entries
            if stream_id > after_id
        ]
        return [(key, pending)] if pending else []


def _entry(stream_id: str, sequence: int) -> tuple[str, dict]:
    return (
        stream_id,
        {
            "event": json.dumps(
                {
                    "sequence": sequence,
                    "type": "agent_message_chunk",
                    "object_id": None,
                    "payload": {"text": str(sequence)},
                }
            )
        },
    )


class TestCursorAdvancesPastDroppedEntries:
    async def test_a_poison_entry_moves_the_cursor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without this the next read starts before it again, forever."""
        run_id = uuid7()
        redis = _FakeRedis([("1-0", {"event": "not json"})])
        stream = AgentHostEventStream()
        monkeypatch.setattr(stream, "_client", lambda: redis)

        batch = await stream.read(run_id=run_id, block_ms=1)

        assert list(batch) == []
        assert batch.cursor == "1-0"

    async def test_a_batch_ending_in_a_poison_entry_still_advances(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cursor is not the last *event*; it is the last entry consumed."""
        run_id = uuid7()
        redis = _FakeRedis([_entry("1-0", 1), ("2-0", {"event": "not json"})])
        stream = AgentHostEventStream()
        monkeypatch.setattr(stream, "_client", lambda: redis)

        batch = await stream.read(run_id=run_id, block_ms=1)

        assert [event.sequence for event in batch] == [1]
        assert batch.cursor == "2-0"

    async def test_the_reader_resumes_after_the_dropped_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = uuid7()
        redis = _FakeRedis([("1-0", {"event": "not json"}), _entry("2-0", 2)])
        stream = AgentHostEventStream()
        monkeypatch.setattr(stream, "_client", lambda: redis)
        reader = StreamReader(stream=stream, run_id=run_id, block_ms=1)

        first = await reader.next_batch()
        second = await reader.next_batch()

        assert [event.sequence for event in first] == [2]
        assert list(second) == []
        # The second read asked for entries after the whole first batch, so the
        # loop makes progress instead of re-reading the poison entry.
        assert redis.requested_after == ["0-0", "2-0"]

    async def test_the_key_is_the_run_s_own_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = uuid7()
        captured: dict = {}

        class _Recording(_FakeRedis):
            async def xread(self, streams, count, block):
                captured.update(streams)
                return await super().xread(streams, count=count, block=block)

        stream = AgentHostEventStream()
        monkeypatch.setattr(stream, "_client", lambda: _Recording([]))
        await stream.read(run_id=run_id, block_ms=1)

        assert set(captured) == {run_events_stream_key(run_id)}


class _FailingStream:
    def __init__(self, failures: int) -> None:
        self.remaining = failures
        self.calls = 0

    async def read(self, *, run_id, after_id, block_ms):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise RedisConnectionError("Too many connections")
        return StreamBatch([], cursor=after_id)


class TestReadFailuresSurface:
    async def test_a_blip_is_ridden_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.modules.agent.infrastructure.harnesses"
            ".agent_host_stream_reader.STREAM_FAILURE_BACKOFF_SECONDS",
            0,
        )
        stream = _FailingStream(failures=1)
        reader = StreamReader(stream=stream, run_id=uuid7(), block_ms=1)

        assert list(await reader.next_batch()) == []
        assert list(await reader.next_batch()) == []
        assert reader.failures == 0

    async def test_a_sustained_outage_fails_the_run_instead_of_going_quiet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This is the whole point: silence for a whole run deadline is worse
        than an error, because it is reported as the host's fault."""
        monkeypatch.setattr(
            "app.modules.agent.infrastructure.harnesses"
            ".agent_host_stream_reader.STREAM_FAILURE_BACKOFF_SECONDS",
            0,
        )
        stream = _FailingStream(failures=MAX_CONSECUTIVE_STREAM_FAILURES + 5)
        reader = StreamReader(stream=stream, run_id=uuid7(), block_ms=1)

        with pytest.raises(StreamUnavailable):
            for _ in range(MAX_CONSECUTIVE_STREAM_FAILURES):
                await reader.next_batch()

        assert stream.calls == MAX_CONSECUTIVE_STREAM_FAILURES

    async def test_a_recovery_resets_the_tolerance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.modules.agent.infrastructure.harnesses"
            ".agent_host_stream_reader.STREAM_FAILURE_BACKOFF_SECONDS",
            0,
        )
        stream = _FailingStream(failures=MAX_CONSECUTIVE_STREAM_FAILURES - 1)
        reader = StreamReader(stream=stream, run_id=uuid7(), block_ms=1)

        for _ in range(MAX_CONSECUTIVE_STREAM_FAILURES):
            await reader.next_batch()

        assert reader.failures == 0
