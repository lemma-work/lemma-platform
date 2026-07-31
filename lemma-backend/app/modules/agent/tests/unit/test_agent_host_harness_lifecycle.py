"""What the harness does when it stops driving a run.

Two things it used to get wrong. Giving up on a run left the machine on the
user's desk executing it -- Lemma reported the turn failed and the ACP agent
kept thinking, calling tools, and spending tokens. And the stream was deleted
from a ``finally`` that also runs on cancellation and on a consumer that walks
away, so an abandoned run could delete events the host was still appending, via
an await inside a ``finally`` during GeneratorExit that is not reliably allowed
to finish.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid7

import pytest

from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType
from app.modules.agent.infrastructure.agent_host_event_stream import StreamBatch
from app.modules.agent.infrastructure.harnesses.agent_host import RemoteHarness
from app.modules.agent.infrastructure.harnesses.agent_host_run_window import (
    DispatchedRun,
)


pytestmark = pytest.mark.asyncio


class _RecordingStream:
    """An always-empty stream that remembers whether it was deleted."""

    def __init__(self) -> None:
        self.deleted: list = []

    async def read(self, *, run_id, after_id="0-0", block_ms=1000):
        return StreamBatch([], cursor=after_id)

    async def delete(self, *, run_id) -> None:
        self.deleted.append(run_id)


def _options():
    return SimpleNamespace(
        model_name=None,
        should_stop=None,
        extra={
            "runtime_profile": {
                "harness_id": str(uuid7()),
                "profile_id": str(uuid7()),
                "config": {"harness_snapshot_revision": "1"},
            }
        },
    )


def _harness(stream: _RecordingStream, **kwargs) -> RemoteHarness:
    return RemoteHarness(uow_factory=None, event_stream=stream, **kwargs)


def _stub_dispatch(harness: RemoteHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _enqueue(**_kwargs):
        return DispatchedRun(
            harness_key="codex",
            event_timeout_seconds=harness.event_timeout_seconds,
            credential_bounded=False,
        )

    monkeypatch.setattr(harness, "_enqueue_run", _enqueue)


async def _drive(harness: RemoteHarness, agent_run_id):
    return harness.run(
        agent=SimpleNamespace(),
        conversation=SimpleNamespace(id=uuid7()),
        messages=[],
        ctx=SimpleNamespace(),
        options=_options(),
        agent_run_id=agent_run_id,
    )


class TestGivingUpCancelsTheHostRun:
    async def test_the_run_deadline_stops_the_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the cancel, the ACP agent runs on for as long as its own
        deadline allows, against a turn Lemma has already failed."""
        agent_run_id = uuid7()
        stream = _RecordingStream()
        harness = _harness(stream, event_timeout_seconds=0.0, stream_block_ms=1)
        _stub_dispatch(harness, monkeypatch)
        cancelled: list = []

        async def _cancel(run_id):
            cancelled.append(run_id)

        monkeypatch.setattr(harness, "_enqueue_host_cancel", _cancel)

        events = [event async for event in await _drive(harness, agent_run_id)]

        assert cancelled == [agent_run_id]
        assert events[-1].type is AgentEventType.ERROR


class TestStreamDeletion:
    async def test_a_finished_run_drops_its_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_run_id = uuid7()
        stream = _RecordingStream()
        harness = _harness(stream, event_timeout_seconds=0.0, stream_block_ms=1)
        _stub_dispatch(harness, monkeypatch)

        async def _cancel(_run_id):
            return None

        monkeypatch.setattr(harness, "_enqueue_host_cancel", _cancel)

        async for _event in await _drive(harness, agent_run_id):
            pass

        assert stream.deleted == [agent_run_id]

    async def test_a_consumer_that_walks_away_leaves_the_stream_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The host may still be appending to it, and the 24h TTL is the
        backstop for a run nobody ever comes back for."""
        agent_run_id = uuid7()
        stream = _RecordingStream()
        harness = _harness(stream, event_timeout_seconds=600.0, stream_block_ms=1)
        _stub_dispatch(harness, monkeypatch)

        async def _forever(**_kwargs):
            for index in range(1000):
                yield AgentEvent(
                    type=AgentEventType.STATUS,
                    data={"status": str(index)},
                    agent_run_id=agent_run_id,
                )

        monkeypatch.setattr(harness, "_consume", _forever)

        generator = await _drive(harness, agent_run_id)
        assert (await anext(generator)).type is AgentEventType.STATUS
        await generator.aclose()

        assert stream.deleted == []

    async def test_a_run_that_never_dispatched_deletes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_run_id = uuid7()
        stream = _RecordingStream()
        harness = _harness(stream)

        async def _refuse(**_kwargs):
            raise RuntimeError("Agent Host harness is unavailable")

        monkeypatch.setattr(harness, "_enqueue_run", _refuse)

        events = [event async for event in await _drive(harness, agent_run_id)]

        assert [event.type for event in events] == [AgentEventType.ERROR]
        assert stream.deleted == []
