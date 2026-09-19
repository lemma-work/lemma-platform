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
from pathlib import Path
import subprocess

#: Below this, the sandbox is close enough to unusable that a browser is no
#: longer worth what it costs. Chosen from measurement rather than taste: a
#: workspace at rest with no browser sits near 1485 MB available of 1983 MB,
#: and a browser session holding three rendered pages still leaves about
#: 1155 MB. Both are an order of magnitude clear of this, while the degraded
#: sandboxes observed in production -- 14 MB, 19 MB, 21 MB available -- are
#: all far below it.
LOW_MEMORY_MB = 220

#: The CLI that owns the browser's lifecycle.
AGENT_BROWSER = "/usr/local/bin/agent-browser"

#: How long it gets. Long enough for a CLI to reach a busy daemon, short
#: enough that a starved sandbox's reaper tick is not held open by it.
CLOSE_TIMEOUT_SECONDS = 8.0


def available_memory_mb() -> int | None:
    """Free memory as the kernel reckons it, or None where that is unknowable.

    `MemAvailable` rather than `MemFree`: reclaimable page cache is not
    pressure, and treating it as pressure would shed the browser on a sandbox
    that had merely read a large file.
    """
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError, ValueError, IndexError:
        return None
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


async def shed_browser_if_starved(
    *, threshold_mb: int = LOW_MEMORY_MB
) -> tuple[int, bool] | None:
    """Close the browser when memory is short. None when nothing was due.

    Returns (available_mb, closed) so the caller can say what it did and why
    -- a sandbox that silently repaired itself would leave the next person
    reading these logs with the same mystery this was built from.
    """
    available = available_memory_mb()
    if available is None or available >= threshold_mb:
        return None
    return available, await shed_browser()
