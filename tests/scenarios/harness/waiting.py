"""Waiting on a condition, never on the clock.

Scenarios are forbidden from sleeping — `journeys/test_harness_contract.py`
fails the run if one does. This is where the waiting is allowed to happen, for
two reasons:

* A failure here says what it was waiting for and what it last saw. A bare
  `sleep(2)` followed by an assertion says only that the assertion failed, and
  leaves you guessing whether the system is broken or merely slow.
* The bound lives in one place. When CI is loaded and everything is three times
  slower, one number moves.

Real work in Lemma is asynchronous in ways a person genuinely experiences —
a cache invalidation that lands just after the response, a document that
converts in the background, a workflow that resumes when a function finishes.
Polling is how a client observes those, so it is how a scenario does too.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_TIMEOUT = 20.0
DEFAULT_INTERVAL = 0.1


class NeverHappened(AssertionError):
    """A condition did not hold within its bound."""


async def eventually(
    probe: Callable[[], Awaitable[T]],
    until: Callable[[T], bool],
    *,
    describe: str,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
) -> T:
    """Poll ``probe`` until ``until`` holds, or fail saying what it last saw.

    ``describe`` completes the sentence "waited for …", so phrase it as the
    thing being waited on: ``"bob's permissions to reflect his new role"``.
    """
    deadline = time.monotonic() + timeout
    attempts = 0
    last: T | None = None
    while True:
        last = await probe()
        attempts += 1
        if until(last):
            return last
        if time.monotonic() >= deadline:
            raise NeverHappened(
                f"waited for {describe}, and it never happened.\n"
                f"  gave up after {timeout:.0f}s and {attempts} checks\n"
                f"  last saw: {last!r}"
            )
        await asyncio.sleep(interval)


async def never(
    probe: Callable[[], Awaitable[T]],
    becomes: Callable[[T], bool],
    *,
    describe: str,
    within: float = 2.0,
    interval: float = DEFAULT_INTERVAL,
) -> None:
    """Assert something does *not* happen within ``within``.

    The mirror of :func:`eventually`, and the honest way to test a negative:
    "the removed member never regains access" needs a window, not a single
    check that could simply have run too early.
    """
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        value = await probe()
        if becomes(value):
            raise AssertionError(
                f"{describe} was not supposed to happen, but it did.\n"
                f"  saw: {value!r}"
            )
        await asyncio.sleep(interval)
