"""A Lemma command resolves to the overlay's copy, and to the image's without one."""

from __future__ import annotations

from pathlib import Path

from sandbox_runtime.paths import IMAGE_BIN, sandbox_command


def test_the_overlay_copy_wins_when_it_is_installed(tmp_path: Path) -> None:
    script = tmp_path / "set-display-size"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)

    assert sandbox_command("set-display-size", overlay_bin=str(tmp_path)) == (
        f"{tmp_path}/set-display-size"
    )


def test_the_image_copy_is_the_floor_without_an_overlay(tmp_path: Path) -> None:
    assert sandbox_command("set-display-size", overlay_bin=str(tmp_path)) == (
        f"{IMAGE_BIN}/set-display-size"
    )


def test_a_file_that_cannot_run_is_not_a_command(tmp_path: Path) -> None:
    """A half-extracted overlay must not take a command away from the image."""
    (tmp_path / "start-vnc-bridge").write_text("#!/bin/sh\n", encoding="utf-8")

    assert sandbox_command("start-vnc-bridge", overlay_bin=str(tmp_path)) == (
        f"{IMAGE_BIN}/start-vnc-bridge"
    )
