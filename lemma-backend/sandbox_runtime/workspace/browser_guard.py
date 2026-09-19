"""The browser is a cache. When memory runs out, it is the thing that goes.

A workspace sandbox is 1 vCPU and 2048 MB, and a headed Chrome is the only
thing in it that can eat all of that. Measured on a real workspace after a
research session: 63 Chrome processes at 2123 MB resident, `MemAvailable` at
14 MB, kswapd0 burning a third of the only vCPU. In that state every unrelated
tool call in the same sandbox degraded with it -- `python -c pass` took 61
seconds, `lemma --version` never returned, and the agent saw `exit_code: 124`
with no explanation.

Three things already try to stop it getting there: a capture closes its own
tab, the agent-browser daemon retires itself after five idle minutes, and
`release` sheds the browser before a pause can snapshot it. This is the one
that holds when those cannot. Each of them needs a healthy process to act --
the daemon has to be responsive enough to notice its own idle timer, and
`release` has to be reached at all -- and the failure being defended against is
precisely the one where memory is gone and nothing is responsive. Worse, it
does not settle: sampled three times inside a single command, with nothing
driving the browser, renderer count and resident size still climbed while
available memory fell 32 -> 24 -> 21 MB. A sandbox that reaches this state does
not come back on its own.

So the rule here is deliberately blunt, and it is a rule about *what* to kill
rather than how much. Only the browser is ever touched. The agent's own
processes are its work -- a build, a test run, a server it started -- and
killing those to free memory would destroy something unreproducible to save
something that is a cache by construction. The browser can always be started
again, and the next `web_fetch` does exactly that.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import time

# Below this, the sandbox is close enough to unusable that a browser is no
# longer worth what it costs. Chosen from measurement rather than taste: a
# workspace at rest with no browser sits near 1485 MB available of 1983 MB, and
# a browser session holding three rendered pages still leaves about 1155 MB.
# Both are an order of magnitude clear of this, so a healthy research session
# never trips it, while the degraded sandboxes observed in production -- 14 MB,
# 19 MB, 21 MB available -- are all far below it.
LOW_MEMORY_MB = 220

# What the browser is, as processes. `workspace-chrome` is the wrapper the
# image installs; the renderers exec the real binary out of the agent-browser
# profile directory, so they are matched on that path rather than on "chrome",
# which would also catch an agent's own chromium script.
_BROWSER_PATTERNS = (
    "agent-browser",
    "workspace-chrome",
    ".agent-browser/browsers/",
    "Xvfb",
)


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


def browser_process_ids() -> tuple[int, ...]:
    """Every pid whose command line says it belongs to the browser."""
    found: list[int] = []
    self_id = os.getpid()
    try:
        entries = sorted(
            int(path.name) for path in Path("/proc").iterdir() if path.name.isdigit()
        )
    except OSError:
        return ()
    for process_id in entries:
        if process_id == self_id:
            continue
        try:
            # NUL-separated argv, so a pattern cannot match across arguments.
            command = (
                Path(f"/proc/{process_id}/cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", "replace")
            )
        except OSError:
            # Exited between the listing and the read.
            continue
        if any(pattern in command for pattern in _BROWSER_PATTERNS):
            found.append(process_id)
    return tuple(found)


#: How long Chrome gets to shut itself down before it is killed.
#:
#: Short, because this runs in a sandbox that is already starved and the old
#: argument against asking Chrome to do anything at all still stands: teardown
#: in a machine with no memory to run it in is how a shutdown becomes a hang.
#: A deadline answers that without giving up the flush -- the worst case is
#: three seconds more in a state that is already bad, and the SIGKILL still
#: arrives.
_GRACEFUL_SECONDS = 3.0

#: How often to look while waiting. Cheap: one `kill(pid, 0)` per process.
_POLL_SECONDS = 0.1


def _signal(process_ids: tuple[int, ...], number: int) -> int:
    """Send one signal to each pid, ignoring the ones already gone."""
    sent = 0
    for process_id in process_ids:
        try:
            os.kill(process_id, number)
        except ProcessLookupError, PermissionError:
            continue
        sent += 1
    return sent


def _still_alive(process_ids: tuple[int, ...]) -> tuple[int, ...]:
    """Which of these are still running. Signal 0 checks without sending."""
    alive: list[int] = []
    for process_id in process_ids:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError, PermissionError:
            continue
        alive.append(process_id)
    return tuple(alive)


def shed_browser(*, graceful_seconds: float = _GRACEFUL_SECONDS) -> int:
    """End the browser. Returns how many processes were signalled.

    SIGTERM first, then SIGKILL for whatever is left after
    `graceful_seconds`. This used to be SIGKILL alone, on the reasoning that
    there was nothing to flush -- true when the profile lived in `/tmp` and
    was scratch, and false now that it is the durable store a person's logins
    are kept in.

    The difference was measured rather than argued, on a real E2B sandbox:
    Chrome's cookie store batches to disk on a 30 second timer, so a SIGKILL
    two seconds after signing in to a site lost the session outright, while
    the same kill forty seconds later kept it. A graceful exit commits the
    store, which is the whole reason to ask before insisting.

    Bounded, because the caller is usually the low-memory guard and a
    shutdown that hangs there would be worse than the cookie it saved.
    """
    process_ids = browser_process_ids()
    signalled = _signal(process_ids, signal.SIGTERM)
    deadline = time.monotonic() + graceful_seconds
    while time.monotonic() < deadline:
        remaining = _still_alive(process_ids)
        if not remaining:
            return signalled
        time.sleep(_POLL_SECONDS)
    _signal(_still_alive(process_ids), signal.SIGKILL)
    return signalled


def shed_browser_if_starved(
    *, threshold_mb: int = LOW_MEMORY_MB
) -> tuple[int, int] | None:
    """Shed the browser when memory is short. None when nothing was due.

    Returns (available_mb, processes_killed) so the caller can say what it did
    and why -- a sandbox that silently repaired itself would leave the next
    person reading these logs with the same mystery this was built from.
    """
    available = available_memory_mb()
    if available is None or available >= threshold_mb:
        return None
    if not browser_process_ids():
        # Something else is using the memory. Not this module's call to make:
        # the only safe thing it knows how to kill is absent.
        return None
    return available, shed_browser()
