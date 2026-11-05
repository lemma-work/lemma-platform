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

from app.modules.agent.domain.runtime_profiles import (
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.modules.agent.agent_host.domain.host import (
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType
from app.modules.agent.agent_host.control.event_stream import (
    StreamBatch,
    StreamedEvent,
)
from app.modules.agent.agent_host.harness import harness as harness_module
from app.modules.agent.agent_host.harness import harness_events as harness_events_module
from app.modules.agent.agent_host.control.recovery import BusyHost
from app.modules.agent.agent_host.harness.harness import RemoteHarness
from app.modules.agent.agent_host.harness.run_window import (
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
        runtime_profile=RuntimeSnapshot(
            profile_id=str(uuid7()),
            profile_name="Laptop",
            harness_id=str(uuid7()),
            scope=RuntimeProfileScope.PERSONAL,
            protocol=RuntimeProfileProtocol.AGENT_HOST,
            config={"harness_snapshot_revision": "1"},
        ),
        extra={},
    )


def _sleeping_since(*, tool_call_id: str):
    """Stand in for the wait row a snoozing run leaves behind."""

    async def _lookup(uow, *, agent_run_id):
        del uow, agent_run_id
        return SimpleNamespace(tool_call_id=tool_call_id, tool_name="wait_for")

    return _lookup


def _awake():
    async def _lookup(uow, *, agent_run_id):
        del uow, agent_run_id
        return

    return _lookup


def _uow_factory():
    """A unit of work that opens and closes and touches nothing."""

    class _Uow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self) -> None:
            return None

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


class TestARunRefusedBeforeItStarted:
    """The Agent Host can refuse a START_RUN before journaling anything.

    Its harness fence rejects a command naming a configuration the machine has
    replaced, and it does so ahead of ``accept_start`` -- so the run has no
    events at all, and the only thing that ever terminalizes it is the
    rejection receipt, which ``apply_rejection`` writes onto the lease as
    ``error_detail``.

    Lemma read the lease's *state* and ignored its reason, so every one of
    these surfaced as "Agent Host reached terminal checkpoint FAILED without
    its required terminal event" -- a sentence about Lemma's own plumbing,
    naming neither the agent nor the cause, shown to the person who pressed
    send.
    """

    @staticmethod
    def _refused_repository(detail: str | None):
        class _Repository:
            def __init__(self, _uow) -> None:
                pass

            async def reconcile_expired_run(self, *, run_id):
                del run_id
                return

            async def get_run_lease(self, *, run_id):
                del run_id
                return SimpleNamespace(
                    state=AgentHostRunState.FAILED.value,
                    accepted_at=None,
                    error_code="CONFIG_REVISION_STALE",
                    error_detail=detail,
                )

        return _Repository

    async def _last_event(
        self, monkeypatch: pytest.MonkeyPatch, *, detail: str | None
    ) -> AgentEvent:
        agent_run_id = uuid7()
        harness = _harness(
            _RecordingStream(),
            uow_factory=_uow_factory,
            event_timeout_seconds=30.0,
            lease_check_seconds=0.0,
            stream_block_ms=1,
            terminal_event_grace_seconds=0.0,
        )
        _stub_dispatch(harness, monkeypatch)
        monkeypatch.setattr(
            harness_module,
            "AgentHostDispatchRepository",
            self._refused_repository(detail),
        )

        events = [event async for event in await _drive(harness, agent_run_id)]
        return events[-1]

    async def test_the_reason_the_lease_recorded_is_what_the_user_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        last = await self._last_event(
            monkeypatch,
            detail=(
                "That computer and Lemma disagree about how Claude Code is "
                "configured; try sending again in a moment"
            ),
        )

        assert last.type is AgentEventType.ERROR
        assert "Claude Code" in last.data
        assert "terminal event" not in last.data

    async def test_a_lease_with_no_recorded_reason_still_says_something(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fallback keeps the case it was written for.

        A run that really did stop reporting has no recorded reason to prefer,
        and saying so is better than ending on silence.
        """
        last = await self._last_event(monkeypatch, detail=None)

        assert last.type is AgentEventType.ERROR
        assert "without its required terminal event" in last.data


class TestATurnEndedForASleepingAgent:
    """A remote `wait_for` ends its turn from the outside, so the state lies.

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
            harness_events_module,
            "run_suspended_on",
            _sleeping_since(tool_call_id="lemma-mcp-1"),
        )

        events = [event async for event in await _drive(harness, agent_run_id)]

        assert events[-1].type is AgentEventType.WAITING
        # The shape the in-process pause yields, so one reader serves both.
        assert events[-1].data["tool_call_id"] == "lemma-mcp-1"
        assert events[-1].data["kind"] == "wait_for"

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
        monkeypatch.setattr(harness_events_module, "run_suspended_on", _awake())
        # Nor ended by Lemma for a pause it sat parked on past its window.
        monkeypatch.setattr(
            harness_events_module, "run_suspended_while_parked", _awake()
        )

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
            harness_events_module,
            "run_suspended_on",
            _sleeping_since(tool_call_id="x"),
        )

        events = [event async for event in await _drive(harness, agent_run_id)]

        assert events[-1].type is AgentEventType.ERROR


