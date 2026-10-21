"""A live subscription's access is decided again when the epoch moves, on the
clock when it does not, and again after a decision that failed."""

from __future__ import annotations

import pytest

from app.core.live.access_clock import AccessClock
from app.core.live.config import LiveSettings

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _clock(recheck_seconds: float = 60.0) -> tuple[AccessClock, _Clock]:
    time = _Clock()
    access = AccessClock(
        LiveSettings(
            live_authz_check_seconds=2, live_authz_recheck_seconds=recheck_seconds
        ),
        clock=time,
    )
    access.start("e1")
    return access, time


def _epoch(value: str):
    async def now() -> str | None:
        return value

    return now


async def test_a_move_rechecks_and_is_then_taken_as_seen() -> None:
    access, _ = _clock()
    made: list[str] = []

    async def recheck() -> None:
        made.append("recheck")

    assert not await access.check(_epoch("e1"), recheck)
    assert await access.check(_epoch("e2"), recheck)
    assert not await access.check(_epoch("e2"), recheck)
    assert made == ["recheck"]


async def test_a_recheck_that_fails_is_made_again() -> None:
    access, _ = _clock()
    attempts: list[int] = []

    async def recheck() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("the database went away")

    with pytest.raises(OSError):
        await access.check(_epoch("e2"), recheck)
    assert access.epoch == "e1"
    assert await access.check(_epoch("e2"), recheck)
    assert access.epoch == "e2" and len(attempts) == 2


async def test_without_a_move_access_is_rechecked_on_the_clock() -> None:
    access, time = _clock(recheck_seconds=60)
    made: list[str] = []

    async def recheck() -> None:
        made.append("recheck")

    time.now += 59
    assert not await access.check(_epoch("e1"), recheck)
    time.now += 2
    assert await access.check(_epoch("e1"), recheck)
    # Redis away: no epoch at all is not a move, and the clock still runs.
    time.now += 61
    assert await access.check(lambda: _none(), recheck)
    assert made == ["recheck", "recheck"] and access.epoch == "e1"


async def _none() -> str | None:
    return None
