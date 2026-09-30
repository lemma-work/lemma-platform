"""`save-webpage` must not hand its capture lock to the daemons it starts.

The capture lock is an open descriptor (fd 9) held for the life of one capture.
When the browser is not up yet, the capture starts it through
`lemma-ensure-display`, and what that starts -- Xvfb, the window manager, the
browser relay -- outlives the capture. A child inherits every open descriptor,
so those daemons went on holding the lock for the life of the sandbox: every
capture after the first waited out the lock timeout and failed, and a
`web_fetch` of three JavaScript pages took minutes and returned nothing.

Driven with stubs on `PATH` in the manner of `test_ensure_display_script.py`;
the stubbed `lemma-ensure-display` records whether fd 9 reached it.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[2] / "sandbox-images/scripts/save-webpage.sh"


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def test_the_display_it_starts_does_not_inherit_the_capture_lock(
    tmp_path: Path,
) -> None:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    mark = tmp_path / "fd9"
    # Holding the lock is not what is under test, and macOS has no flock.
    _stub(binaries, "flock", "exit 0")
    _stub(binaries, "browser-is-live", "exit 1")
    _stub(
        binaries,
        "lemma-ensure-display",
        f'if ( : >&9 ) 2>/dev/null; then echo inherited > "{mark}"; '
        f'else echo closed > "{mark}"; fi',
    )
    _stub(binaries, "agent-browser", "exit 0")

    subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "https://example.com",
            "--formats",
            "markdown",
            "--out",
            str(tmp_path / "out"),
            "--name",
            "page",
        ],
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "AGENT_BROWSER_CAPTURE_LOCK": str(tmp_path / "capture.lock"),
        },
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert mark.read_text().strip() == "closed"
