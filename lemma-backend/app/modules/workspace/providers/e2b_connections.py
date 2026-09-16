"""Holding one connection to a sandbox, rather than opening one per call.

Its own module because `e2b.py` is at the size ceiling and because this is a
policy, not a detail of any one operation: how long a connection is reused, and
what ends that.

Connecting to an E2B sandbox is a request over the internet *and* the thing that
re-arms the sandbox's lease, so it cannot simply be cached forever -- a workspace
whose lease runs out loses every process at once. It also cannot be paid per
operation: opening the browser pane on a paused sandbox was measured making
thirty requests to E2B, nineteen of them connects, to the same sandbox, inside
four seconds. The person reads that as "the sandbox is slow"; it is the platform
talking to itself.

The timeout passed on each connect is not optional either. The SDK's rule is
that "the timeout will update only if the new timeout is longer than the
existing one", so passing nothing does not mean "leave it alone" -- it means
five minutes, the SDK's default. A workspace resumed after days came back with a
five-minute lease and every process in it died together when that elapsed, which
a caller sees as three tool calls returning 502 at the same instant, several
minutes into a turn that was working. Holding the connection is what moves that
renewal onto a clock instead of making it a side effect of however many
operations a request happens to perform.

So: held for a short window, re-made on a clock, and dropped the moment this
process does something that makes it meaningless -- pausing or killing the
sandbox. Both of those are connection-ending events the caller knows about, and
`forget` is how they say so.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

#: How long a connection is reused before it is re-made.
#:
#: Short against the lease it renews (`sandbox_timeout_seconds`, 30 minutes by
#: default) and long against a burst of operations, which is the point: the
#: burst is what was costing nineteen round trips.
REARM_SECONDS = 30.0


class SandboxConnections:
    """One live connection per sandbox id, with the clock that re-arms it."""

    def __init__(self, *, rearm_seconds: float = REARM_SECONDS) -> None:
        self._rearm_seconds = rearm_seconds
        self._held: dict[str, tuple[float, object]] = {}
        self._opening: dict[str, asyncio.Lock] = {}

    async def get(
        self, provider_id: str, connect: Callable[[], Awaitable[object]]
    ) -> object:
        """The held connection, or a new one.

        The lock matters more than it looks: the browser pane opens two sockets,
        and without it each pays the full resume of a paused sandbox in
        parallel.
        """
        held = self._held.get(provider_id)
        if held is not None and time.monotonic() - held[0] < self._rearm_seconds:
            return held[1]

        lock = self._opening.setdefault(provider_id, asyncio.Lock())
        async with lock:
            held = self._held.get(provider_id)
            if held is not None and time.monotonic() - held[0] < self._rearm_seconds:
                return held[1]
            sandbox = await connect()
            self._held[provider_id] = (time.monotonic(), sandbox)
            return sandbox

    def forget(self, provider_id: str) -> None:
        """Drop a connection to a sandbox that is no longer running.

        Called by the paths that end one. A held connection to a paused sandbox
        would make the next operation act as though it were up -- and connecting
        is what resumes it, so keeping the connection is precisely what stops
        the resume happening.
        """
        self._held.pop(provider_id, None)
        self._opening.pop(provider_id, None)


__all__ = ["REARM_SECONDS", "SandboxConnections"]
