"""Telling a held connection apart from a stalled process.

The connection-scope gate measures a hold in wall-clock, which is the right
unit for the thing it protects and the wrong unit for blame. A CI runner that
deschedules the whole process for half a second produces a half-second gap on
whichever connection happened to be open — and that is what it did: a path
whose real idle stretch is 5ms was reported at 649ms and failed the build.
"""

from __future__ import annotations

from app.core.observability.connection_scope import ConnectionHold
from app.modules.test_support.connection_scope import attributable_violations


def _hold(gap_seconds: float) -> ConnectionHold:
    return ConnectionHold(
        gap_seconds=gap_seconds,
        held_seconds=gap_seconds + 0.02,
        querying_seconds=0.02,
        statements=1,
        in_transaction=True,
        stack="  File 'somewhere.py', line 1, in handler",
    )


def test_a_hold_on_a_healthy_loop_is_still_reported() -> None:
    """The case the gate exists for: an await inside a session. It hands control
    back, so the loop stays punctual and the gap is entirely this path's."""
    blamed = attributable_violations(
        [_hold(0.3)], worst_lag_seconds=0.002, threshold=0.2
    )

    assert len(blamed) == 1


def test_a_gap_the_whole_process_lost_is_not_blamed_on_one_path() -> None:
    """649ms of gap on a runner that stalled for 640ms says nothing about the
    path that happened to hold the connection while it happened."""
    blamed = attributable_violations(
        [_hold(0.649)], worst_lag_seconds=0.64, threshold=0.2
    )

    assert blamed == []


def test_a_real_hold_survives_a_stall_that_only_partly_explains_it() -> None:
    """Subtracting the stall is not the same as excusing anything that happens
    near one. Two seconds of gap with 100ms of stall is still two seconds."""
    blamed = attributable_violations([_hold(2.0)], worst_lag_seconds=0.1, threshold=0.2)

    assert len(blamed) == 1


def test_each_hold_is_judged_on_its_own_gap() -> None:
    kept, dropped = _hold(1.0), _hold(0.25)

    blamed = attributable_violations(
        [kept, dropped], worst_lag_seconds=0.2, threshold=0.2
    )

    assert blamed == [kept]
