"""A call between a scripted caller, a scripted voice and scripted agents.

What is under test is the session's own work: which turns reach the agents and
with what context, how relays reach the voice, what the caller's screen is
sent, and how the call ends.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from uuid import UUID, uuid4

import pytest

from app.modules.agent.contracts.calls import CanvasItem
from app.modules.calls.domain.voice import (
    AudioOut,
    EngineClosed,
    Heard,
    Inject,
    Said,
    TurnDone,
    VoiceEvent,
)
from app.modules.calls.services.agents import (
    AgentSignal,
    CallAgents,
    Relay,
    Shown,
    Working,
)
from app.modules.calls.services.gate import Route
from app.modules.calls.services.session import CallLimits, CallSession, CallStats

pytestmark = pytest.mark.unit


class Caller:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue[bytes | str | None] = asyncio.Queue()
        self.frames: list[dict[str, object]] = []
        self.audio: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    async def send_text(self, data: str) -> None:
        self.frames.append(json.loads(data))

    async def receive_message(self) -> bytes | str | None:
        return await self.inbox.get()

    def of(self, kind: str) -> list[dict[str, object]]:
        return [frame for frame in self.frames if frame["type"] == kind]


class Voice:
    output_sample_rate = 24_000

    def __init__(self) -> None:
        self.out: asyncio.Queue[VoiceEvent] = asyncio.Queue()
        self.heard: list[bytes] = []
        self.injected: list[tuple[str, Inject]] = []

    async def open(self) -> None: ...

    async def push_audio(self, pcm: bytes) -> None:
        self.heard.append(pcm)

    async def inject(self, text: str, mode: Inject) -> None:
        self.injected.append((text, mode))

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            event = await self.out.get()
            yield event
            if isinstance(event, EngineClosed):
                return

    async def close(self) -> None: ...


class Agents:
    call_id = uuid4()

    def __init__(self, canvas: list[CanvasItem] | None = None) -> None:
        self.turns: list[str] = []
        self.signals_in: asyncio.Queue[AgentSignal] = asyncio.Queue()
        self._canvas = canvas or []

    async def send_turn(self, content: str) -> None:
        self.turns.append(content)

    async def canvas(self) -> list[CanvasItem]:
        return self._canvas

    async def signals(self) -> AsyncIterator[AgentSignal]:
        while True:
            yield await self.signals_in.get()


class Gate:
    """Answers from a script, and remembers what it was asked."""

    def __init__(self, answers: dict[str, Route]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def __call__(self, turn: str, recent: Sequence[str]) -> Route:
        self.asked.append(turn)
        return self.answers.get(turn, Route.TEAM)


#: A chart shown the way any run shows one: a `show` widget given data, with
#: the line the voice is told about it as its `fallback`.
CHART: dict[str, object] = {
    "type": "WIDGET",
    "path": "/skills/lemma-widget/library/show/chart.html",
    "data": {"title": "Venue prices", "spec": {"mark": "bar"}},
    "fallback": "nightly rate by venue",
}


def _gate(answers: dict[str, Route]) -> Gate:
    return Gate(answers)


def _session(caller: Caller, voice: Voice, agents: Agents, gate) -> CallSession:
    return CallSession(
        socket=caller,
        engine=voice,  # type: ignore[arg-type]
        agents=agents,  # type: ignore[arg-type]
        gate=gate,
        limits=CallLimits(max_seconds=30, quiet_window_ms=0),
    )


async def _until(condition, *, within: float = 2.0) -> None:
    """Let the session's tasks run until ``condition`` holds."""
    async with asyncio.timeout(within):
        while not condition():
            await asyncio.sleep(0)


async def test_chit_chat_waits_and_rides_along_with_the_next_real_request() -> None:
    caller, voice, agents = Caller(), Voice(), Agents()
    gate = _gate({"hey there": Route.VOICE})
    session = _session(caller, voice, agents, gate)
    running = asyncio.create_task(session.run())
    await voice.out.put(Heard("hey there", final=True))
    await voice.out.put(Said("Hi! What can I do?", final=True))
    await _until(lambda: "hey there" in gate.asked)
    assert agents.turns == []
    await voice.out.put(Heard("plan a Goa offsite for twelve", final=True))
    await _until(lambda: agents.turns)
    assert len(agents.turns) == 1
    turn = agents.turns[0]
    assert turn.startswith("[caller] plan a Goa offsite for twelve")
    assert "Caller: hey there" in turn and "Voice: Hi! What can I do?" in turn
    caller.inbox.put_nowait(None)
    await running
    assert session.transcript().splitlines()[0] == "Caller: hey there"


