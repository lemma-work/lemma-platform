"""The floor decides when an update is spoken; PCM conversion keeps time."""

from __future__ import annotations

import pytest

from app.modules.calls.domain.floor import MAX_QUEUED, VOICE_SILENT_MS, Floor, Urgency
from app.modules.calls.domain.pcm import duration_ms, resample

pytestmark = pytest.mark.unit


def test_an_update_waits_for_both_sides_to_be_quiet() -> None:
    floor = Floor(quiet_window_ms=700)
    floor.queue("three venues found", Urgency.WHEN_IDLE)
    floor.voice_active(1000)
    assert floor.take(1500) is None  # the voice is talking
    floor.voice_finished(2000)
    assert floor.take(2500) is None  # not quiet long enough yet
    assert floor.take(2700) == "three venues found"
    assert floor.waiting == 0


def test_the_caller_is_never_talked_over_even_for_an_urgent_update() -> None:
    floor = Floor(quiet_window_ms=700)
    floor.queue("the booking failed", Urgency.NOW)
    floor.caller_started(1000)
    assert floor.take(5000) is None
    floor.caller_finished(5000)
    # Urgent: no quiet window, and the voice speaking does not hold it back.
    floor.voice_active(5000)
    assert floor.take(5001) == "the booking failed"


def test_waiting_updates_go_out_in_one_breath_and_the_oldest_fall_away() -> None:
    floor = Floor(quiet_window_ms=0)
    for index in range(MAX_QUEUED + 2):
        floor.queue(f"update {index}", Urgency.WHEN_IDLE)
    said = floor.take(10)
    assert said is not None
    assert "update 0" not in said and "update 1" not in said
    assert said.count("update") == MAX_QUEUED
    # Speaking holds the floor until the voice says it has finished.
    floor.queue("later", Urgency.WHEN_IDLE)
    assert floor.take(20) is None


def test_an_overflowing_floor_drops_an_update_that_can_wait_before_an_urgent_one() -> (
    None
):
    floor = Floor(quiet_window_ms=0)
    floor.queue("the booking failed", Urgency.NOW)
    for index in range(MAX_QUEUED):
        floor.queue(f"update {index}", Urgency.WHEN_IDLE)
    said = floor.take(10)
    assert said is not None
    assert "the booking failed" in said and "update 0" not in said


def test_resampling_keeps_the_duration() -> None:
    second_at_16k = bytes(16_000 * 2)
    converted = resample(second_at_16k, 16_000, 24_000)
    assert duration_ms(converted, 24_000) == pytest.approx(1000, abs=1)
    assert resample(second_at_16k, 16_000, 16_000) is second_at_16k
    assert resample(b"", 16_000, 24_000) == b""


def test_resampling_interpolates_between_samples() -> None:
    import array

    ramp = array.array("h", [0, 300, 600, 900]).tobytes()
    out = array.array("h")
    out.frombytes(resample(ramp, 2, 4))
    assert list(out)[:6] == [0, 150, 300, 450, 600, 750]


def test_a_voice_that_never_answered_does_not_hold_the_floor_forever() -> None:
    floor = Floor(quiet_window_ms=700)
    floor.queue("first", Urgency.WHEN_IDLE)
    assert floor.take(1000) == "first"  # the voice took it, and said nothing back
    floor.queue("second", Urgency.WHEN_IDLE)
    assert floor.take(2000) is None
    assert floor.take(1000 + VOICE_SILENT_MS) == "second"


def test_a_caller_turn_that_never_finished_does_not_hold_the_floor_forever() -> None:
    floor = Floor(quiet_window_ms=700)
    floor.caller_started(1000)
    floor.queue("update", Urgency.WHEN_IDLE)
    assert floor.take(5000) is None
    assert floor.take(1000 + VOICE_SILENT_MS) == "update"
