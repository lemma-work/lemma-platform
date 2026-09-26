"""`start-browser` still works for instructions written before it was renamed.

The command became `lemma-ensure-display`, and `agent-browser open` now brings
the display up by itself, but copies of the `browser` skill installed before
either change still tell an agent to run `start-browser` first. The script is
run directly with stubs on `PATH`, and both image definitions are read for the
line that installs it -- the images copy an explicit file list, so a script
neither of them names is a script no sandbox has.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "sandbox-images/scripts/start-browser.sh"
SOURCE = "lemma-backend/sandbox-images/scripts/start-browser.sh"


def _run(tmp_path: Path, *args: str) -> list[str]:
    """Run the script with both commands it calls stubbed to log their argv."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "calls"
    for name in ("lemma-ensure-display", "agent-browser"):
        stub = binaries / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        stub.chmod(0o755)
    subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}"},
        check=True,
        timeout=10,
    )
    return [line.rstrip() for line in log.read_text().splitlines()]


def test_with_a_url_it_brings_the_display_up_then_opens_the_page(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "https://example.com") == [
        "lemma-ensure-display",
        "agent-browser open https://example.com",
    ]


def test_without_arguments_it_only_brings_the_display_up(tmp_path: Path) -> None:
    assert _run(tmp_path) == ["lemma-ensure-display"]


def test_both_workspace_images_install_it() -> None:
    dockerfile = (BACKEND / "sandbox-images/Dockerfile.workspace").read_text()
    assert f"COPY {SOURCE} /usr/local/bin/start-browser" in dockerfile
    assert "/usr/local/bin/start-browser \\" in dockerfile, "not made executable"

    builder = (BACKEND / "sandbox-images/templates/e2b/build_templates.py").read_text()
    assert f'"{SOURCE}",\n            "/usr/local/bin/start-browser",' in builder