async def test_microphone_audio_reaches_the_voice_unless_muted_and_its_audio_reaches_the_caller() -> (
    None
):
    caller, voice, agents = Caller(), Voice(), Agents()
    running = asyncio.create_task(_session(caller, voice, agents, _gate({})).run())
    caller.inbox.put_nowait(b"\x01\x02")
    caller.inbox.put_nowait(json.dumps({"type": "mute", "muted": True}))
    caller.inbox.put_nowait(b"\x03\x04")
    await voice.out.put(AudioOut(b"\x05\x06", 24_000))
    await _until(lambda: caller.audio)
    # The caller's frames are taken in order, so once `end` ends the call,
    # everything sent before it has been handled.
    caller.inbox.put_nowait(json.dumps({"type": "end"}))
    await running
    assert voice.heard == [b"\x01\x02"]
    assert caller.audio == [b"\x05\x06"]


async def test_relays_are_known_at_once_or_spoken_at_the_next_pause() -> None:
    caller, voice, agents = Caller(), Voice(), Agents()
    running = asyncio.create_task(_session(caller, voice, agents, _gate({})).run())
    await agents.signals_in.put(Relay("research started", "context"))
    await agents.signals_in.put(Relay("three venues found", "when_idle"))
    await voice.out.put(AudioOut(b"\x00\x00", 24_000))  # the voice is mid-sentence
    await _until(lambda: voice.injected and caller.audio)
    assert voice.injected[0] == ("[team] research started", Inject.CONTEXT)
    await voice.out.put(TurnDone())
    await _until(lambda: ("[team] three venues found", Inject.SPEAK) in voice.injected)
    caller.inbox.put_nowait(None)
    await running


async def test_what_is_shown_goes_to_the_screen_and_the_voice_is_told_what_is_on_it() -> (
    None
):
    caller, voice = Caller(), Voice()
    earlier = CanvasItem(
        conversation_id=uuid4(),
        tool_call_id="t-0",
        tool_name="display_resource",
        args={"type": "FILE", "path": "/me/plan.pdf"},
        result={"success": True},
        shown_at=__import__("datetime").datetime.now(),
    )
    agents = Agents(canvas=[earlier])
    running = asyncio.create_task(_session(caller, voice, agents, _gate({})).run())
    child: UUID = uuid4()
    await agents.signals_in.put(
        Shown(
            conversation_id=child,
            tool_call_id="t-1",
            tool_name="display_resource",
            args=CHART,
            result={"success": True, "item_id": "t-1"},
        )
    )
    await agents.signals_in.put(Working(running=True))
    await agents.signals_in.put(Working(running=True))
    # Signals are handled in order: once this one lands, so have the rest.
    await agents.signals_in.put(Relay("done", "context"))
    await _until(lambda: ("[team] done", Inject.CONTEXT) in voice.injected)
    items = caller.of("canvas.item")
    assert [item.get("replay") for item in items] == [True, None]
    assert items[1]["item"] == {
        "conversation_id": str(child),
        "tool_call_id": "t-1",
        "tool_name": "display_resource",
        "args": CHART,
        "result": {"success": True, "item_id": "t-1"},
    }
    assert (
        "[canvas] Now on the person's screen: Venue prices -- nightly rate by venue",
        Inject.CONTEXT,
    ) in voice.injected
    assert caller.of("work") == [{"type": "work", "running": True}]
    # The person paging back to the earlier file is told to the voice.
    caller.inbox.put_nowait(json.dumps({"type": "canvas.focus", "tool_call_id": "t-0"}))
    failed = json.dumps(
        {"type": "canvas.render_failed", "tool_call_id": "t-1", "error": "Parse error"}
    )
    # Reported twice, as a screen redrawing on reconnect would: told once.
    caller.inbox.put_nowait(failed)
    caller.inbox.put_nowait(failed)
    caller.inbox.put_nowait(json.dumps({"type": "end"}))
    await running
    assert len(agents.turns) == 1
    assert (
        "[canvas] The person is looking at: plan.pdf",
        Inject.CONTEXT,
    ) in voice.injected
    assert "'Venue prices' could not be drawn" in agents.turns[0]