class TestARunQueuedBehindAFullComputer:
    """A computer with every slot taken is busy, not missing.

    The run used to fail at its wait deadline -- "No Agent Host received the
    run" -- because two other runs on the same machine went long, after showing
    nothing at all for those minutes. It waits now, and says what on.
    """

    @staticmethod
    def _queued_repository(*, busy: bool, expired: list):
        class _Repository:
            def __init__(self, _uow) -> None:
                pass

            async def reconcile_expired_run(self, *, run_id):
                del run_id

            async def get_run_lease(self, *, run_id):
                del run_id
                return SimpleNamespace(
                    state=AgentHostRunState.QUEUED_FOR_HOST.value,
                    accepted_at=None,
                    error_detail=None,
                )

            async def hold_for_busy_host(self, *, run_id, wait_seconds):
                del run_id, wait_seconds
                return BusyHost(name="My Mac", active_runs=2) if busy else None

            async def expire_unaccepted_run(self, *, run_id):
                expired.append(run_id)
                return AgentHostRunState.FAILED

        return _Repository

    async def _events(
        self, monkeypatch: pytest.MonkeyPatch, *, busy: bool, stopped: bool = False
    ):
        expired: list = []
        harness = _harness(
            _RecordingStream(),
            uow_factory=_uow_factory,
            # Outlives the one-second wait, so a run that is not held fails
            # on the wait and one that is held runs into this instead -- by a
            # margin a loaded runner cannot close (at 1.5s it once fired first).
            event_timeout_seconds=3.0,
            lease_check_seconds=0.0,
            stream_block_ms=1,
        )
        _stub_dispatch(harness, monkeypatch)
        repository = self._queued_repository(busy=busy, expired=expired)
        monkeypatch.setattr(harness_module, "AgentHostDispatchRepository", repository)

        async def _cancel(_run_id) -> None:
            return None

        monkeypatch.setattr(harness, "_enqueue_host_cancel", _cancel)
        options = _options()
        options.runtime_profile.config["host_wait_timeout_seconds"] = 1
        if stopped:

            async def _stop() -> bool:
                return True

            options.should_stop = _stop
        run = harness.run(
            agent=SimpleNamespace(),
            conversation=SimpleNamespace(id=uuid7()),
            messages=[],
            ctx=SimpleNamespace(),
            options=options,
            agent_run_id=uuid7(),
        )
        return [event async for event in run], expired

    async def test_it_waits_past_the_deadline_and_says_so_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events, expired = await self._events(monkeypatch, busy=True)

        assert expired == []
        notices = [event for event in events if event.type is AgentEventType.STATUS]
        assert [notice.data["detail"] for notice in notices] == [
            "Waiting for a free slot on My Mac: 2 runs already going"
        ]
        # The run's own deadline still ends the wait.
        assert events[-1].type is AgentEventType.ERROR

    async def test_a_computer_that_is_not_full_still_fails_the_wait(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events, expired = await self._events(monkeypatch, busy=False)

        assert len(expired) == 1
        assert not any(event.type is AgentEventType.STATUS for event in events)
        assert events[-1].data == (
            "No Agent Host received the run before its wait deadline"
        )

    async def test_a_stopped_run_does_not_keep_its_place(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Held, it would be handed out once a slot freed -- after its cancel."""
        events, expired = await self._events(monkeypatch, busy=True, stopped=True)

        assert len(expired) == 1
        assert not any(event.type is AgentEventType.STATUS for event in events)
