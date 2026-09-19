"""The browser is a cache. When memory runs out, it is the thing that goes.

A workspace sandbox is 1 vCPU and 2048 MB, and a headed Chrome is the only
thing in it that can eat all of that. Measured on a real workspace after a
research session: 63 Chrome processes at 2123 MB resident, `MemAvailable` at
14 MB, kswapd0 burning a third of the only vCPU. In that state every unrelated
tool call in the same sandbox degraded with it -- `python -c pass` took 61
seconds, `lemma --version` never returned, and the agent saw `exit_code: 124`
with no explanation.

So the rule is about *what* to end, not how much. Only the browser is ever
touched. The agent's own processes are its work -- a build, a test run, a
server it started -- and killing those to free memory would destroy something
unreproducible to save something that is a cache by construction. The browser
can always be started again, and the next `web_fetch` does exactly that.

**This used to do the ending itself, and it was never once ending anything.**
It scanned `/proc`, matched command lines against a list of patterns, and
sent SIGTERM then SIGKILL. The patterns were written when agent-browser
installed its own Chromium under `~/.agent-browser/browsers/` and the image
launched it through a `workspace-chrome` wrapper. The image uses Debian's
chromium now and a running process reports `/usr/lib/chromium/chromium`,
which carries none of those strings: counted on a real sandbox, 14 Chromium
processes and 13 matched by nothing in the list. So for as long as that has
been true this guard has been shedding the display and the daemon while
leaving every process that held the memory -- and nobody noticed, which is
the most useful thing anybody has learnt about what the machinery was worth.

What replaces it is one call. `agent-browser close --all` is the CLI that
owns the browser's lifecycle, and it is also the only stop that keeps a
login: on a real sandbox, one second after signing in, it keeps the session,
while SIGTERM to all eleven processes loses it and so does SIGTERM to the
browser process alone, even exiting cleanly in half a second.

The reason is not Chrome's own commit timer, which is what this said first.
`agent-browser` does not run Chrome on the profile it is configured with --
it launches on a throwaway `--user-data-dir=/tmp/agent-browser-chrome-<uuid>`
and copies the profile back **when it is closed cleanly, and only then**.
Measured directly: the durable `Cookies` file's mtime does not move while the
browser runs, moves on `close --all`, and the cookie is there after a reopen.

The difference matters. Under a commit timer, waiting long enough would make
a kill safe; under copy-on-close nothing ever does, so there is no version of
this that escalates to a signal after a timeout. That is also why `release`
and `quiesce` both close before they pause rather than after.

So the simpler version is also the correct one, and it names no process, so
it cannot go quietly out of date the way the pattern list did.

It can fail -- a sandbox with nothing left may not manage to spawn a Node
CLI -- and nothing here escalates to a signal afterwards. The honest reason
is that the escalation is what was there before and it did not work. A
sandbox that far gone is released and replaced, and the daemon retires the
browser by itself five minutes after anything stops driving it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import subprocess

from sandbox_runtime.sandbox_memory import (
    SandboxMemory,
    available_memory_mb,
    read_memory,
)

#: The fallback threshold, against `/proc/meminfo`'s `MemAvailable`, used
#: only where there is no cgroup to read. Chosen from measurement rather
#: than taste: a workspace at rest with no browser sits near 1485 MB
#: available of 1983 MB, and a browser session holding three rendered pages
#: still leaves about 1155 MB, while the degraded sandboxes observed in
#: production -- 14 MB, 19 MB, 21 MB -- are all far below this.
LOW_MEMORY_MB = 220

#: How much room must remain between what cannot be reclaimed and the hard
#: limit. 256 MB on a 2048 MB box, and the claim it makes is "the rest of
#: the sandbox can still work": the display stack measured ~100 MB, plus the
#: workspace runtime, the relay, the Node daemon and whatever shell the
#: agent is holding.
#:
#: Deliberately not the 1.2 GB ceiling on `anon + shmem` that the research
#: suggested. The same research measured twelve concurrent heavy tabs
#: sitting at `anon` 1188 MB with `memory.events` all zero and the sandbox
#: healthy -- a threshold that fires on a measured-healthy state would shed
#: the browser out from under working sessions. And no static floor can be
#: both quiet there and safe against the largest single page measured
#: (cnn.com, +963 MB in one capture): those two requirements are
#: arithmetically incompatible, which is the reason the first signal below
#: is a symptom rather than a prediction.
HEADROOM_FLOOR_MB = 256

#: `memory.pressure`'s `full avg10`: the share of a ten-second window in
#: which *every* task was stalled on memory. This is the only signal that
#: measures the harm the guard exists for -- `python -c pass` taking 61
#: seconds is by definition a memory stall.
#:
#: Two ticks, because one sample of a ten-second average is not a trend.
#: And optional, because it is not everywhere: measured absent on Docker
#: Desktop's kernel, which has no `memory.pressure` at all.
PRESSURE_FULL_AVG10 = 10.0

#: The CLI that owns the browser's lifecycle.
AGENT_BROWSER = "/usr/local/bin/agent-browser"

#: How long it gets. Long enough for a CLI to reach a busy daemon, short
#: enough that a starved sandbox's reaper tick is not held open by it.
CLOSE_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class Shed:
    """Why the browser was ended, so the log can say."""

    signal: str
    closed: bool
    headroom_mb: int | None = None
    anon_mb: int | None = None
    oom_kill: int | None = None
    available_mb: int | None = None


def _pressure_is_sustained(now: float | None, previous: float | None) -> bool:
    if now is None or previous is None:
        return False
    return now >= PRESSURE_FULL_AVG10 and previous >= PRESSURE_FULL_AVG10


def _reason(memory: SandboxMemory, *, last: _Last) -> str | None:
    """Which signal says the browser has to go, if any.

    Order is confidence. Never `memory.current` or `memory.peak`: reading
    3 GB of file data through the cgroup drove `peak` to exactly the limit
    with `oom_kill 0` and `anon` falling, and acting on that would shed a
    browser because somebody read a file.
    """
    if (
        memory.oom_kill is not None
        and last.oom_kill is not None
        and memory.oom_kill > last.oom_kill
    ):
        # Zero false positives by construction. A cgroup OOM kill takes the
        # largest resident task, which is essentially always a renderer --
        # so the first is the bulkhead working and this is the second.
        return "oom_kill"
    if _pressure_is_sustained(memory.pressure_full_avg10, last.pressure_full_avg10):
        return "memory_stall"
    headroom = memory.headroom_mb
    if headroom is not None and headroom <= HEADROOM_FLOOR_MB:
        return "headroom"
    if memory.limit_mb is None:
        # No cgroup to read. `MemAvailable` is the host's number on Docker
        # and the guest's on Firecracker, so this is honest on E2B and the
        # best available elsewhere.
        available = memory.available_mb
        if available is not None and available < LOW_MEMORY_MB:
            return "available"
    return None


async def shed_browser() -> bool:
    """Ask the daemon to close the browser. True if it says it did.

    On a thread, because the caller is the runtime's reaper loop and this
    spawns a Node CLI that can take seconds against a wedged browser.
    Blocking the loop there would freeze process-output handling and every
    other runtime request at exactly the moment the sandbox is struggling --
    which is the state this exists to get out of.

    False covers both "there was nothing to close" and "the CLI could not
    run", and the caller need not tell those apart: neither is something this
    module can do anything further about.
    """

    def _close() -> bool:
        try:
            done = subprocess.run(  # noqa: S603
                [AGENT_BROWSER, "close", "--all"],
                capture_output=True,
                timeout=CLOSE_TIMEOUT_SECONDS,
                check=False,
            )
        except OSError, subprocess.SubprocessError:
            return False
        return done.returncode == 0

    return await asyncio.to_thread(_close)


@dataclass
class _Last:
    """What the previous tick saw. Counters only mean something as a delta."""

    oom_kill: int | None = None
    pressure_full_avg10: float | None = None


#: Module-level because the reaper is a single loop in a single process, and
#: because seeding it from the first reading is the point: a resumed sandbox
#: whose `oom_kill` is already non-zero must not shed on its first tick for
#: a kill that happened before it woke up.
_last = _Last()


async def shed_browser_if_starved() -> Shed | None:
    """Close the browser when the *sandbox* is in trouble. None if not.

    Reads the cgroup rather than `/proc/meminfo`, which is not namespaced:
    measured inside a 2 GiB container, `MemTotal` reported 8.8 GiB and
    `nproc` 8, so the old threshold was comparing a host-wide figure against
    a per-sandbox number. On a roomy host it could never fire; on a busy one
    it would fire for something else's reasons.

    Returns which signal fired so the caller can say, because a sandbox that
    silently repaired itself leaves the next person reading these logs with
    the mystery this was built from.
    """
    memory = read_memory()
    reason = _reason(memory, last=_last)
    # Seeded from what was observed rather than reset to zero, on every tick
    # including the first.
    _last.oom_kill = memory.oom_kill
    _last.pressure_full_avg10 = memory.pressure_full_avg10
    if reason is None:
        return None
    return Shed(
        signal=reason,
        closed=await shed_browser(),
        headroom_mb=memory.headroom_mb,
        anon_mb=memory.anon_mb,
        oom_kill=memory.oom_kill,
        available_mb=memory.available_mb,
    )