async def test_a_voice_that_drops_ends_the_call_with_its_reason() -> None:
    caller, voice, agents = Caller(), Voice(), Agents()
    session = _session(caller, voice, agents, _gate({}))
    running = asyncio.create_task(session.run())
    await voice.out.put(
        EngineClosed("The voice connection dropped. Please call again.")
    )
    await running
    assert caller.of("ended") == [
        {"type": "ended", "reason": "The voice connection dropped. Please call again."}
    ]


async def test_a_request_the_agents_never_got_is_not_promised() -> None:
    caller, voice = Caller(), Voice()

    class Failing(Agents):
        async def send_turn(self, content: str) -> None:
            raise ConnectionError("database unavailable")

    agents = Failing()
    running = asyncio.create_task(_session(caller, voice, agents, _gate({})).run())
    await voice.out.put(Heard("book the cheapest one", final=True))
    await _until(lambda: voice.injected)
    assert (
        voice.injected and "did not receive the last request" in voice.injected[-1][0]
    )
    caller.inbox.put_nowait(None)
    await running


def test_the_session_takes_call_agents() -> None:
    # The stand-in above mirrors these; a renamed method should fail here first.
    for name in ("send_turn", "canvas", "signals"):
        assert hasattr(CallAgents, name)


async def test_a_turn_heard_while_another_is_routed_is_not_called_handled() -> None:
    caller, voice, agents = Caller(), Voice(), Agents()
    release = asyncio.Event()

    class Slow(Gate):
        async def __call__(self, turn: str, recent: Sequence[str]) -> Route:
            self.asked.append(turn)
            if turn == "find venues":
                await release.wait()
            return Route.TEAM

    running = asyncio.create_task(_session(caller, voice, agents, Slow({})).run())
    await voice.out.put(Heard("find venues", final=True))
    await voice.out.put(Heard("and flights too", final=True))
    await _until(lambda: len(caller.of("transcript")) == 2)
    release.set()
    await _until(lambda: len(agents.turns) == 2)
    first, second = agents.turns
    # The second request was not sent to the agents as already handled
    # context of the first: it arrives as its own turn.
    assert "and flights too" not in first
    assert second.startswith("[caller] and flights too")
    caller.inbox.put_nowait(None)
    await running


def test_a_canvas_item_whose_data_is_json_text_is_described_by_its_title() -> None:
    from app.modules.calls.services.session import _describe

    shown = Shown(
        conversation_id=uuid4(),
        tool_call_id="t1",
        tool_name="display_resource",
        args={"type": "WIDGET", "data": json.dumps({"title": "Venues"})},
        result={"success": True},
    )
    assert _describe(shown)[0] == "Venues"


async def test_a_call_counts_the_audio_and_turns_it_carried() -> None:
    caller, voice, agents = Caller(), Voice(), Agents()
    session = _session(caller, voice, agents, _gate({"hello": Route.VOICE}))
    running = asyncio.create_task(session.run())
    caller.inbox.put_nowait(b"\x00" * 640)
    caller.inbox.put_nowait(json.dumps({"type": "mute", "muted": True}))
    caller.inbox.put_nowait(b"\x00" * 640)  # muted: not counted
    await voice.out.put(Heard("hello", final=True))
    await voice.out.put(Said("Hi there.", final=True))
    await _until(lambda: session.stats.voice_lines)
    caller.inbox.put_nowait(json.dumps({"type": "text", "text": "and Pune?"}))
    caller.inbox.put_nowait(json.dumps({"type": "end"}))
    await running
    # A typed turn is a turn too.
    assert session.stats == CallStats(audio_in_bytes=640, caller_turns=2, voice_lines=1)


