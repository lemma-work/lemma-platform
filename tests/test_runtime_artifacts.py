"""The release half of runtime reuse: the guest's input fingerprint, picking a
published archive by it, and archives whose bytes depend only on their tree.

The install half -- reusing an archive's installed contents instead of
downloading it -- is `desktop/src/artifact_install/tests/reuse.rs`.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
from pathlib import Path

import pytest

from scripts.runtime_artifacts import (
    GUEST_INPUTS,
    REPO_ROOT,
    ReusableGuest,
    guest_fingerprint,
    guest_sidecar,
    reusable_guest,
    reuse_published_guest,
    write_deterministic_zip,
)

INPUTS = ("build.sh", "image")


@pytest.fixture
def sources(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "image/overlay/bin").mkdir(parents=True)
    (root / "build.sh").write_text("#!/bin/sh\n")
    (root / "image/Dockerfile").write_text("FROM ubuntu@sha256:00\n")
    script = root / "image/overlay/bin/lemma-init"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    (tmp_path / "guestd").write_bytes(b"\x7fELF guestd")
    return root


def fingerprint(root: Path, **overrides: object) -> str:
    arguments: dict[str, object] = {
        "target": "macos-aarch64",
        "guestd": root.parent / "guestd",
        "package_epoch": "2026-09",
        "root": root,
        "inputs": INPUTS,
    }
    arguments.update(overrides)
    return guest_fingerprint(**arguments)  # type: ignore[arg-type]


def test_identical_inputs_give_the_same_fingerprint(sources: Path, tmp_path: Path) -> None:
    first = fingerprint(sources)
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert fingerprint(sources) == first
    # Where the checkout is and when its files were written are not inputs.
    moved = tmp_path / "elsewhere"
    os.rename(sources, moved)
    os.utime(moved / "build.sh", (time.time() + 3600, time.time() + 3600))
    assert fingerprint(moved) == first


@pytest.mark.parametrize(
    "change",
    [
        "script content",
        "image file content",
        "added overlay file",
        "removed overlay file",
        "executable bit",
        "guestd binary",
        "target",
        "package epoch",
    ],
)
def test_changing_any_input_changes_the_fingerprint(sources: Path, change: str) -> None:
    before = fingerprint(sources)
    overrides: dict[str, object] = {}
    if change == "script content":
        (sources / "build.sh").write_text("#!/bin/sh\nset -e\n")
    elif change == "image file content":
        (sources / "image/Dockerfile").write_text("FROM ubuntu@sha256:01\n")
    elif change == "added overlay file":
        (sources / "image/overlay/etc").mkdir()
        (sources / "image/overlay/etc/fstab").write_text("")
    elif change == "removed overlay file":
        (sources / "image/overlay/bin/lemma-init").unlink()
    elif change == "executable bit":
        (sources / "image/overlay/bin/lemma-init").chmod(0o644)
    elif change == "guestd binary":
        (sources.parent / "guestd").write_bytes(b"\x7fELF guestd 2")
    elif change == "target":
        overrides["target"] = "windows-x86_64"
    elif change == "package epoch":
        overrides["package_epoch"] = "2026-10"
    assert fingerprint(sources, **overrides) != before


def test_a_missing_input_is_an_error_not_an_empty_hash(sources: Path) -> None:
    with pytest.raises(FileNotFoundError):
        fingerprint(sources, inputs=(*INPUTS, "not-there"))


def test_every_guest_input_is_named() -> None:
    """Every repository file the guest build reads is one the fingerprint
    hashes. A new file the build script starts reading, and nobody adds here,
    would change the guest without changing its fingerprint -- and the next
    release would republish the old guest."""
    script = (REPO_ROOT / "scripts/build_local_guest_runtime.sh").read_text()
    read = set(re.findall(r'\$repo_root/([\w./-]+)', script))
    assert read, "the build script no longer reads anything from the repository?"
    for path in read:
        assert any(
            path == name or path.startswith(f"{name}/") for name in GUEST_INPUTS
        ), f"{path} is read by the guest build but not in GUEST_INPUTS"
    for name in GUEST_INPUTS:
        assert (REPO_ROOT / name).exists(), name


def make_tree(root: Path, mode: int) -> Path:
    (root / "target/sub").mkdir(parents=True)
    (root / "target/b.txt").write_text("bee")
    (root / "target/sub/a.txt").write_text("ay")
    tool = root / "target/tool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(mode)
    (root / "target/tool-link").symlink_to("tool")
    return root


def test_an_unchanged_tree_archives_to_the_same_bytes(tmp_path: Path) -> None:
    first = make_tree(tmp_path / "one", 0o755)
    second = make_tree(tmp_path / "two", 0o775)
    later = time.time() + 86400
    for path in (second / "target").rglob("*"):
        os.utime(path, (later, later), follow_symlinks=False)
    write_deterministic_zip(first, tmp_path / "one.zip", prefix="local-runtime")
    write_deterministic_zip(second, tmp_path / "two.zip", prefix="local-runtime")
    assert (tmp_path / "one.zip").read_bytes() == (tmp_path / "two.zip").read_bytes()

    with zipfile.ZipFile(tmp_path / "one.zip") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        assert names == sorted(names)
        assert names == [
            "local-runtime/target/b.txt",
            "local-runtime/target/sub/a.txt",
            "local-runtime/target/tool",
            "local-runtime/target/tool-link",
        ]
        assert {info.date_time for info in infos} == {(1980, 1, 1, 0, 0, 0)}
        modes = {info.filename: (info.external_attr >> 16) & 0o777 for info in infos}
        assert modes["local-runtime/target/tool"] == 0o755
        assert modes["local-runtime/target/b.txt"] == 0o644
        # A link is stored as what it points at: the installer refuses links.
        assert (infos[3].external_attr >> 16) & 0o170000 == 0o100000
        assert archive.read("local-runtime/target/tool-link") == b"#!/bin/sh\n"


def test_a_changed_tree_archives_differently(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "one", 0o755)
    write_deterministic_zip(tree, tmp_path / "before.zip")
    (tree / "target/b.txt").write_text("bea")
    write_deterministic_zip(tree, tmp_path / "after.zip")
    assert (tmp_path / "before.zip").read_bytes() != (tmp_path / "after.zip").read_bytes()


def test_the_guest_sidecar_describes_the_archive_and_its_inputs(tmp_path: Path) -> None:
    tree = tmp_path / "artifact/macos-aarch64"
    tree.mkdir(parents=True)
    (tree / "disk.raw").write_bytes(b"\0" * 4096)
    (tree / "vmlinuz").write_bytes(b"kernel")
    (tree / "runtime.json").write_text("{}")
    archive = tmp_path / "guest.zip"
    write_deterministic_zip(tmp_path / "artifact", archive, compresslevel=9)
    sidecar = guest_sidecar(archive, "f" * 64)
    assert sidecar["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert sidecar["size"] == archive.stat().st_size
    assert sidecar["expanded_size"] == 4096 + 6 + 2
    assert sidecar["input_fingerprint"] == "f" * 64
    assert sidecar["breakdown"]["root_bytes"] == 4096  # type: ignore[index]


def manifest(fingerprint: str | None, sha256: str = "a" * 64, size: int = 10) -> dict[str, object]:
    document: dict[str, object] = {
        "guest_runtimes": {"macos-aarch64": {"sha256": sha256, "size": size}},
    }
    if fingerprint is not None:
        document["input_fingerprints"] = {"guest_runtimes": {"macos-aarch64": fingerprint}}
    return document


def test_the_newest_release_with_the_same_fingerprint_is_chosen() -> None:
    wanted = "1" * 64
    releases = [
        ("desktop-nightly-new", manifest("2" * 64)),
        ("desktop-nightly-before-fingerprints", manifest(None)),
        ("desktop-nightly-bad-digest", manifest(wanted, sha256="not-a-digest")),
        ("desktop-nightly-match", manifest(wanted, sha256="b" * 64, size=7)),
        ("v0.8.0", manifest(wanted, sha256="c" * 64)),
    ]
    assert reusable_guest(releases, "macos-aarch64", wanted) == ReusableGuest(
        tag="desktop-nightly-match", sha256="b" * 64, size=7
    )
    assert reusable_guest(releases, "windows-x86_64", wanted) is None
    assert reusable_guest(releases, "macos-aarch64", "3" * 64) is None


def test_a_published_archive_is_used_only_if_it_matches_its_manifest(tmp_path: Path) -> None:
    body = b"published guest"
    good = manifest("1" * 64, sha256=hashlib.sha256(body).hexdigest(), size=len(body))
    output = tmp_path / "out/lemma-guest-runtime-macos-aarch64.zip"
    fetched: list[str] = []

    def download(contents: bytes):
        def fetch(tag: str, name: str, directory: Path) -> None:
            fetched.append(tag)
            (directory / name).write_bytes(contents)

        return fetch

    tag = reuse_published_guest(
        target="macos-aarch64",
        fingerprint="1" * 64,
        output=output,
        manifests=[("desktop-nightly-1", good)],
        download=download(body),
    )
    assert tag == "desktop-nightly-1"
    assert output.read_bytes() == body

    output.unlink()
    tag = reuse_published_guest(
        target="macos-aarch64",
        fingerprint="1" * 64,
        output=output,
        manifests=[("desktop-nightly-1", good)],
        download=download(b"published guesT"),
    )
    assert tag is None, "same size, different bytes: built instead"
    assert not output.exists()
    assert list(output.parent.iterdir()) == [], "nothing left behind to be uploaded"

    assert (
        reuse_published_guest(
            target="macos-aarch64",
            fingerprint="9" * 64,
            output=output,
            manifests=[("desktop-nightly-1", good)],
            download=download(body),
        )
        is None
    )
    assert fetched == ["desktop-nightly-1", "desktop-nightly-1"], "no match, no download"


def test_the_manifest_records_the_fingerprint_where_installed_apps_do_not_look() -> None:
    """Installed apps read artifact entries with unknown fields refused, so the
    fingerprint must live at the manifest's top level, never in an entry."""
    workflow = (REPO_ROOT / ".github/workflows/release-local-images.yml").read_text()
    assert '"input_fingerprints": {"guest_runtimes": guest_fingerprints}' in workflow
    entry = workflow[workflow.index("guest_runtimes[target] = {") :]
    entry = entry[: entry.index("}")]
    assert "fingerprint" not in entry
    mod = (REPO_ROOT / "desktop/src/artifact_install/mod.rs").read_text()
    reference = mod[mod.index("pub(crate) struct ArtifactRef") - 80 :]
    assert "deny_unknown_fields" in reference[:200]
