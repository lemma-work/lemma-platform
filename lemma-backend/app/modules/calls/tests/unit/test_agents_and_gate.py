"""Reading the agents' streams, and the gate in front of them."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.modules.calls.services.agents import CallAgents, Party, Relay, Shown, Working
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


async def test_a_stream_that_fails_tells_the_voice_and_does_not_stop_the_call_closing() -> (
    None
):
    @asynccontextmanager
    async def broken(conversation_id):
        raise RedisConnectionError("gone")
        yield

    agents = CallAgents(
        call_id=uuid4(), party=Party(user_id=uuid4(), pod_id=uuid4()), stream=broken
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
