"""The backstop that holds when everything gentler has already failed.

Nothing here signals a real process, because nothing in the subject does any
more. This guard used to scan `/proc`, match command lines against a list of
patterns and send SIGTERM then SIGKILL -- and the patterns had gone stale, so
on a real sandbox it matched 1 of 14 Chromium processes and had been shedding
the display and the daemon while leaving everything that held the memory.

It asks the daemon to close the browser now. That is measured to be the only
stop which also commits Chrome's cookie store, so the simpler version is the
correct one; and it names no process, so it cannot go quietly out of date the
way the pattern list did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

from sandbox_runtime.workspace import browser_guard
from sandbox_runtime.workspace.browser_guard import (
    LOW_MEMORY_MB,
    available_memory_mb,
    shed_browser_if_starved,
)


def _meminfo(available_kb: int) -> str:
    return (
        "MemTotal:        2030612 kB\n"
        "MemFree:           64280 kB\n"
        f"MemAvailable:    {available_kb} kB\n"
    )


@pytest.fixture
def sandbox(monkeypatch, tmp_path: Path):
    """A fake `/proc/meminfo`, and a close that is counted, never run."""

    state = {"available_kb": 1_520_000, "closes": 0, "closed": True}
    meminfo = tmp_path / "meminfo"

    def _read() -> int | None:
        meminfo.write_text(_meminfo(state["available_kb"]))
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
        return None

    async def _close() -> bool:
        state["closes"] += 1
        return bool(state["closed"])

    monkeypatch.setattr(browser_guard, "available_memory_mb", _read)
    monkeypatch.setattr(browser_guard, "shed_browser", _close)
    return state


async def test_a_healthy_sandbox_is_left_alone(sandbox) -> None:
    """A workspace at rest sits near 1485 MB available, and a browser holding
    three rendered pages still leaves about 1155 MB. Neither may trip this."""
    for available_mb in (1485, 1155, LOW_MEMORY_MB + 1):
        sandbox["available_kb"] = available_mb * 1024

        assert await shed_browser_if_starved() is None, available_mb

    assert sandbox["closes"] == 0


async def test_a_starved_sandbox_has_its_browser_closed(sandbox) -> None:
    """The states this was built from: 14 MB, 19 MB and 21 MB available, with
    every unrelated tool call in the sandbox degrading alongside."""
    sandbox["available_kb"] = 14 * 1024

    outcome = await shed_browser_if_starved()

    assert outcome == (14, True)
    assert sandbox["closes"] == 1


async def test_a_close_that_could_not_run_is_reported_rather_than_retried(
    sandbox,
) -> None:
    """A sandbox with nothing left may not manage to spawn a Node CLI, and
    there is deliberately no escalation to a signal behind it: signals are
    what this did before, and they neither matched the browser nor flushed
    its cookies. The caller says what happened, and the daemon's own idle
    timeout is what remains."""
    sandbox["available_kb"] = 14 * 1024
    sandbox["closed"] = False

    assert await shed_browser_if_starved() == (14, False)


async def test_memory_that_cannot_be_read_is_not_treated_as_pressure(
    monkeypatch,
) -> None:
    """No `/proc/meminfo` is a fabric this does not understand, not a sandbox
    in trouble -- and closing somebody's browser on a guess is worse than not
    closing it."""
    monkeypatch.setattr(browser_guard, "available_memory_mb", lambda: None)
    closes: list[int] = []

    async def _close() -> bool:
        closes.append(1)
        return True

    monkeypatch.setattr(browser_guard, "shed_browser", _close)

    assert await shed_browser_if_starved() is None
    assert closes == []


def test_available_memory_is_what_the_kernel_says_is_available(
    monkeypatch, tmp_path: Path
) -> None:
    """`MemAvailable`, not `MemFree`: reclaimable page cache is not pressure,
    and reading it as pressure would shed the browser of a sandbox that had
    merely read a large file."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(_meminfo(1_520_000))
    monkeypatch.setattr(
        browser_guard,
        "Path",
        lambda argument: (
            meminfo if str(argument) == "/proc/meminfo" else Path(argument)
        ),
    )

    assert available_memory_mb() == 1484


async def test_the_close_goes_through_the_cli_that_owns_the_browser(
    monkeypatch,
) -> None:
    """`agent-browser close --all`, and nothing else. The argv is worth
    pinning because the last version of this named processes instead, and
    those names went stale without anything noticing."""
    seen: dict[str, object] = {}

    class _Done:
        returncode = 0

    def _run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        return _Done()

    monkeypatch.setattr(browser_guard.subprocess, "run", _run)

    assert await browser_guard.shed_browser() is True
    assert seen["argv"] == [browser_guard.AGENT_BROWSER, "close", "--all"]
    assert seen["timeout"] == browser_guard.CLOSE_TIMEOUT_SECONDS


async def test_a_cli_that_is_not_there_is_a_false_rather_than_a_crash(
    monkeypatch,
) -> None:
    """This runs from the reaper loop of a sandbox that is already in
    trouble. Whatever happens, it must not be the thing that ends that loop."""

    def _run(*_args, **_kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(browser_guard.subprocess, "run", _run)

    assert await browser_guard.shed_browser() is False
