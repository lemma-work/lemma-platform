"""A Lemma command resolves to the overlay's copy, and to the image's without one."""

from __future__ import annotations

from pathlib import Path

from sandbox_runtime import runtime_install
from sandbox_runtime.paths import (
    _OVERLAY_STAMP,
    IMAGE_BIN,
    RUNTIME_FLOOR,
    running_runtime_version,
    sandbox_command,
)


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


def _installed_overlay(root: Path, version: str) -> Path:
    """An overlay laid out as `runtime_install` leaves one, `current` and all."""
    directory = root / f"sha256-{version.split(':', 1)[1]}"
    package = directory / "site-packages" / "sandbox_runtime"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (directory / ".stamp").write_text(version, encoding="utf-8")
    (root / "current").symlink_to(directory.name)
    return root / "current" / "site-packages" / "sandbox_runtime" / "__init__.py"


def test_a_server_imported_from_the_overlay_reports_its_version(tmp_path: Path) -> None:
    version = "sha256:" + "a" * 64
    package_file = _installed_overlay(tmp_path, version)

    assert (
        running_runtime_version(str(package_file), overlay_root=str(tmp_path))
        == version
    )


def test_the_version_is_the_one_imported_not_the_one_current_names_later(
    tmp_path: Path,
) -> None:
    """A newer install flips `current`; the running server did not change."""
    version = "sha256:" + "a" * 64
    package_file = _installed_overlay(tmp_path, version)
    reported = running_runtime_version(str(package_file), overlay_root=str(tmp_path))
    newer = tmp_path / ("sha256-" + "b" * 64)
    newer.mkdir()
    (newer / ".stamp").write_text("sha256:" + "b" * 64, encoding="utf-8")
    (tmp_path / "current").unlink()
    (tmp_path / "current").symlink_to(newer.name)

    assert reported == version


def test_a_server_imported_from_the_image_reports_the_floor(tmp_path: Path) -> None:
    floor = tmp_path / "app" / "sandbox_runtime" / "__init__.py"
    floor.parent.mkdir(parents=True)
    floor.write_text("", encoding="utf-8")

    assert (
        running_runtime_version(str(floor), overlay_root=str(tmp_path / "opt"))
        == RUNTIME_FLOOR
    )


def test_an_overlay_with_no_stamp_reports_the_floor(tmp_path: Path) -> None:
    package_file = _installed_overlay(tmp_path, "sha256:" + "a" * 64)
    (tmp_path / ("sha256-" + "a" * 64) / ".stamp").unlink()

    assert running_runtime_version(str(package_file), overlay_root=str(tmp_path)) == (
        RUNTIME_FLOOR
    )


def test_the_stamp_is_the_one_the_installer_writes() -> None:
    assert _OVERLAY_STAMP == runtime_install.STAMP_NAME
