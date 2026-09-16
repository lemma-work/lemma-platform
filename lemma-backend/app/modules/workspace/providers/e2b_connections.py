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
        #: How many `get` calls are inside the lock for an id, so the lock is
        #: only discarded when nobody can still be holding it.
        self._waiting: dict[str, int] = {}
        #: Bumped by `forget`. A connect that started before the bump does not
        #: publish its result -- see `get`.
        self._epoch: dict[str, int] = {}

    def _sweep(self) -> None:
        """Drop every connection whose window has closed, not just this one's.

        `_fresh` cleans only the id it is asked about, which leaves an entry
        behind for every sandbox that is touched once and never again. This
        process serves every user, so that is unbounded. Cheap: the dictionary
        holds one entry per sandbox in recent use, and this runs on a call that
        is already about to talk to E2B.
        """
        cutoff = time.monotonic() - self._rearm_seconds
        for provider_id in [key for key, (at, _) in self._held.items() if at <= cutoff]:
            del self._held[provider_id]
            # An id nobody is connecting for and nothing is held for needs no
            # epoch either; one that is in flight keeps it, or the in-flight
            # connect would lose the invalidation it is being measured against.
            if provider_id not in self._waiting:
                self._epoch.pop(provider_id, None)

    def _fresh(self, provider_id: str) -> object | None:
        """The held connection if it is still within its window, else nothing.

        Expired entries are dropped as they are found rather than left to
        accumulate: this dictionary is keyed by sandbox id in a process that
        serves every user, so "never removed" is unbounded.
        """
        held = self._held.get(provider_id)
        if held is None:
            return None
        if time.monotonic() - held[0] >= self._rearm_seconds:
            del self._held[provider_id]
            return None
        return held[1]

    async def get(
        self, provider_id: str, connect: Callable[[], Awaitable[object]]
    ) -> object:
        """The held connection, or a new one.

        The lock matters more than it looks: the browser pane opens two sockets,
        and without it each pays the full resume of a paused sandbox in
        parallel.
        """
        self._sweep()
        fresh = self._fresh(provider_id)
        if fresh is not None:
            return fresh

        lock = self._opening.setdefault(provider_id, asyncio.Lock())
        self._waiting[provider_id] = self._waiting.get(provider_id, 0) + 1
        try:
            async with lock:
                fresh = self._fresh(provider_id)
                if fresh is not None:
                    return fresh
                epoch = self._epoch.get(provider_id, 0)
                sandbox = await connect()
                # Published only if nothing invalidated while we were
                # connecting. `release` pauses a sandbox and then forgets it; a
                # connect that began before the pause and finished after it
                # would otherwise restore a connection to a sandbox that is no
                # longer running -- which is the exact thing `forget` exists to
                # prevent. The caller still gets this sandbox, because it is
                # what they asked for and refusing it here would be a surprise;
                # the next caller connects afresh.
                if self._epoch.get(provider_id, 0) == epoch:
                    self._held[provider_id] = (time.monotonic(), sandbox)
                return sandbox
        finally:
            remaining = self._waiting[provider_id] - 1
            if remaining:
                self._waiting[provider_id] = remaining
            else:
                # Last one out. Safe to drop the lock only now: removing it
                # while another task held it would let a third create a second
                # lock and connect in parallel, which is the mutual exclusion
                # this is for.
                del self._waiting[provider_id]
                self._opening.pop(provider_id, None)
                if provider_id not in self._held:
                    self._epoch.pop(provider_id, None)

    def forget(self, provider_id: str) -> None:
        """Drop a connection to a sandbox that is no longer running.

        Called by the paths that end one. A held connection to a paused sandbox
        would make the next operation act as though it were up -- and connecting
        is what resumes it, so keeping the connection is precisely what stops
        the resume happening.

        The lock is deliberately left alone. A `get` may be inside it right now,
        and taking it away would let the next caller build a second lock and
        connect alongside. Bumping the epoch is what tells that in-flight
        connect its result is already stale.
        """
        self._held.pop(provider_id, None)
        self._epoch[provider_id] = self._epoch.get(provider_id, 0) + 1


__all__ = ["REARM_SECONDS", "SandboxConnections"]
