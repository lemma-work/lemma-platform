"""The backstop that holds when everything gentler has already failed.

None of these tests signal a real process. `shed_browser` matches things a
developer plausibly has running -- agent-browser, Xvfb -- so the kill is
injected wherever it is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox_runtime.paths import BROWSER_PROFILE
from sandbox_runtime.workspace import browser_guard
from sandbox_runtime.workspace.browser_guard import (
    LOW_MEMORY_MB,
    available_memory_mb,
    shed_browser_if_starved,
)


def _write_proc(monkeypatch, tmp_path: Path, processes: dict[int, str]) -> None:
    """A fake /proc holding exactly these command lines.

    `browser_process_ids` reads the real one, and a test that asserted on
    that would depend on whatever the developer running it has open.
    """
    for process_id, command in processes.items():
        entry = tmp_path / str(process_id)
        entry.mkdir()
        (entry / "cmdline").write_bytes(command.replace(" ", "\0").encode())

    real = browser_guard.Path

    def _path(argument):
        text = str(argument)
        if text == "/proc":
            return tmp_path
        if text.startswith("/proc/"):
            return tmp_path / text[len("/proc/") :]
        return real(argument)

    monkeypatch.setattr(browser_guard, "Path", _path)


def _meminfo(available_kb: int) -> str:
    return (
        "MemTotal:        2030612 kB\n"
        "MemFree:           64280 kB\n"
        f"MemAvailable:    {available_kb} kB\n"
    )


@pytest.fixture
def sandbox(monkeypatch, tmp_path: Path):
    """A fake /proc/meminfo and a browser that is counted, never killed."""

    state = {"available_kb": 1_520_000, "browser_pids": (), "killed": 0}
    meminfo = tmp_path / "meminfo"

    def _read() -> int | None:
        meminfo.write_text(_meminfo(state["available_kb"]))
        monkeypatch.setattr(browser_guard, "Path", Path)
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
        return None

    def _kill() -> int:
        state["killed"] = len(state["browser_pids"])
        return state["killed"]

    monkeypatch.setattr(browser_guard, "available_memory_mb", _read)
    monkeypatch.setattr(
        browser_guard, "browser_process_ids", lambda: state["browser_pids"]
    )
    monkeypatch.setattr(browser_guard, "shed_browser", _kill)
    return state


def test_a_healthy_sandbox_is_left_alone(sandbox) -> None:
    """A workspace at rest sits near 1485 MB available, and a browser holding
    three rendered pages still leaves about 1155 MB. Neither may trip this."""
    sandbox["browser_pids"] = (101, 102, 103)

    for available_mb in (1485, 1226, 1155):
        sandbox["available_kb"] = available_mb * 1024
        assert shed_browser_if_starved() is None, available_mb
    assert sandbox["killed"] == 0


def test_a_starved_sandbox_loses_its_browser(sandbox) -> None:
    """The states actually observed in production: 14, 19 and 21 MB free."""
    sandbox["browser_pids"] = tuple(range(200, 258))

    for available_mb in (14, 19, 21):
        sandbox["available_kb"] = available_mb * 1024
        outcome = shed_browser_if_starved()
        assert outcome is not None, available_mb
        assert outcome == (available_mb, 58)


def test_nothing_happens_when_the_memory_is_not_the_browsers(sandbox) -> None:
    """Starved with no browser running means something else is using it.

    The only thing this module knows how to kill safely is absent, and an
    agent's own build or test run is its work -- unreproducible, where a
    browser is a cache by construction.
    """
    sandbox["available_kb"] = 12 * 1024
    sandbox["browser_pids"] = ()

    assert shed_browser_if_starved() is None
    assert sandbox["killed"] == 0


def test_an_unreadable_meminfo_sheds_nothing(monkeypatch) -> None:
    """Not knowing the memory is not the same as knowing it is short."""
    monkeypatch.setattr(browser_guard, "available_memory_mb", lambda: None)
    monkeypatch.setattr(browser_guard, "browser_process_ids", lambda: (1, 2))
    killed = []
    monkeypatch.setattr(browser_guard, "shed_browser", lambda: killed.append(1) or 1)

    assert shed_browser_if_starved() is None
    assert killed == []


def test_the_threshold_clears_a_working_browser_by_an_order_of_magnitude() -> None:
    """Guards the constant itself: it was chosen from measurement.

    1155 MB is a real sandbox with three pages rendered; 21 MB is a real
    sandbox that had become unusable. The threshold has to sit far from the
    first and above the second, or it either fires on healthy research or
    never fires at all.
    """
    assert 21 < LOW_MEMORY_MB < 1155 / 4


def test_available_memory_reads_the_real_proc_when_there_is_one() -> None:
    """Linux only. Elsewhere the absence must be reported, not guessed."""
    value = available_memory_mb()
    if Path("/proc/meminfo").exists():
        assert value is not None and value > 0
    else:
        assert value is None


class _Signals:
    """A process table that records signals instead of sending them.

    `os.kill` is patched rather than real pids used, for the reason at the top
    of this file -- and because "signal 0" here has to mean "is it alive",
    which a real killed process would answer differently on each run.
    """

    def __init__(self, *, dies_on_term: bool) -> None:
        self.sent: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.alive = {101, 102, 103}
        self._dies_on_term = dies_on_term

    def kill(self, process_id: int, number: int) -> None:
        if process_id not in self.alive:
            raise ProcessLookupError(process_id)
        if number == 0:
            return
        self.sent.append((process_id, number))
        import signal as _signal

        if number == _signal.SIGTERM and self._dies_on_term:
            self.alive.discard(process_id)
        if number == _signal.SIGKILL:
            self.alive.discard(process_id)


@pytest.fixture
def signals(monkeypatch):
    def _install(*, dies_on_term: bool) -> _Signals:
        table = _Signals(dies_on_term=dies_on_term)
        # No real CLI in a unit test. What the close *achieves* is measured
        # on a sandbox, not here; what matters here is that it is attempted
        # before anything is signalled.
        monkeypatch.setattr(
            browser_guard, "_close_through_the_daemon", lambda: table.closed.append(1)
        )
        monkeypatch.setattr(browser_guard.os, "kill", table.kill)
        monkeypatch.setattr(
            browser_guard, "browser_process_ids", lambda: tuple(sorted(table.alive))
        )
        monkeypatch.setattr(browser_guard.time, "sleep", lambda _: None)
        return table

    return _install


def test_the_browser_is_asked_before_it_is_killed(signals) -> None:
    """Measured, not assumed, and the measurement corrected an earlier guess.

    Chrome batches cookies to disk on a 30 second timer, so a kill seconds
    after somebody signs in loses the session. A *signal* does not flush the
    queue -- not SIGTERM to all eleven processes and not SIGTERM to the
    browser process alone, even exiting cleanly in half a second. Only the
    daemon's own close does. So the close comes first and the signals are
    the fallback for when it cannot run."""
    import signal as _signal

    table = signals(dies_on_term=True)

    assert browser_guard.shed_browser() == 3

    assert table.closed, "the daemon's close is the only step that flushes"

    assert {number for _, number in table.sent} == {_signal.SIGTERM}
    assert not table.alive


def test_a_browser_that_will_not_go_is_killed_anyway(signals) -> None:
    """The old reasoning still holds for the case it was written about: this
    usually runs in a sandbox with no memory left to run a teardown in, and a
    shutdown that hangs there is worse than the cookie it would have saved.
    So the ask is bounded and the SIGKILL still arrives."""
    import signal as _signal

    table = signals(dies_on_term=False)

    assert browser_guard.shed_browser(graceful_seconds=0.05) == 3

    assert (101, _signal.SIGTERM) in table.sent
    assert (101, _signal.SIGKILL) in table.sent
    assert not table.alive


def test_the_browser_this_sandbox_actually_runs_is_matched(
    monkeypatch, tmp_path
) -> None:
    """The guard has to match Chromium as this image launches it.

    Counted on a real sandbox: 14 Chromium processes, 13 matched by nothing
    in the pattern list. `agent-browser`, `workspace-chrome` and
    `.agent-browser/browsers/` were written when agent-browser installed its
    own Chromium and the image launched it through a wrapper; the image uses
    Debian's chromium now, and a launched process reports
    `/usr/lib/chromium/chromium`, which carries none of them. So this guard
    was shedding the display and the daemon and leaving every process that
    held the memory -- which is the one thing it exists to do.
    """
    launched = (
        "/usr/lib/chromium/chromium --type=renderer --crashpad-handler-pid=690 "
        f"--user-data-dir={BROWSER_PROFILE} --enable-crash-reporter"
    )
    _write_proc(monkeypatch, tmp_path, {4242: launched})

    assert browser_guard.browser_process_ids() == (4242,)


def test_an_agents_own_headless_chromium_is_left_alone(monkeypatch, tmp_path) -> None:
    """Matching on the profile rather than on "chrome" is what keeps this
    true, and it is why the profile path is the right pattern: an agent
    building a frame-capture harness runs its own `chromium --headless=new`,
    which takes Chromium's default user-data-dir. That is the agent's work,
    not this sandbox's browser, and the module's whole rule is that only the
    browser is ever touched."""
    theirs = (
        "/usr/lib/chromium/chromium --headless=new --no-sandbox "
        "--remote-debugging-port=9333 --window-size=1920,1080"
    )
    _write_proc(monkeypatch, tmp_path, {4243: theirs})

    assert browser_guard.browser_process_ids() == ()
