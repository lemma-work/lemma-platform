"""The cascade's turn: drafting early, barge-in, and what the talker is told it said.

The listener, speaker and talker are stand-ins scripted with the events Deepgram
and a model produce; what is under test is the turn logic between them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from app.modules.calls.domain.voice import (
    EngineClosed,
    Heard,
    Inject,
    Interrupted,
    Said,
    TurnDone,
    UserSpeaking,
    VoiceEvent,
)
from app.modules.calls.infrastructure.engines.cascade import CascadeEngine, _split
from app.modules.calls.infrastructure.engines.deepgram import TurnInfo
from app.modules.calls.infrastructure.engines.talker import Line

pytestmark = pytest.mark.unit


class ScriptedListener:
    def __init__(self) -> None:
        self.turns_in: asyncio.Queue[TurnInfo | None] = asyncio.Queue()

    async def open(self) -> None: ...

    async def send(self, pcm: bytes) -> None: ...

    async def turns(self) -> AsyncIterator[TurnInfo]:
        while (turn := await self.turns_in.get()) is not None:
            yield turn

    async def close(self) -> None:
        self.turns_in.put_nowait(None)


class RecordingSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.cleared = 0
        self.flushed = asyncio.Condition()
        self.flush_count = 0

    async def open(self) -> None: ...

    async def speak(self, text: str) -> None:
        self.spoken.append(text)

    async def flush(self) -> None:
        # Acknowledged from the socket's reader, as Aura's `Flushed` is.
        asyncio.get_running_loop().create_task(self._flushed())

    async def _flushed(self) -> None:
        async with self.flushed:
            self.flush_count += 1
            self.flushed.notify_all()

    async def clear(self) -> None:
        self.cleared += 1

    async def audio(self) -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""

    async def close(self) -> None: ...


class ScriptedTalker:
    """Answers each prompt with its script, pausing between deltas when asked."""

    def __init__(self, replies: dict[str, list[str]], *, pause: float = 0.0) -> None:
        self.replies = replies
        self.pause = pause
        self.prompts: list[str] = []
        self.histories: list[list[Line]] = []

    async def reply(self, history: Sequence[Line], prompt: str) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        self.histories.append(list(history))
        for delta in self.replies.get(prompt, ["Okay."]):
            if self.pause:
                await asyncio.sleep(self.pause)
            yield delta


async def _until(
    engine: CascadeEngine, kind: type[VoiceEvent], seen: list[VoiceEvent]
) -> None:
    async with asyncio.timeout(2):
        async for event in engine.events():
            seen.append(event)
            if isinstance(event, kind):
                return


def _engine(
    talker: ScriptedTalker,
) -> tuple[CascadeEngine, ScriptedListener, RecordingSpeaker]:
    listener, speaker = ScriptedListener(), RecordingSpeaker()
    engine = CascadeEngine(listener=listener, speaker=speaker, talker=talker)  # type: ignore[arg-type]
    return engine, listener, speaker


async def test_a_draft_started_early_is_spoken_once_the_turn_is_confirmed() -> None:
    talker = ScriptedTalker({"find a flight": ["Sure. ", "I'll pass that on."]})
    engine, listener, speaker = _engine(talker)
    await engine.open()
    listener.turns_in.put_nowait(TurnInfo("StartOfTurn", ""))
    listener.turns_in.put_nowait(TurnInfo("EagerEndOfTurn", "find a flight"))
    async with asyncio.timeout(2):
        while not talker.prompts:
            await asyncio.sleep(0)
    assert speaker.spoken == []  # drafting, not said
    listener.turns_in.put_nowait(TurnInfo("EndOfTurn", "Find a flight."))
    seen: list[VoiceEvent] = []
    await _until(engine, TurnDone, seen)
    assert talker.prompts == ["find a flight"]  # the draft was kept, not redone
    assert speaker.spoken == ["Sure.", "I'll pass that on."]
    assert Heard("Find a flight.", final=True) in seen
    assert Said("Sure. I'll pass that on.", final=True) in seen
    await engine.close()


async def test_a_resumed_turn_drops_the_draft_and_answers_the_whole_thing() -> None:
    talker = ScriptedTalker({}, pause=0.01)
    engine, listener, speaker = _engine(talker)
    await engine.open()
    listener.turns_in.put_nowait(TurnInfo("EagerEndOfTurn", "find a flight"))
    listener.turns_in.put_nowait(TurnInfo("TurnResumed", ""))
    listener.turns_in.put_nowait(
        TurnInfo("EndOfTurn", "find a flight to Delhi on Friday")
    )
    seen: list[VoiceEvent] = []
    await _until(engine, TurnDone, seen)
    assert talker.prompts[-1] == "find a flight to Delhi on Friday"
    assert speaker.spoken == ["Okay."]
    await engine.close()


async def test_barge_in_stops_the_reply_and_the_history_says_where_it_was_cut() -> None:
    talker = ScriptedTalker(
        {
            "tell me everything": ["First. ", "Second. ", "Third. ", "Fourth."],
            "stop": ["Sure."],
        },
        pause=0.05,
    )
    engine, listener, speaker = _engine(talker)
    await engine.open()
    listener.turns_in.put_nowait(TurnInfo("EndOfTurn", "tell me everything"))
    seen: list[VoiceEvent] = []
    async with asyncio.timeout(2):
        async for event in engine.events():
            seen.append(event)
            if isinstance(event, Said) and not event.final and "Second" in event.text:
                break
    listener.turns_in.put_nowait(TurnInfo("StartOfTurn", ""))
    await _until(engine, Interrupted, seen)
    assert speaker.cleared == 1
    assert isinstance(seen[-2], TurnDone)  # the cut-off reply ended first
    listener.turns_in.put_nowait(TurnInfo("EndOfTurn", "stop"))
    await _until(engine, TurnDone, seen)  # the answer to "stop"
    cut = talker.histories[-1]
    assert cut[0] == Line("caller", "tell me everything")
    assert cut[1].speaker == "voice" and cut[1].text.endswith("—")
    assert "Fourth" not in cut[1].text
    await engine.close()


async def test_a_team_note_to_speak_is_said_and_one_to_know_is_only_remembered() -> (
    None
):
    talker = ScriptedTalker({}, pause=0.0)
    engine, listener, speaker = _engine(talker)
    await engine.open()
    await engine.inject("[team] venues are on screen", Inject.CONTEXT)
    assert talker.prompts == []  # known, and nothing started to say it
    await engine.inject("[team] three venues found", Inject.SPEAK)
    seen: list[VoiceEvent] = []
    await _until(engine, TurnDone, seen)
    assert "three venues found" in talker.prompts[0]
    assert Line("note", "[team] venues are on screen") in talker.histories[0]
    assert speaker.spoken == ["Okay."]
    await engine.close()


async def test_a_barge_in_stops_the_reply_speaking_not_the_update_queued_behind_it() -> (
    None
):
    talker = ScriptedTalker(
        {"tell me everything": ["One. ", "Two. ", "Three. ", "Four. ", "Five."]},
        pause=0.05,
    )
    engine, listener, speaker = _engine(talker)
    await engine.open()
    listener.turns_in.put_nowait(TurnInfo("EndOfTurn", "tell me everything"))
    seen: list[VoiceEvent] = []
    async with asyncio.timeout(2):
        async for event in engine.events():
            seen.append(event)
            if isinstance(event, Said) and not event.final:
                break
    # A team update arrives while the reply is speaking, and waits behind it.
    await engine.inject("[team] found it", Inject.SPEAK)
    listener.turns_in.put_nowait(TurnInfo("StartOfTurn", ""))
    await _until(engine, Interrupted, seen)
    assert speaker.cleared == 1
    # The reply that was speaking is the one that ended, cut short -- not the
    # update waiting behind it, with the reply talking on over the caller.
    said = [e for e in seen if isinstance(e, Said) and e.final]
    assert len(said) == 1 and "Five." not in said[0].text
    assert engine._history[-1].speaker == "voice"
    assert engine._history[-1].text.endswith("—")
    await engine.close()


async def test_the_engine_closes_when_listening_ends() -> None:
    engine, listener, _ = _engine(ScriptedTalker({}))
    await engine.open()
    listener.turns_in.put_nowait(TurnInfo("StartOfTurn", ""))
    listener.turns_in.put_nowait(None)
    seen: list[VoiceEvent] = []
    await _until(engine, EngineClosed, seen)
    assert isinstance(seen[0], UserSpeaking)
    await engine.close()


def test_sentences_are_spoken_whole_and_long_clauses_are_not_held_back() -> None:
    assert _split("One. Two! Three is") == (["One.", "Two!"], "Three is")
    long = "a" * 150 + ", and then more"
    done, rest = _split(long)
    assert done == ["a" * 150 + ","] and rest == " and then more"


async def test_the_callers_words_go_out_as_fragments_and_the_final_turn_whole() -> None:
    # Flux sends the whole turn on every update; sent on as-is, a caption
    # that appends fragments showed the turn once per update.
    engine, listener, _ = _engine(ScriptedTalker({}))
    await engine.open()
    for event, text in [
        ("StartOfTurn", ""),
        ("Update", "Hello."),
        ("Update", "Hello. How are"),
        ("Update", "Hello. Who are"),  # a revision: nothing until the final
        ("EndOfTurn", "Hello. How are you?"),
    ]:
        listener.turns_in.put_nowait(TurnInfo(event, text))
    seen: list[VoiceEvent] = []
    await _until(engine, TurnDone, seen)
    heard = [(e.text, e.final) for e in seen if isinstance(e, Heard)]
    assert heard == [
        ("Hello.", False),
        (" How are", False),
        ("Hello. How are you?", True),
    ]
    await engine.close()


class FailingTalker(ScriptedTalker):
    async def reply(self, history: Sequence[Line], prompt: str) -> AsyncIterator[str]:
        raise OSError("model unreachable")
        yield ""


async def test_a_reply_that_fails_is_said_out_loud_not_left_as_silence() -> None:
    engine, listener, speaker = _engine(FailingTalker({}))
    await engine.open()
    listener.turns_in.put_nowait(TurnInfo("StartOfTurn", ""))
    listener.turns_in.put_nowait(TurnInfo("EndOfTurn", "find a flight"))
    seen: list[VoiceEvent] = []
    async with asyncio.timeout(2):
        async for event in engine.events():
            seen.append(event)
            if isinstance(event, Said) and event.final:
                break
    [said] = [e for e in seen if isinstance(e, Said)]
    assert "say it again" in said.text
    assert speaker.spoken == [said.text]
    await engine.close()


class FailsOnceTalker(ScriptedTalker):
    """Fails the first reply it is asked for, then answers."""

    async def reply(self, history: Sequence[Line], prompt: str) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            raise OSError("model unreachable")
        yield "Here you go."


async def test_a_draft_that_fails_is_not_apologised_for_and_the_turn_still_answers() -> (
    None
):
    talker = FailsOnceTalker({})
    engine, listener, speaker = _engine(talker)
    await engine.open()
    listener.turns_in.put_nowait(TurnInfo("StartOfTurn", ""))
    listener.turns_in.put_nowait(TurnInfo("EagerEndOfTurn", "find a flight"))
    # The draft has been asked for, has failed, and has been dropped.
    async with asyncio.timeout(2):
        while not talker.prompts or engine._reply is not None:
            await asyncio.sleep(0)
    listener.turns_in.put_nowait(TurnInfo("EndOfTurn", "find a flight"))
    seen: list[VoiceEvent] = []
    await _until(engine, TurnDone, seen)
    said = [e.text for e in seen if isinstance(e, Said) and e.final]
    assert said == ["Here you go."]
    assert talker.prompts == ["find a flight", "find a flight"]
    assert speaker.spoken == ["Here you go."]
    await engine.close()
