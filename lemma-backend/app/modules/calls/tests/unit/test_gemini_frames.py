"""Gemini Live's server frames, as the call's events.

Frame shapes are the ones a live 3.8 session sends: input transcription arrives
in pieces and keeps arriving after the voice starts answering, and the voice
answering is the only end-of-turn the caller's side ever gets.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from app.modules.calls.domain.voice import (
    Inject,
    AudioOut,
    Heard,
    Interrupted,
    Said,
    TurnDone,
    UserSpeaking,
    VoiceEvent,
)
from app.modules.calls.infrastructure.engines.google_auth import api_key_headers
from app.modules.calls.infrastructure.engines.gemini_live import (
    CALLER_TURN_GRACE_SECONDS,
    GeminiLiveEngine,
)

pytestmark = pytest.mark.unit


def _engine() -> GeminiLiveEngine:
    return GeminiLiveEngine(
        headers=api_key_headers("test"),
        model="gemini-3.8-live",
        instructions="be brief",
    )


def _drain(engine: GeminiLiveEngine) -> list[VoiceEvent]:
    events: list[VoiceEvent] = []
    while not engine._events.empty():
        events.append(engine._events.get_nowait())
    return events


def _audio(pcm: bytes) -> dict[str, object]:
    return {
        "modelTurn": {
            "parts": [
                {
                    "inlineData": {
                        "mimeType": "audio/pcm;rate=24000",
                        "data": base64.b64encode(pcm).decode(),
                    }
                }
            ]
        }
    }


async def test_a_turn_heard_answered_and_finished() -> None:
    engine = _engine()
    engine._handle_frame(
        {"serverContent": {"inputTranscription": {"text": "Find me "}}}
    )
    engine._handle_frame(
        {"serverContent": {"inputTranscription": {"text": "a flight."}}}
    )
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    engine._handle_frame({"serverContent": {"outputTranscription": {"text": "Sure."}}})
    engine._handle_frame({"serverContent": {"turnComplete": True}})
    assert _drain(engine) == [
        UserSpeaking(),
        Heard("Find me ", final=False),
        Heard("a flight.", final=False),
        AudioOut(b"\x01\x00" * 4, 24_000),
        Said("Sure.", final=False),
        Heard("Find me a flight.", final=True),
        Said("Sure.", final=True),
        TurnDone(),
    ]


async def test_words_heard_after_the_voice_begins_stay_in_the_callers_turn() -> None:
    engine = _engine()
    engine._handle_frame(
        {"serverContent": {"inputTranscription": {"text": "Book the "}}}
    )
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    # The transcript runs behind the audio: the last words land late.
    engine._handle_frame(
        {"serverContent": {"inputTranscription": {"text": "cheapest one."}}}
    )
    finals = [e for e in _drain(engine) if isinstance(e, Heard) and e.final]
    assert finals == []
    finals: list[VoiceEvent] = []
    async with asyncio.timeout(CALLER_TURN_GRACE_SECONDS + 1):
        while not finals:
            await asyncio.sleep(0.01)
            finals = [e for e in _drain(engine) if isinstance(e, Heard) and e.final]
    assert finals == [Heard("Book the cheapest one.", final=True)]


async def test_a_barge_in_ends_the_voices_turn_where_it_was() -> None:
    engine = _engine()
    engine._handle_frame(
        {"serverContent": {"outputTranscription": {"text": "The first venue is"}}}
    )
    engine._handle_frame({"serverContent": {"interrupted": True}})
    events = _drain(engine)
    assert events[-3:] == [
        Interrupted(),
        Said("The first venue is", final=True),
        TurnDone(),
    ]


def test_resumption_handles_are_kept_and_go_away_rotates_the_connection() -> None:
    engine = _engine()
    engine._handle_frame(
        {"sessionResumptionUpdate": {"newHandle": "h-1", "resumable": True}}
    )
    engine._handle_frame(
        {"sessionResumptionUpdate": {"newHandle": "h-2", "resumable": False}}
    )
    assert engine._handle == "h-1"
    setup = engine._setup()["setup"]
    assert isinstance(setup, dict) and setup["sessionResumption"] == {"handle": "h-1"}
    assert setup["contextWindowCompression"] == {"slidingWindow": {}}


async def test_text_that_is_not_pcm_audio_is_not_played() -> None:
    engine = _engine()
    engine._handle_frame(
        {
            "serverContent": {
                "modelTurn": {
                    "parts": [
                        {"text": "thinking"},
                        {"inlineData": {"mimeType": "image/png", "data": ""}},
                    ]
                }
            }
        }
    )
    assert not any(isinstance(event, AudioOut) for event in _drain(engine))


class _Socket:
    """Records what the engine sends."""

    class state:  # noqa: N801 - mirrors websockets' ``State`` attribute shape
        name = "OPEN"

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(self, raw: str | bytes) -> None:
        import json

        self.sent.append(json.loads(raw))


async def _settle(engine: GeminiLiveEngine) -> None:
    """Until no queued text is still being sent: a condition, not a clock."""
    from app.modules.test_support.e2e.waiters import eventually

    async def idle() -> bool:
        return not engine._sending

    await eventually(
        label="queued text sent",
        probe=idle,
        done=bool,
        timeout_seconds=2,
        interval_seconds=0.01,
    )


async def test_text_sent_while_the_voice_talks_waits_for_its_turn_to_end() -> None:
    """Gemini cuts its own sentence for any input; a team note mid-sentence
    must not. It is held, and goes once the turn completes, in order."""
    engine = _engine()
    socket = _Socket()
    engine._socket = socket  # type: ignore[assignment]
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})

    await engine.inject("India 143 for 1.", Inject.CONTEXT)
    await engine.inject("Tell them the score.", Inject.SPEAK)
    await _settle(engine)
    assert socket.sent == []

    engine._handle_frame({"serverContent": {"turnComplete": True}})
    await _settle(engine)
    assert socket.sent == [
        {
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": "India 143 for 1."}]}],
                "turnComplete": False,
            }
        },
        {"realtimeInput": {"text": "Tell them the score."}},
    ]


async def test_held_text_outlasts_a_barge_in_until_the_voice_answers() -> None:
    engine = _engine()
    socket = _Socket()
    engine._socket = socket  # type: ignore[assignment]
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    await engine.inject("first", Inject.SPEAK)
    engine._handle_frame({"serverContent": {"interrupted": True}})
    # Gemini closes the cut-off turn too; that is not the answer's end.
    engine._handle_frame({"serverContent": {"turnComplete": True}})
    # The caller has the floor; a later note queues behind the held one.
    await engine.inject("second", Inject.CONTEXT)
    await _settle(engine)
    assert socket.sent == []

    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    engine._handle_frame({"serverContent": {"turnComplete": True}})
    await _settle(engine)
    assert [next(iter(frame)) for frame in socket.sent] == [
        "realtimeInput",
        "clientContent",
    ]


async def test_text_goes_at_once_while_the_voice_is_quiet() -> None:
    engine = _engine()
    socket = _Socket()
    engine._socket = socket  # type: ignore[assignment]
    await engine.inject("now", Inject.SPEAK)
    assert socket.sent == [{"realtimeInput": {"text": "now"}}]


async def test_a_turn_that_never_completes_releases_held_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.calls.infrastructure.engines import gemini_live

    monkeypatch.setattr(gemini_live, "MAX_TURN_HOLD_SECONDS", 0.01)
    engine = _engine()
    socket = _Socket()
    engine._socket = socket  # type: ignore[assignment]
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    await engine.inject("late", Inject.SPEAK)
    async with asyncio.timeout(1):
        while not socket.sent:
            await asyncio.sleep(0.01)
    assert socket.sent == [{"realtimeInput": {"text": "late"}}]


async def test_a_note_after_a_barge_in_waits_for_the_answer_even_with_nothing_held() -> (
    None
):
    engine = _engine()
    socket = _Socket()
    engine._socket = socket  # type: ignore[assignment]
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    engine._handle_frame({"serverContent": {"interrupted": True}})
    engine._handle_frame({"serverContent": {"turnComplete": True}})
    await engine.inject("news", Inject.SPEAK)
    await _settle(engine)
    assert socket.sent == []
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    engine._handle_frame({"serverContent": {"turnComplete": True}})
    await _settle(engine)
    assert socket.sent == [{"realtimeInput": {"text": "news"}}]


async def test_a_note_injected_as_held_ones_are_released_goes_after_them() -> None:
    engine = _engine()
    socket = _Socket()
    engine._socket = socket  # type: ignore[assignment]
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    await engine.inject("A", Inject.SPEAK)
    engine._handle_frame({"serverContent": {"turnComplete": True}})
    # Before the release task has run.
    await engine.inject("B", Inject.SPEAK)
    await _settle(engine)
    assert socket.sent == [
        {"realtimeInput": {"text": "A"}},
        {"realtimeInput": {"text": "B"}},
    ]


async def test_text_left_when_the_voice_starts_again_keeps_its_deadline() -> None:
    engine = _engine()
    socket = _Socket()
    engine._socket = socket  # type: ignore[assignment]
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    await engine.inject("A", Inject.SPEAK)
    # The turn ends and a release is scheduled; the voice starts again first.
    engine._handle_frame({"serverContent": {"turnComplete": True}})
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    await _settle(engine)
    assert socket.sent == []
    # The deadline is armed again for what is left...
    assert engine._turn_hold is not None
    engine._turn_hold.cancel()
    # ...and when it comes, the text goes.
    engine._hold_expired()
    await _settle(engine)
    assert socket.sent == [{"realtimeInput": {"text": "A"}}]
