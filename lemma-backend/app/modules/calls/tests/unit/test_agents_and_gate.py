"""Reading the agents' streams, and the gate in front of them."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.modules.calls.services.agents import CallAgents
from app.modules.calls.services.caller import Party
from app.modules.calls.services.signals import (
    Failed,
    Finished,
    Relay,
    RunStarted,
    Shown,
    Working,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.calls.services.gate import Caller, Route, route_turn
from app.modules.decisions.contracts import DecisionResult, DecisionUnavailableError
from app.modules.decisions.domain.answers import Answer
from app.modules.usage.contracts import UsageLimitExceededError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.unit


def _agents() -> tuple[CallAgents, list[object]]:
    """Agents whose conversation streams are empty, recording which they open."""
    watched: list[object] = []

    @asynccontextmanager
    async def stream(conversation_id):
        watched.append(conversation_id)

        async def nothing():
            return
            yield

        yield nothing()

    agents = CallAgents(
        call_id=uuid4(), party=Party(user_id=uuid4(), pod_id=uuid4()), stream=stream
    )
    return agents, watched


def _message(kind: str, tool: str, call_id: str, **fields: object) -> dict[str, object]:
    return {
        "type": "message",
        "data": {"kind": kind, "tool_name": tool, "tool_call_id": call_id, **fields},
    }


async def _decided(agents: CallAgents) -> None:
    """Until every pending decision -- whether a spawning run is finished --
    has been made: a condition, not a clock."""

    async def idle() -> bool:
        return not agents._catching_up

    await eventually(
        label="the call's pending decisions",
        probe=idle,
        done=bool,
        timeout_seconds=2,
        interval_seconds=0.01,
    )


def _signals(agents: CallAgents) -> list[object]:
    out = []
    while not agents._signals.empty():
        out.append(agents._signals.get_nowait())
    return out


def test_the_coordinators_relay_is_read_off_its_tool_call() -> None:
    agents, _ = _agents()
    agents._read(
        agents.call_id,
        _message(
            "TOOL_CALL",
            "relay_to_voice",
            "r1",
            tool_args={"text": " found it ", "urgency": "now"},
        ),
    )
    # A sub-agent cannot speak through the voice, whatever it calls.
    agents._read(
        uuid4(),
        _message("TOOL_CALL", "relay_to_voice", "r2", tool_args={"text": "me too"}),
    )
    assert _signals(agents) == [Relay(text="found it", urgency="now")]


def test_a_canvas_item_is_shown_once_its_tool_succeeds_from_anyone_in_the_call() -> (
    None
):
    agents, _ = _agents()
    child = uuid4()
    args = {
        "type": "WIDGET",
        "path": "/skills/lemma-widget/library/show/diagram.html",
        "data": {"title": "Plan", "mermaid": "flowchart LR; A-->B"},
    }
    agents._read(child, _message("TOOL_CALL", "display_resource", "c1", tool_args=args))
    agents._read(
        child,
        _message(
            "TOOL_RETURN",
            "display_resource",
            "c1",
            tool_result={"success": True, "item_id": "c1"},
        ),
    )
    agents._read(child, _message("TOOL_CALL", "display_resource", "c2", tool_args=args))
    agents._read(
        child,
        _message(
            "TOOL_RETURN",
            "display_resource",
            "c2",
            tool_result={"success": False, "error": "bad"},
        ),
    )
    assert _signals(agents) == [
        Shown(
            conversation_id=child,
            tool_call_id="c1",
            tool_name="display_resource",
            args=args,
            result={"success": True, "item_id": "c1"},
        )
    ]


async def test_a_spawned_sub_agent_is_followed_and_the_coordinators_run_state_is_reported() -> (
    None
):
    agents, watched = _agents()
    child = uuid4()
    agents._read(
        agents.call_id,
        _message(
            "TOOL_RETURN",
            "spawn_subagent",
            "s1",
            tool_result={"conversation_id": str(child)},
        ),
    )
    agents._read(agents.call_id, {"type": "status", "data": {}})
    agents._read(agents.call_id, {"type": "completed", "data": {}})
    # A sub-agent's run ending is not the call's.
    agents._read(child, {"type": "completed", "data": {}})
    await asyncio.sleep(0)
    await agents.close()
    assert watched == [child]
    assert _signals(agents) == [Working(running=True), Working(running=False)]


async def test_every_child_a_delegation_started_is_followed() -> None:
    agents, watched = _agents()
    first, second = uuid4(), uuid4()
    agents._read(
        agents.call_id,
        _message(
            "TOOL_RETURN",
            "delegate",
            "d1",
            tool_result={
                "children": [
                    {"task": 0, "conversation_id": str(first)},
                    {"task": 1, "error": "past the cap"},
                    {"task": 2, "conversation_id": str(second)},
                ]
            },
        ),
    )
    await asyncio.sleep(0)
    await agents.close()
    assert watched == [first, second]


async def test_a_stream_that_fails_tells_the_voice_and_does_not_stop_the_call_closing() -> (
    None
):
    @asynccontextmanager
    async def broken(conversation_id):
        raise RedisConnectionError("gone")
        yield

    agents = CallAgents(
        call_id=uuid4(),
        party=Party(user_id=uuid4(), pod_id=uuid4()),
        stream=broken,
        retry_seconds=0,
    )
    agents._watch(agents.call_id)
    await agents._watching[agents.call_id]
    await agents.close()
    [signal] = _signals(agents)
    assert isinstance(signal, Relay)
    assert "stopped arriving" in signal.text


def _caller() -> Caller:
    return Caller(
        user_id=uuid4(), organization_id=uuid4(), pod_id=uuid4(), call_id=uuid4()
    )


@pytest.mark.parametrize(
    ("answer", "route"),
    [
        (Answer("voice", 0.95), Route.VOICE),
        (Answer("voice", None), Route.VOICE),
        (Answer("voice", 0.55), Route.TEAM),  # unsure is work
        (Answer(None, None), Route.TEAM),
        (Answer("team", 0.99), Route.TEAM),
    ],
)
async def test_only_a_confident_conversation_answer_keeps_a_turn_from_the_agents(
    answer: Answer, route: Route
) -> None:
    async def decide(request, *, caller):
        assert request.priority == "interactive"
        assert request.evidence["latest_turn"] == "sounds good"
        return DecisionResult(answers={"route": answer}, provider="model", model=None)

    assert (
        await route_turn(
            "sounds good", ["Voice: three options"], _caller(), decide=decide
        )
        is route
    )


@pytest.mark.parametrize(
    "failure",
    [
        DecisionUnavailableError("timeout"),
        UsageLimitExceededError("spend is used up"),
        UnexpectedModelBehavior("the model answered nonsense"),
        OperationalError("select 1", {}, Exception("connection reset")),
        RedisConnectionError("redis went away"),
        TimeoutError(),
    ],
    ids=lambda failure: type(failure).__name__,
)
async def test_a_gate_that_cannot_decide_sends_the_turn_to_the_agents(
    failure: BaseException,
) -> None:
    async def decide(request, *, caller):
        raise failure

    assert await route_turn("book it", [], _caller(), decide=decide) is Route.TEAM


def _run_frame(kind: str, run_id: str, **fields: object) -> dict[str, object]:
    return {"type": kind, "agent_run_id": run_id, **fields}


def test_working_lasts_while_anyone_in_the_call_runs() -> None:
    agents, _ = _agents()
    child = uuid4()
    agents._read(agents.call_id, _run_frame("status", "r1", data={}))
    agents._read(child, _run_frame("status", "c1", data={}))
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    # The coordinator finished, but its sub-agent is still going.
    assert [s for s in _signals(agents) if isinstance(s, Working)] == [
        Working(running=True)
    ]
    agents._read(child, _run_frame("completed", "c1", data={}))
    assert [s for s in _signals(agents) if isinstance(s, Working)] == [
        Working(running=False)
    ]


def test_a_failed_coordinator_run_says_why_and_is_not_also_called_silent() -> None:
    agents, _ = _agents()
    agents._read(agents.call_id, _run_frame("status", "r1", data={}))
    agents._read(
        agents.call_id, _run_frame("error", "r1", data="The usage limit is reached.")
    )
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    signals = _signals(agents)
    assert Failed(reason="The usage limit is reached.") in signals
    assert not [s for s in signals if isinstance(s, Finished)]


def test_a_run_that_ends_without_relaying_is_heard_with_its_last_words() -> None:
    agents, _ = _agents()
    agents._read(agents.call_id, _run_frame("status", "r1", data={}))
    agents._read(
        agents.call_id,
        {
            "type": "message",
            "data": {
                "agent_run_id": "r1",
                "role": "assistant",
                "kind": "TEXT",
                "text": "Goa Palms is the cheapest.",
            },
        },
    )
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    assert Finished(answer="Goa Palms is the cheapest.") in _signals(agents)


def test_a_run_that_relayed_is_not_reported_again_when_it_ends() -> None:
    agents, _ = _agents()
    agents._read(agents.call_id, _run_frame("status", "r1", data={}))
    agents._read(
        agents.call_id,
        _message(
            "TOOL_CALL",
            "relay_to_voice",
            "x1",
            agent_run_id="r1",
            tool_args={"text": "on it", "urgency": "context"},
        ),
    )
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    assert not [s for s in _signals(agents) if isinstance(s, Finished)]


def _default_runtime_turn(run_id: str) -> list[dict[str, object]]:
    """What the default runtime publishes for a turn: the caller's message as
    the run's input, then the run's own messages, then its end. No ``status``
    frame -- only Agent Host sends those."""
    return [
        {
            "type": "message",
            "agent_run_id": run_id,
            "data": {
                "agent_run_id": run_id,
                "role": "user",
                "kind": "TEXT",
                "text": "[caller] score?",
            },
        },
        {
            "type": "message",
            "agent_run_id": run_id,
            "data": {
                "agent_run_id": run_id,
                "role": "assistant",
                "kind": "TOOL_CALL",
                "tool_name": "exec_command",
                "tool_call_id": "e1",
            },
        },
    ]


def test_a_run_without_a_status_frame_is_still_heard_starting_and_followed() -> None:
    agents, _ = _agents()
    for frame in _default_runtime_turn("r1"):
        agents._read(agents.call_id, frame)
    signals = _signals(agents)
    assert RunStarted(agent_run_id="r1") in signals
    assert Working(running=True) in signals
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    assert Working(running=False) in _signals(agents)


def test_every_new_coordinator_run_is_announced_even_while_already_working() -> None:
    agents, _ = _agents()
    child = uuid4()
    agents._read(child, _run_frame("message", "c1", data={}))
    agents._read(agents.call_id, _run_frame("message", "r1", data={}))
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    agents._read(agents.call_id, _run_frame("message", "r2", data={}))
    starts = [s for s in _signals(agents) if isinstance(s, RunStarted)]
    assert starts == [RunStarted(agent_run_id="r1"), RunStarted(agent_run_id="r2")]


def test_a_late_frame_of_an_ended_run_does_not_start_it_again() -> None:
    agents, _ = _agents()
    agents._read(agents.call_id, _run_frame("message", "r1", data={}))
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    _signals(agents)
    agents._read(agents.call_id, _run_frame("message", "r1", data={}))
    assert _signals(agents) == []


async def test_a_run_that_handed_work_to_sub_agents_is_not_called_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.calls.services import agents as agents_module

    monkeypatch.setattr(agents_module, "_SPAWN_SETTLE_SECONDS", 0.01)
    agents, _ = _agents()
    child = uuid4()
    agents._read(agents.call_id, _run_frame("message", "r1", data={}))
    agents._read(
        agents.call_id,
        _message("TOOL_CALL", "delegate", "d1", agent_run_id="r1", tool_args={}),
    )
    # Discovery reads the child's `parent_run_id`.
    agents._lineage.spawned(child, "r1")
    agents._read(child, _run_frame("message", "c1", data={}))
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    await _decided(agents)
    assert not [s for s in _signals(agents) if isinstance(s, Finished)]
    await agents.close()


async def test_a_delegate_that_started_no_child_still_reaches_the_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.calls.services import agents as agents_module

    monkeypatch.setattr(agents_module, "_SPAWN_SETTLE_SECONDS", 0.01)
    agents, _ = _agents()
    agents._read(agents.call_id, _run_frame("message", "r1", data={}))
    agents._read(
        agents.call_id,
        _message("TOOL_CALL", "delegate", "d1", agent_run_id="r1", tool_args={}),
    )
    agents._read(
        agents.call_id,
        {
            "type": "message",
            "data": {
                "agent_run_id": "r1",
                "role": "assistant",
                "kind": "TEXT",
                "text": "Nothing to hand off.",
            },
        },
    )
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    await _decided(agents)
    assert Finished(answer="Nothing to hand off.") in _signals(agents)
    await agents.close()


def test_a_relay_a_catch_up_already_said_is_not_said_again_when_its_frame_lands() -> (
    None
):
    agents, _ = _agents()
    agents._heard_relays.add("x1")
    agents._read(
        agents.call_id,
        _message(
            "TOOL_CALL",
            "relay_to_voice",
            "x1",
            tool_args={"text": "India 143 for 1.", "urgency": "now"},
        ),
    )
    assert not [s for s in _signals(agents) if isinstance(s, Relay)]


async def test_a_stream_back_after_a_drop_reads_back_only_what_was_missed() -> None:
    """A relay heard live is not said twice; one published while the stream
    was down is; the canvas and the working state are read back too."""
    subscriptions = 0
    resumed = asyncio.Event()

    @asynccontextmanager
    async def stream(conversation_id):
        nonlocal subscriptions
        subscriptions += 1
        first = subscriptions == 1

        async def frames():
            if first:
                yield _message(
                    "TOOL_CALL",
                    "relay_to_voice",
                    "heard",
                    tool_args={"text": "On it.", "urgency": "context"},
                )
                raise RedisConnectionError("pubsub dropped")
            resumed.set()
            await asyncio.Event().wait()
            yield {}

        yield frames()

    agents = CallAgents(
        call_id=uuid4(),
        party=Party(user_id=uuid4(), pod_id=uuid4()),
        stream=stream,
        retry_seconds=0,
    )
    since_seen: list[object] = []

    async def read_back(conversation_id, since):
        since_seen.append(since)
        return (
            [
                ("heard", "On it.", "context"),
                ("missed", "India 143 for 1.", "when_idle"),
            ],
            [],
            True,
        )

    agents._read_back = read_back  # type: ignore[method-assign]
    agents._watch(agents.call_id)
    async with asyncio.timeout(2):
        await resumed.wait()

    async def caught_up() -> bool:
        return bool(since_seen) and not agents._catching_up

    await eventually(
        label="the catch-up read back and finished",
        probe=caught_up,
        done=bool,
        timeout_seconds=2,
        interval_seconds=0.01,
    )
    await agents.close()
    signals = _signals(agents)
    relays = [s for s in signals if isinstance(s, Relay)]
    assert relays == [
        Relay(text="On it.", urgency="context"),
        Relay(text="India 143 for 1.", urgency="when_idle"),
    ]
    assert Working(running=True) in signals
    assert len(since_seen) == 1


async def test_an_earlier_runs_busy_child_does_not_swallow_a_later_runs_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.calls.services import agents as agents_module

    monkeypatch.setattr(agents_module, "_SPAWN_SETTLE_SECONDS", 0.01)
    agents, _ = _agents()
    earlier_child = uuid4()

    def delegate(run_id: str, tool_call_id: str) -> None:
        agents._read(
            agents.call_id,
            _message(
                "TOOL_CALL",
                "delegate",
                tool_call_id,
                agent_run_id=run_id,
                tool_args={},
            ),
        )

    # Run 1 delegates, and its child is still working.
    agents._read(agents.call_id, _run_frame("message", "r1", data={}))
    delegate("r1", "d1")
    agents._lineage.spawned(earlier_child, "r1")
    agents._read(earlier_child, _run_frame("message", "c1", data={}))
    agents._read(agents.call_id, _run_frame("completed", "r1", data={}))
    # Run 2 delegates too, but starts no child of its own.
    agents._read(agents.call_id, _run_frame("message", "r2", data={}))
    delegate("r2", "d2")
    agents._read(
        agents.call_id,
        {
            "type": "message",
            "data": {
                "agent_run_id": "r2",
                "role": "assistant",
                "kind": "TEXT",
                "text": "Already on it.",
            },
        },
    )
    agents._read(agents.call_id, _run_frame("completed", "r2", data={}))
    await _decided(agents)
    finished = [s for s in _signals(agents) if isinstance(s, Finished)]
    assert finished == [Finished(answer="Already on it.")]
    await agents.close()


async def test_a_child_started_by_a_later_run_is_not_counted_for_an_earlier_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run A delegates but starts no child and ends; run B then starts one
    while A waits to hear. A's answer still reaches the person."""
    from app.modules.calls.services import agents as agents_module

    monkeypatch.setattr(agents_module, "_SPAWN_SETTLE_SECONDS", 0.05)
    agents, _ = _agents()

    def delegate(run_id: str, tool_call_id: str) -> None:
        agents._read(
            agents.call_id,
            _message(
                "TOOL_CALL", "delegate", tool_call_id, agent_run_id=run_id, tool_args={}
            ),
        )

    agents._read(agents.call_id, _run_frame("message", "rA", data={}))
    delegate("rA", "dA")
    agents._read(
        agents.call_id,
        {
            "type": "message",
            "data": {
                "agent_run_id": "rA",
                "role": "assistant",
                "kind": "TEXT",
                "text": "Nothing to hand off.",
            },
        },
    )
    agents._read(agents.call_id, _run_frame("completed", "rA", data={}))
    # Run B, during A's wait, delegates and its child starts.
    agents._read(agents.call_id, _run_frame("message", "rB", data={}))
    delegate("rB", "dB")
    child_of_b = uuid4()
    agents._lineage.spawned(child_of_b, "rB")
    agents._read(child_of_b, _run_frame("message", "cB", data={}))
    await _decided(agents)
    finished = [s for s in _signals(agents) if isinstance(s, Finished)]
    assert finished == [Finished(answer="Nothing to hand off.")]
    await agents.close()


