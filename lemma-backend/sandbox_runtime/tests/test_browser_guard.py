"""The backstop that holds when everything gentler has already failed.

None of these tests signal a real process. `shed_browser` matches things a
developer plausibly has running -- agent-browser, Xvfb -- so the kill is
injected wherever it is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
