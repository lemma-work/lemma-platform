"""The CLI the Desktop app carries for its Agent Host, as the script stages it.

Building one needs a standalone CPython and the CLI's wheels, which is not a
thing to do on every PR; `make desktop-dmg` builds one and runs it from inside
the app. What is decided without building is here: what is pruned, which
files are code that has to be signed before the app around it is, and the
archive and manifest a release publishes for the Agent Host to download.
"""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path

import pytest

from scripts.build_desktop_lemma_cli import (
    archive_name,
    macho_files,
    prune_for_cli,
    publish,
    release_manifest,
    write_archive,
)
from scripts.build_local_host_pack import LEMMA_CLI_LAUNCHER


def _touch(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_every_mach_o_is_found_for_signing_and_nothing_else(tmp_path: Path) -> None:
    thin = _touch(
        tmp_path / "python/bin/python3.14", bytes.fromhex("cffaedfe") + b"rest"
    )
    universal = _touch(
        tmp_path / "cli/pydantic_core/_pydantic_core.so", bytes.fromhex("cafebabe")
    )
    _touch(tmp_path / "bin/lemma", LEMMA_CLI_LAUNCHER.encode())
    _touch(tmp_path / "cli/lemma_cli/app.py", b"print('hi')\n")
    (tmp_path / "python/bin/python3").symlink_to("python3.14")

    # The symlink is the same file, signed once through its target.
    assert macho_files(tmp_path) == sorted([thin, universal])


def test_what_no_lemma_command_runs_is_pruned(tmp_path: Path) -> None:
    python = tmp_path / "python"
    kept = [
        _touch(python / "bin/python3.14"),
        _touch(python / "lib/libpython3.14.dylib"),
        _touch(python / "lib/python3.14/json/__init__.py"),
        _touch(python / "lib/python3.14/lib-dynload/_dbm.cpython-314-darwin.so"),
    ]
    pruned = [
        _touch(python / "bin/pip3"),
        _touch(python / "bin/idle3"),
        _touch(python / "bin/python3.14-config"),
        _touch(python / "lib/python3.14/site-packages/pip/__init__.py"),
        _touch(python / "lib/python3.14/tkinter/__init__.py"),
        _touch(python / "lib/python3.14/test/test_x.py"),
        _touch(python / "lib/python3.14/config-3.14-darwin/libpython3.14.a"),
        _touch(python / "lib/python3.14/lib-dynload/_tkinter.cpython-314-darwin.so"),
        _touch(python / "lib/tcl8.6/init.tcl"),
        _touch(python / "lib/libtk8.6.dylib"),
    ]

    prune_for_cli(python)

    assert all(path.exists() for path in kept)
    assert not [path for path in pruned if path.exists()]


def _staged_cli(root: Path) -> Path:
    launcher = _touch(root / "bin/lemma", LEMMA_CLI_LAUNCHER.encode())
    launcher.chmod(0o755)
    _touch(root / "cli/lemma_cli/app.py", b"print('hi')\n")
    _touch(root / "python/bin/python3.14", bytes.fromhex("cffaedfe"))
    (root / "python/bin/python3").symlink_to("python3.14")
    return root


def test_the_release_archive_unpacks_to_a_runnable_cli(tmp_path: Path) -> None:
    root = _staged_cli(tmp_path / "staged")
    archive = tmp_path / "out" / archive_name("aarch64-apple-darwin")

    sha256, size = write_archive(root, archive)

    assert size == archive.stat().st_size
    assert sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as tar:
        members = {member.name: member for member in tar.getmembers()}
    # At the top level, as the Agent Host renames it into cli/<version>.
    assert "bin/lemma" in members and "cli/lemma_cli/app.py" in members
    assert not any(name.startswith(("/", "..", "staged")) for name in members)
    assert members["bin/lemma"].mode & 0o111
    assert members["python/bin/python3"].issym()
    assert members["python/bin/python3"].linkname == "python3.14"
    assert {member.uid for member in members.values()} == {0}


def test_the_same_tree_is_the_same_digest(tmp_path: Path) -> None:
    root = _staged_cli(tmp_path / "staged")
    first = write_archive(root, tmp_path / "a.tar.gz")
    os.utime(root / "cli/lemma_cli/app.py", (1, 1))
    assert write_archive(root, tmp_path / "b.tar.gz") == first


def test_the_manifest_names_the_archive_beside_it(tmp_path: Path) -> None:
    root = _staged_cli(tmp_path / "staged")
    release = "https://github.com/lemma-work/lemma-platform/releases/download/v0.9.0"

    path = publish(
        root,
        tmp_path / "out",
        version="v0.9.0",
        platform="aarch64-apple-darwin",
        release_url=release + "/",
    )

    # The name `desktop/agent-host/src/lemma_cli/download.rs` asks for.
    assert path.name == "lemma-cli-aarch64-apple-darwin.json"
    manifest = json.loads(path.read_text())
    archive = tmp_path / "out" / "lemma-cli-aarch64-apple-darwin.tar.gz"
    assert manifest == {
        "version": "0.9.0",
        "platform": "aarch64-apple-darwin",
        "url": f"{release}/lemma-cli-aarch64-apple-darwin.tar.gz",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "size": archive.stat().st_size,
    }
    assert manifest == release_manifest(
        version="0.9.0",
        platform="aarch64-apple-darwin",
        release_url=release,
        sha256=manifest["sha256"],
        size=manifest["size"],
    )


def test_only_a_staged_cli_is_published(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit):
        publish(
            empty,
            tmp_path / "out",
            version="0.9.0",
            platform="aarch64-apple-darwin",
            release_url="https://example.invalid/v0.9.0",
        )
