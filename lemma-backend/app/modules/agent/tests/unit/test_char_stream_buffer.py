"""Streamed text reaches the reader at the model's pace, not in fifty-character steps.

Every text, thinking and tool delta passes through `CharStreamBuffer` on its way
to the realtime channel. Batched by size alone, the opening words of a reply sat
in it until fifty characters had arrived -- most of a sentence at the pace a
model writes -- and a short reply appeared only when its part ended. These pin
the two bounds it keeps now: text waits for company at most about one flush
interval, and a stream fast enough to fill the size bound is still batched by it.
"""

from __future__ import annotations

import pytest

from app.modules.agent.infrastructure.harnesses.streaming import CharStreamBuffer

pytestmark = pytest.mark.unit

_MAX_WAIT = 0.05
#: About 65 tokens a second, in the twelve-character deltas a provider sends.
_ANSWER = "Sure. I checked the table and found three overdue invoices; here they are."
_DELTA_CHARS = 12
_DELTA_GAP = 0.045


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _buffer(clock: _Clock) -> CharStreamBuffer:
    return CharStreamBuffer(max_chars=50, max_wait=_MAX_WAIT, clock=clock)


def _deltas(text: str) -> list[str]:
    return [text[i : i + _DELTA_CHARS] for i in range(0, len(text), _DELTA_CHARS)]


def _time_in_buffer(deltas: list[str], gap: float) -> list[float]:
    """How long each delta's text waited, for deltas arriving ``gap`` apart.

    The part ends one gap after its last delta, which drains whatever is left.
    """
    clock = _Clock()
    buffer = _buffer(clock)
    arrivals: list[tuple[float, int]] = []
    sends: list[tuple[float, int]] = []
    appended = sent = 0

    def record(chunks: list[str]) -> None:
        nonlocal sent
        for chunk in chunks:
            sent += len(chunk)
            sends.append((clock.now, sent))

    for index, delta in enumerate(deltas):
        clock.now = index * gap
        appended += len(delta)
        arrivals.append((clock.now, appended))
        record(buffer.append(delta))
    clock.now += gap
    record(buffer.drain(force=True))
    return [
        next(at for at, total in sends if total >= needed) - arrived_at
        for arrived_at, needed in arrivals
    ]


def test_the_first_text_of_a_stream_goes_out_at_once() -> None:
    assert _buffer(_Clock()).append("Sure.") == ["Sure."]


def test_text_arriving_within_the_wait_is_held_for_more() -> None:
    clock = _Clock()
    buffer = _buffer(clock)
    buffer.append("Sure. ")

    clock.now = 0.02
    assert buffer.append("I checked ") == []
    clock.now = 0.04
    assert buffer.append("the table ") == []


def test_held_text_goes_out_with_the_first_delta_after_the_wait() -> None:
    clock = _Clock()
    buffer = _buffer(clock)
    buffer.append("Sure. ")
    clock.now = 0.03
    buffer.append("I checked ")

    clock.now = 0.06
    assert buffer.append("the table ") == ["I checked the table "]


def test_at_a_models_pace_no_text_waits_much_past_the_flush_interval() -> None:
    """The reader sees each delta within one interval and one delta's gap.

    Batched by size alone, the first words of this answer waited for the fifth
    delta -- four gaps, nearly a fifth of a second -- before anyone saw them.
    """
    waits = _time_in_buffer(_deltas(_ANSWER), _DELTA_GAP)

    assert max(waits) < _MAX_WAIT + _DELTA_GAP, waits


def test_a_stream_faster_than_the_wait_is_still_batched_by_size() -> None:
    """Every delta at the same instant: only the size bound can release them."""
    buffer = _buffer(_Clock())

    chunks = [chunk for _ in range(20) for chunk in buffer.append("abcde")]

    assert chunks == ["abcde", "abcde" * 10]
    assert buffer.drain(force=True) == ["abcde" * 9]


def test_a_forced_drain_releases_everything_held_as_one_chunk() -> None:
    clock = _Clock()
    buffer = _buffer(clock)
    buffer.append("Sure. ")
    buffer.append("I checked the table")

    assert buffer.drain(force=True) == ["I checked the table"]
    assert buffer.drain(force=True) == []


def test_nothing_is_lost_or_reordered_on_the_way_through() -> None:
    clock = _Clock()
    buffer = _buffer(clock)
    sent: list[str] = []
    for index, delta in enumerate(_deltas(_ANSWER * 3)):
        clock.now = index * 0.013
        sent.extend(buffer.append(delta))
    sent.extend(buffer.drain(force=True))

    assert "".join(sent) == _ANSWER * 3