async def test_a_late_child_of_an_ended_run_is_its_own_not_the_live_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run A delegates and ends; A's child first starts while run B -- which
    also called a spawning tool but started nothing -- is live. The child is
    A's (its `parent_run_id`), so A is held back and B's answer goes out."""
    from app.modules.calls.services import agents as agents_module

    monkeypatch.setattr(agents_module, "_SPAWN_SETTLE_SECONDS", 0.05)
    agents, _ = _agents()

    def delegate(run_id: str, tool_call_id: str) -> None:
        agents._read(
            agents.call_id,
            _message(
                "TOOL_CALL", "delegate", tool_call_id, agent_run_id=run_id, tool_args={}
            ),
        )

    def says(run_id: str, text: str) -> None:
        agents._read(
            agents.call_id,
            {
                "type": "message",
                "data": {
                    "agent_run_id": run_id,
                    "role": "assistant",
                    "kind": "TEXT",
                    "text": text,
                },
            },
        )

    agents._read(agents.call_id, _run_frame("message", "rA", data={}))
    delegate("rA", "dA")
    says("rA", "Handed it off.")
    agents._read(agents.call_id, _run_frame("completed", "rA", data={}))
    agents._read(agents.call_id, _run_frame("message", "rB", data={}))
    delegate("rB", "dB")
    child_of_a = uuid4()
    agents._lineage.spawned(child_of_a, "rA")
    agents._read(child_of_a, _run_frame("message", "cA", data={}))
    says("rB", "Nothing more to do.")
    agents._read(agents.call_id, _run_frame("completed", "rB", data={}))
    await _decided(agents)
    finished = [s for s in _signals(agents) if isinstance(s, Finished)]
    assert finished == [Finished(answer="Nothing more to do.")]
    await agents.close()


def test_a_sub_agents_own_sub_agent_traces_back_to_the_coordinator_run() -> None:
    from app.modules.calls.services.lineage import Lineage

    call_id, child, grandchild = uuid4(), uuid4(), uuid4()
    lineage = Lineage(call_id)
    lineage.ran("r1", call_id)
    lineage.spawned(child, "r1")
    lineage.ran("c1", child)
    lineage.spawned(grandchild, "c1")
    assert lineage.coordinator_run(grandchild) == "r1"
    assert lineage.coordinator_run(uuid4()) is None


async def test_a_catch_up_retries_a_store_that_blinked_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from app.modules.calls.services import agents as agents_module

    monkeypatch.setattr(agents_module, "_CATCH_UP_RETRY_SECONDS", (0.0, 0.0, 0.0))
    agents, _ = _agents()
    tries = 0

    async def read_back(conversation_id, since):
        nonlocal tries
        tries += 1
        if tries < 3:
            raise OperationalError("select", {}, Exception("connection reset"))
        return [("missed", "India 143 for 1.", "now")], [], False

    agents._read_back = read_back  # type: ignore[method-assign]
    await agents._catch_up(agents.call_id, datetime.now(UTC))
    assert tries == 3
    assert Relay(text="India 143 for 1.", urgency="when_idle") in _signals(agents)


async def test_a_catch_up_that_cannot_recover_tells_the_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from app.modules.calls.services import agents as agents_module

    monkeypatch.setattr(agents_module, "_CATCH_UP_RETRY_SECONDS", (0.0,))
    agents, _ = _agents()

    async def read_back(conversation_id, since):
        raise OperationalError("select", {}, Exception("down"))

    agents._read_back = read_back  # type: ignore[method-assign]
    await agents._catch_up(agents.call_id, datetime.now(UTC))
    relays = [s for s in _signals(agents) if isinstance(s, Relay)]
    assert relays == [Relay(text=agents_module._CATCH_UP_LOST, urgency="when_idle")]