async def test_the_agents_are_told_what_the_voice_was_told_once_with_their_first_turn() -> (
    None
):
    # The voice was briefed with the chat the call grew out of; the
    # coordinator, never told, answered as if nothing had been said.
    caller, voice, agents = Caller(), Voice(), Agents()
    session = CallSession(
        socket=caller,
        engine=voice,  # type: ignore[arg-type]
        agents=agents,  # type: ignore[arg-type]
        gate=_gate({}),
        limits=CallLimits(max_seconds=30, quiet_window_ms=0),
        background="Recent chat: they asked for Goa venues for 12.",
    )
    running = asyncio.create_task(session.run())
    await voice.out.put(Heard("make it fifteen people", final=True))
    await _until(lambda: agents.turns)
    await voice.out.put(Heard("and near the beach", final=True))
    await _until(lambda: len(agents.turns) == 2)
    caller.inbox.put_nowait(None)
    await running
    first, second = agents.turns
    assert "Before the call, the voice was told:" in first
    assert "Goa venues for 12" in first
    assert "Goa venues" not in second


async def test_a_turn_the_agents_never_got_leaves_the_brief_for_the_next() -> None:
    caller, voice = Caller(), Voice()

    class FailsOnce(Agents):
        async def send_turn(self, content: str) -> None:
            if not self.turns:
                self.turns.append("")  # the failed attempt
                raise ConnectionError("database unavailable")
            await super().send_turn(content)

    agents = FailsOnce()
    session = CallSession(
        socket=caller,
        engine=voice,  # type: ignore[arg-type]
        agents=agents,  # type: ignore[arg-type]
        gate=_gate({}),
        limits=CallLimits(max_seconds=30, quiet_window_ms=0),
        background="Recent chat: Goa venues for 12.",
    )
    running = asyncio.create_task(session.run())
    await voice.out.put(Heard("make it fifteen", final=True))
    await _until(lambda: voice.injected)
    await voice.out.put(Heard("make it fifteen", final=True))
    await _until(lambda: len(agents.turns) == 2)
    caller.inbox.put_nowait(None)
    await running
    assert "Goa venues for 12" in agents.turns[1]


async def test_a_resumed_call_tells_the_voice_what_the_screen_shows() -> None:
    # On dev the voice said the match scores were on the screen after a
    # reconnect that showed nothing. The replayed screen and what the voice
    # believes it shows have to be the same thing.
    from datetime import datetime

    def shown(call_id: str, name: str, fallback: str) -> CanvasItem:
        return CanvasItem(
            conversation_id=uuid4(),
            tool_call_id=call_id,
            tool_name="display_resource",
            args={"type": "WIDGET", "name": name, "fallback": fallback},
            result={"success": True},
            shown_at=datetime.now(),
        )

    caller, voice = Caller(), Voice()
    agents = Agents(
        canvas=[
            shown("a", "Fixtures", "This week's fixtures"),
            shown("b", "Scores", "India 2-0 up in NZ"),
        ]
    )
    running = asyncio.create_task(_session(caller, voice, agents, _gate({})).run())
    await _until(lambda: voice.injected)
    caller.inbox.put_nowait(None)
    await running
    replayed = [f for f in caller.frames if f.get("type") == "canvas.item"]
    assert [f["item"]["tool_call_id"] for f in replayed] == ["a", "b"]
    [(note, mode)] = voice.injected
    assert mode is Inject.CONTEXT
    assert "screen shows: Scores -- India 2-0 up in NZ" in note
    assert "page back to: Fixtures" in note


def test_a_resumed_voice_is_told_what_was_said_most_recent_kept() -> None:
    from app.modules.calls.domain.voice_brief import voice_brief

    earlier = "Caller: hello\n" + "x" * 10_000 + "\nVoice: India won by 4 wickets"
    brief = voice_brief(teammate="Ria", pod="Personal", earlier=earlier)
    assert "This call is being resumed" in brief
    assert "India won by 4 wickets" in brief
    assert "Caller: hello" not in brief  # the oldest is what is cut
    assert "being resumed" not in voice_brief(teammate="Ria", pod="Personal")
