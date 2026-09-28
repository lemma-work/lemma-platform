"""The display bootstrap runs the overlay's scripts, and the image's without one."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.workspace.services.browser_relay_commands import ENSURE_DISPLAY
from sandbox_runtime.paths import RUNTIME_OVERLAY_BIN

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform == "win32", reason="runs a POSIX shell"),
]


def _install(directory: Path, who: str) -> None:
    directory.mkdir()
    for name in ("lemma-ensure-display", "start-vnc-bridge"):
        script = directory / name
        script.write_text(f"#!/bin/sh\necho {who} {name}\n", encoding="utf-8")
        script.chmod(0o755)


def _run(command: str, image: Path) -> list[str]:
    return subprocess.run(
        ["/bin/sh", "-c", command],
        env={"PATH": f"{image}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def test_the_image_scripts_answer_until_an_overlay_is_installed(tmp_path: Path) -> None:
    image = tmp_path / "image"
    overlay = tmp_path / "overlay"
    _install(image, "image")
    command = ENSURE_DISPLAY.replace(RUNTIME_OVERLAY_BIN, str(overlay))

    assert _run(command, image) == [
        "image lemma-ensure-display",
        "image start-vnc-bridge",
    ]


def test_the_overlay_scripts_win_once_installed(tmp_path: Path) -> None:
    """Even where the sandbox's own `PATH` has never heard of the overlay."""
    image = tmp_path / "image"
    overlay = tmp_path / "overlay"
    _install(image, "image")
    _install(overlay, "overlay")
    command = ENSURE_DISPLAY.replace(RUNTIME_OVERLAY_BIN, str(overlay))

    assert _run(command, image) == [
        "overlay lemma-ensure-display",
        "overlay start-vnc-bridge",
    ]
