"""Clearing a profile's lock files must mean "nobody is using it".

Live regression, found on a running stack. `start-browser.sh` deleted
`SingletonCookie`, `SingletonLock`, `SingletonSocket` and
`DevToolsActivePort` every time it ran. That was harmless while the profile
lived in `/tmp` and the script ran once per sandbox with nothing up.

The profile is durable now, and this script is the "make the browser work"
entry point -- the relay's health start, a take-control, a sign-in all reach
it, and the rest of it is idempotent by `pgrep`, so a second run skips Xvfb,
skips x11vnc and leaves the running Chrome alone. Chrome writes
`DevToolsActivePort` only at startup. So the second run deleted the only
record of the live browser's port and nothing rewrote it: `live_port` raised
`BrowserNotRunning`, the socket closed 4409, and the pane said "The browser
is not running" about a browser that was running the whole time, for the
rest of the sandbox's life.

The real block is lifted out of the shipped script and run, rather than
restated here -- a copy of the guard would pass while the script did
anything it liked.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1].parent
    / "sandbox-images/scripts/start-browser.sh"
)

LOCKS = ("SingletonCookie", "SingletonLock", "SingletonSocket", "DevToolsActivePort")


def _guard_block() -> str:
    """The `if pgrep ... fi` that decides whether the locks are cleared."""
    lines = SCRIPT.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("if pgrep -f --"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start : end + 1])


def _run(profile: Path, *, owned: bool) -> None:
    """Run the guard with a `pgrep` that answers `owned`.

    A stub on PATH rather than a real process: the thing under test is what
    the script concludes from the answer, and a test that launched a browser
    to find out would be testing Chrome.
    """
    stubs = profile.parent / "bin"
    stubs.mkdir(exist_ok=True)
    pgrep = stubs / "pgrep"
    pgrep.write_text("#!/bin/sh\nexit %d\n" % (0 if owned else 1))
    pgrep.chmod(0o755)
    subprocess.run(
        ["bash", "-c", f'PROFILE_DIR="{profile}"\n{_guard_block()}'],
        env={"PATH": f"{stubs}:/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    where = tmp_path / "profile"
    where.mkdir()
    for name in LOCKS:
        (where / name).write_text("41363\n")
    return where


def test_a_profile_nobody_owns_has_its_stale_locks_cleared(profile: Path) -> None:
    """The case the deletion was written for, and it still has to work: a
    lock file naming a process that died with the sandbox stops the next
    Chrome starting at all."""
    _run(profile, owned=False)

    assert [name for name in LOCKS if (profile / name).exists()] == []


def test_a_profile_a_browser_is_using_keeps_them(profile: Path) -> None:
    """The regression. `DevToolsActivePort` is the only record of which port
    a running Chrome is on, and Chrome writes it once, at startup."""
    _run(profile, owned=True)

    assert [name for name in LOCKS if (profile / name).exists()] == list(LOCKS)
