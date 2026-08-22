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

from app.modules.agent.domain.agent_host import AgentHostEventType
from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType
from app.modules.agent.infrastructure.agent_host.event_stream import (
    StreamBatch,
    StreamedEvent,
)
from app.modules.agent.infrastructure.harnesses.agent_host import (
    harness as harness_module,
)
from app.modules.agent.infrastructure.harnesses.agent_host.harness import RemoteHarness
from app.modules.agent.infrastructure.harnesses.agent_host.run_window import (
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


def _sleeping_since(*, tool_call_id: str):
    """Stand in for the wait row a snoozing run leaves behind."""

    async def _lookup(uow, *, agent_run_id):
        del uow, agent_run_id
        return SimpleNamespace(tool_call_id=tool_call_id)

    return _lookup


def _awake():
    async def _lookup(uow, *, agent_run_id):
        del uow, agent_run_id
        return None

    return _lookup


def _uow_factory():
    """A unit of work that opens and closes and touches nothing."""

    class _Uow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return _Uow()


def _harness(stream, *, uow_factory=None, **kwargs) -> RemoteHarness:
    return RemoteHarness(uow_factory=uow_factory, event_stream=stream, **kwargs)


def _stub_dispatch(harness: RemoteHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _enqueue(**_kwargs):
        return DispatchedRun(
            harness_key="codex",
            event_timeout_seconds=harness.event_timeout_seconds,
            credential_bounded=False,
        )

    # Dispatch lives in its own module now; the harness imported the name,
    # so that binding is what a stub has to replace.
    monkeypatch.setattr(harness_module, "enqueue_run", _enqueue)


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

        monkeypatch.setattr(harness_module, "enqueue_run", _refuse)

        events = [event async for event in await _drive(harness, agent_run_id)]

        assert [event.type for event in events] == [AgentEventType.ERROR]
        assert stream.deleted == []


class _TerminalStream:
    """A stream that reports one finished run and nothing else."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.deleted: list = []
        self.sent = False

    async def read(self, *, run_id, after_id="0-0", block_ms=1000):
        if self.sent:
            return StreamBatch([], cursor=after_id)
        self.sent = True
        return StreamBatch(
            [
                StreamedEvent(
                    stream_id="1-0",
                    sequence=1,
                    type=AgentHostEventType.TERMINAL.value,
                    object_id=None,
                    payload={"state": self.state},
                )
            ],
            cursor="1-0",
        )

    async def delete(self, *, run_id) -> None:
        self.deleted.append(run_id)


class TestATurnEndedForASleepingAgent:
    """A remote `snooze` ends its turn from the outside, so the state lies.

    Lemma asks the host to stop, and the host reports what it saw — CANCELLED
    when the stop landed first, SUCCEEDED when the agent finished talking before
    it did, and which one wins is a race between a poke and a model. Taken
    literally, the first says the user pressed Stop and the second says the turn
    is over; both leave the conversation looking finished while a timer is still
    counting down to wake it. What actually happened is on the wait row.
    """

    @pytest.mark.parametrize("state", ["CANCELLED", "SUCCEEDED"])
    async def test_either_ending_is_reported_as_waiting(
        self, state: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_run_id = uuid7()
        stream = _TerminalStream(state)
        harness = _harness(
            stream,
            uow_factory=_uow_factory,
            event_timeout_seconds=30.0,
            stream_block_ms=1,
        )
        _stub_dispatch(harness, monkeypatch)
        monkeypatch.setattr(
            harness_module,
            "run_suspended_on",
            _sleeping_since(tool_call_id="lemma-mcp-1"),
        )

        events = [event async for event in await _drive(harness, agent_run_id)]

        assert events[-1].type is AgentEventType.WAITING
        # The shape the in-process pause yields, so one reader serves both.
        assert events[-1].data["tool_call_id"] == "lemma-mcp-1"
        assert events[-1].data["kind"] == "snooze"

    async def test_a_turn_that_simply_ended_is_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_run_id = uuid7()
        stream = _TerminalStream("SUCCEEDED")
        harness = _harness(
            stream,
            uow_factory=_uow_factory,
            event_timeout_seconds=30.0,
            stream_block_ms=1,
        )
        _stub_dispatch(harness, monkeypatch)
        monkeypatch.setattr(harness_module, "run_suspended_on", _awake())

        events = [event async for event in await _drive(harness, agent_run_id)]

        assert events[-1].type is AgentEventType.COMPLETED

    async def test_a_failure_is_still_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run that hit its context ceiling really did fail, wait row or not.

        Reporting it as WAITING would hide the error behind a conversation that
        looks like it is patiently sleeping. The timer still wakes it either way.
        """
        agent_run_id = uuid7()
        stream = _TerminalStream("FAILED")
        harness = _harness(
            stream,
            uow_factory=_uow_factory,
            event_timeout_seconds=30.0,
            stream_block_ms=1,
        )
        _stub_dispatch(harness, monkeypatch)
        monkeypatch.setattr(
            harness_module, "run_suspended_on", _sleeping_since(tool_call_id="x")
        )

        events = [event async for event in await _drive(harness, agent_run_id)]

        assert events[-1].type is AgentEventType.ERROR
