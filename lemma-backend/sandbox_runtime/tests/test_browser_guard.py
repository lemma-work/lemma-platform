"""The backstop that holds when everything gentler has already failed.

Nothing here signals a real process, because nothing in the subject does any
more. This guard used to scan `/proc`, match command lines against a list of
patterns and send SIGTERM then SIGKILL -- and the patterns had gone stale, so
on a real sandbox it matched 1 of 14 Chromium processes and had been shedding
the display and the daemon while leaving everything that held the memory.

It asks the daemon to close the browser now: measured to be the only stop
that also writes the profile back, and it names no process, so it cannot go
quietly out of date the way the pattern list did.

**And it reads the cgroup, because `/proc/meminfo` is not namespaced.**
Measured inside a container limited to 2 GiB: `MemTotal` 8.8 GiB, `nproc` 8,
`memory.max` 2147483648. The threshold it used to apply was comparing a
host-wide number against a per-sandbox one -- unfireable on a roomy host,
and liable to fire for somebody else's reasons on a busy one.

Two negatives here are worth as much as the positives, and both come from
measurement rather than reasoning: a cgroup whose `memory.current` has
reached `memory.max` purely on page cache must not shed, and the
twelve-heavy-tab state that the research recorded as healthy must not shed
either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

from sandbox_runtime.workspace import browser_guard
from sandbox_runtime.workspace.browser_guard import (
    HEADROOM_FLOOR_MB,
    LOW_MEMORY_MB,
    shed_browser_if_starved,
)

_MB = 1024 * 1024


def _write_cgroup(
    root: Path,
    *,
    limit_mb: int | None = 2048,
    anon_mb: int = 400,
    shmem_mb: int = 0,
    current_mb: int | None = None,
    oom_kill: int = 0,
    pressure: float | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "memory.max").write_text("max" if limit_mb is None else str(limit_mb * _MB))
    (root / "memory.current").write_text(
        str((current_mb if current_mb is not None else anon_mb + 100) * _MB)
    )
    # A real `memory.stat` is ~40 lines in no particular order, with keys
    # this parser has never heard of. Shaped like one on purpose.
    (root / "memory.stat").write_text(
        "\n".join(
            [
                f"anon {anon_mb * _MB}",
                "file 524288000",
                "kernel_stack 1048576",
                f"slab_unreclaimable {2 * _MB}",
                f"shmem {shmem_mb * _MB}",
                "unevictable 0",
                "inactive_file 419430400",
                "some_key_from_a_newer_kernel 1",
            ]
        )
    )
    (root / "memory.events").write_text(
        f"low 0\nhigh 0\nmax 614\noom 0\noom_kill {oom_kill}\n"
    )
    (root / "memory.swap.current").write_text("0")
    (root / "memory.swap.max").write_text(str(2048 * _MB))
    if pressure is not None:
        (root / "memory.pressure").write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            f"full avg10={pressure:.2f} avg60=0.00 avg300=0.00 total=0\n"
        )


@pytest.fixture
def sandbox(monkeypatch, tmp_path: Path):
    """A cgroup on disk, a `/proc/meminfo`, and a close that is only counted."""
    root = tmp_path / "cgroup"
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:        9214732 kB\nMemFree: 64280 kB\nMemAvailable:    6570496 kB\n"
    )
    state = {"closes": 0, "closed": True, "root": root, "meminfo": meminfo}
    _write_cgroup(root)

    from sandbox_runtime import sandbox_memory

    def _read():
        return sandbox_memory.read_memory(root=root, meminfo=meminfo)

    async def _close() -> bool:
        state["closes"] += 1
        return bool(state["closed"])

    monkeypatch.setattr(browser_guard, "read_memory", _read)
    monkeypatch.setattr(browser_guard, "shed_browser", _close)
    monkeypatch.setattr(browser_guard, "_last", browser_guard._Last())
    return state


class TestTheStatesThatMustNotShed:
    async def test_page_cache_at_the_limit_is_not_pressure(self, sandbox) -> None:
        """The negative the research bought. Reading 3 GB of file data through
        the cgroup drove `memory.current` to exactly `memory.max` with 614
        successful reclaims, `oom_kill 0`, and `anon` going *down*. Shedding
        a browser because somebody read a file is the failure this replaces.
        """
        _write_cgroup(sandbox["root"], anon_mb=300, current_mb=2048, oom_kill=0)

        assert await shed_browser_if_starved() is None
        assert sandbox["closes"] == 0

    async def test_twelve_heavy_tabs_are_a_working_sandbox(self, sandbox) -> None:
        """Measured healthy: `anon` 1188 MB, `memory.events` all zero,
        nothing wrong. This is exactly why the suggested 1.2 GB ceiling on
        `anon + shmem` was not adopted -- it fires here."""
        _write_cgroup(sandbox["root"], anon_mb=1188, current_mb=1671)

        assert await shed_browser_if_starved() is None
        assert sandbox["closes"] == 0

    async def test_a_sandbox_at_rest_is_left_alone(self, sandbox) -> None:
        _write_cgroup(sandbox["root"], anon_mb=400)

        assert await shed_browser_if_starved() is None


class TestTheSignals:
    async def test_a_fresh_oom_kill_sheds(self, sandbox) -> None:
        """A cgroup OOM kill takes the largest resident task, which is
        essentially always a renderer -- so the first is the bulkhead
        working and this is the browser refusing to fit."""
        await shed_browser_if_starved()  # seeds the counter at 0
        _write_cgroup(sandbox["root"], oom_kill=1)

        outcome = await shed_browser_if_starved()

        assert outcome is not None and outcome.signal == "oom_kill"
        assert outcome.closed is True

    async def test_an_old_oom_kill_does_not(self, sandbox) -> None:
        """A resumed sandbox can wake with a non-zero counter from before it
        slept. Only the delta means anything."""
        _write_cgroup(sandbox["root"], oom_kill=3)

        assert await shed_browser_if_starved() is None
        assert await shed_browser_if_starved() is None

    async def test_a_sustained_stall_sheds_and_a_single_sample_does_not(
        self, sandbox
    ) -> None:
        """`python -c pass` taking 61 seconds *is* a memory stall, so this is
        the only signal that measures the harm rather than predicting it.
        Two ticks, because one sample of a ten-second average is not a
        trend."""
        _write_cgroup(sandbox["root"], pressure=40.0)
        assert await shed_browser_if_starved() is None, "one sample is not a trend"

        _write_cgroup(sandbox["root"], pressure=40.0)
        outcome = await shed_browser_if_starved()

        assert outcome is not None and outcome.signal == "memory_stall"

    async def test_a_calm_stall_reading_never_sheds(self, sandbox) -> None:
        for _ in range(3):
            _write_cgroup(sandbox["root"], pressure=9.9)
            assert await shed_browser_if_starved() is None

    async def test_the_headroom_floor_is_the_backstop(self, sandbox) -> None:
        _write_cgroup(sandbox["root"], anon_mb=2048 - HEADROOM_FLOOR_MB)

        outcome = await shed_browser_if_starved()

        assert outcome is not None and outcome.signal == "headroom"

    async def test_one_megabyte_of_room_above_the_floor_is_enough(
        self, sandbox
    ) -> None:
        _write_cgroup(sandbox["root"], anon_mb=2048 - HEADROOM_FLOOR_MB - 3)

        assert await shed_browser_if_starved() is None


class TestWhereThereIsNoCgroup:
    async def test_it_falls_back_to_the_host_reading(self, sandbox) -> None:
        """A fabric with no `memory.max` to read. On a Firecracker guest --
        which is what E2B runs -- the guest kernel reports the VM's own
        memory, so `MemAvailable` is honest there."""
        _write_cgroup(sandbox["root"], limit_mb=None)
        sandbox["meminfo"].write_text(f"MemAvailable:    {14 * 1024} kB\n")

        outcome = await shed_browser_if_starved()

        assert outcome is not None and outcome.signal == "available"
        assert outcome.available_mb == 14

    async def test_a_healthy_host_reading_does_not_shed(self, sandbox) -> None:
        _write_cgroup(sandbox["root"], limit_mb=None)
        sandbox["meminfo"].write_text(
            f"MemAvailable:    {LOW_MEMORY_MB * 1024 + 1} kB\n"
        )

        assert await shed_browser_if_starved() is None

    async def test_memory_that_cannot_be_read_at_all_is_not_pressure(
        self, sandbox, tmp_path: Path, monkeypatch
    ) -> None:
        """Unknown is not "empty". A guard that shed on an unreadable file
        would shed on every sandbox whose kernel names things differently."""
        from sandbox_runtime import sandbox_memory

        empty = tmp_path / "nothing"
        absent = tmp_path / "no-meminfo"
        # `monkeypatch`, not a bare assignment: the fixture's own patch is
        # undone at teardown and a raw one here would outlive this test and
        # silently disarm every case after it.
        monkeypatch.setattr(
            browser_guard,
            "read_memory",
            lambda: sandbox_memory.read_memory(root=empty, meminfo=absent),
        )

        assert await shed_browser_if_starved() is None


class TestClosing:
    async def test_a_close_that_could_not_run_is_reported_not_retried(
        self, sandbox
    ) -> None:
        """A sandbox with nothing left may not manage to spawn a Node CLI,
        and there is deliberately no escalation to a signal behind it:
        signals are what this did before, and they neither matched the
        browser nor wrote its profile back."""
        sandbox["closed"] = False
        _write_cgroup(sandbox["root"], anon_mb=2048 - HEADROOM_FLOOR_MB)

        outcome = await shed_browser_if_starved()

        assert outcome is not None and outcome.closed is False

    async def test_the_argv_is_the_one_the_image_ships(self) -> None:
        """Pinned because the last version of this went stale without anyone
        noticing -- it matched one Chromium process in fourteen."""
        import subprocess

        seen: dict[str, object] = {}

        def _run(argv, **kwargs):
            seen["argv"] = argv
            seen["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(argv, 0)

        original = subprocess.run
        subprocess.run = _run  # type: ignore[assignment]
        try:
            assert await browser_guard.shed_browser() is True
        finally:
            subprocess.run = original  # type: ignore[assignment]

        assert seen["argv"] == [browser_guard.AGENT_BROWSER, "close", "--all"]
        assert seen["timeout"] == browser_guard.CLOSE_TIMEOUT_SECONDS
