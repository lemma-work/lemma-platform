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

# The bounds, named for what is being waited on rather than written as a number
# at each call site. Thirty-two call sites carried eight different literals, and
# the docstring above already claimed the bound "lives in one place" — it did
# not, so when a shared deployment was slow there was no one number to move and
# a scenario failed on whichever literal happened to be tightest that day.
#
# Each is set from the slowest *healthy* observation of that kind of work, not
# from what usually happens. Being generous costs a green run nothing: a
# deadline is only ever reached when something has already gone wrong, so the
# price of a larger bound is paid solely by a run that was going to fail anyway.
# The assertion is unchanged either way — only the patience is.

#: A write becoming readable: a cache invalidating, a role taking effect, a row
#: appearing to a watcher. Fast, and slow only when something is wrong.
UNTIL_A_CHANGE_IS_VISIBLE = 30.0

#: An agent run reaching a terminal state, once it has started. Covers the queue
#: as well as the model, because a scenario cannot see the difference.
UNTIL_A_RUN_SETTLES = 120.0

#: A real model producing a reply, or choosing to call a tool. The longest
#: bound, and the one worth being most generous with: it is the only kind of
#: wait whose length is decided by something outside this system, and a run that
#: is merely queued behind another looks exactly like one that will never
#: answer.
UNTIL_A_MODEL_ACTS = 180.0

#: Work the product does on its own schedule — a trigger firing, a document
#: converting, a bundle exporting, a failing schedule giving up.
UNTIL_BACKGROUND_WORK_LANDS = 180.0


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
                f"{describe} was not supposed to happen, but it did.\n  saw: {value!r}"
            )
        await asyncio.sleep(interval)
