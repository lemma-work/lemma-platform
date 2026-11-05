"""How long a wait-for-a-child loop sleeps between checks.

Two paths wait on work another process is doing -- a sub-agent run and a JOB
function run -- and each tick opens a fresh unit of work. At a fixed interval a
single wait bounded at five minutes is hundreds of checkouts of a pool holding
ten connections plus ten overflow per process, and it grows with exactly the
delegation the sub-agent feature exists to enable: a handful of parents waiting
on children is a meaningful share of the pool asking "are you done yet".

Backing off keeps the opening seconds responsive -- a child that finishes fast
is the common case, and somebody is watching -- while a long wait settles to one
check every few seconds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

#: Longest pause between two checks.
POLL_BACKOFF_CAP_SECONDS = 5.0

#: Checks made at the caller's own interval before the pause starts growing.
_EAGER_ATTEMPTS = 5


def poll_delay(
    attempt: int,
    *,
    base_seconds: float,
    remaining_seconds: float,
) -> float:
    """Seconds to sleep before check number ``attempt`` (1-based).

    Never sleeps past ``remaining_seconds``: the deadline check happens before
    the sleep, so an unclamped pause would make a wait overrun its own timeout
    by as much as the cap.
    """
    if attempt <= _EAGER_ATTEMPTS:
        delay = base_seconds
    else:
        delay = min(
            POLL_BACKOFF_CAP_SECONDS,
            base_seconds * 2 ** (attempt - _EAGER_ATTEMPTS),
        )
    return max(0.0, min(delay, remaining_seconds))


async def poll_until[T](
    read: Callable[[], Awaitable[T | None]],
    *,
    wait_seconds: float,
    base_seconds: float = 1.0,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> T | None:
    """``read`` until it answers, or ``None`` once ``wait_seconds`` are up.

    Reads once even with no time to wait, so a caller asking "is it done yet"
    with nothing to spare still gets the answer if there is one.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(wait_seconds, 0.0)
    attempt = 0
    while True:
        answer = await read()
        if answer is not None:
            return answer
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        attempt += 1
        await sleep(
            poll_delay(attempt, base_seconds=base_seconds, remaining_seconds=remaining)
        )
