"""The release half of runtime reuse: the guest's input fingerprint, picking a
published archive by it, and archives whose bytes depend only on their tree.

The install half -- reusing an archive's installed contents instead of
downloading it -- is `desktop/src/artifact_install/tests/reuse.rs`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.runtime_artifacts import (
    GUEST_INPUTS,
    REPO_ROOT,
    WORKSPACE_DOCKERFILE,
    WORKSPACE_IMAGE_FLOOR,
    WORKSPACE_IMAGE_INPUTS,
    ReusableGuest,
    ReusableImage,
    guest_fingerprint,
    guest_sidecar,
    reusable_guest,
    reusable_workspace_image,
    reuse_published_guest,
    source_date_epoch,
    workspace_image_fingerprint,
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


def test_a_failed_or_empty_download_builds_instead(tmp_path: Path) -> None:
    """Every way the download can fail is "build instead", never a traceback."""
    good = manifest("1" * 64, sha256="a" * 64, size=1)
    output = tmp_path / "out/lemma-guest-runtime-macos-aarch64.zip"

    def refused(tag: str, name: str, directory: Path) -> None:
        raise subprocess.CalledProcessError(1, ["gh", "release", "download", tag])

    def unreadable(tag: str, name: str, directory: Path) -> None:
        raise OSError("disk full")

    def nothing(tag: str, name: str, directory: Path) -> None:
        return None

    for download in (refused, unreadable, nothing):
        tag = reuse_published_guest(
            target="macos-aarch64",
            fingerprint="1" * 64,
            output=output,
            manifests=[("desktop-nightly-1", good)],
            download=download,
        )
        assert tag is None, download.__name__
        assert not output.exists()


def test_the_manifest_records_the_fingerprint_where_installed_apps_do_not_look() -> None:
    """Installed apps read artifact entries with unknown fields refused, so the
    fingerprint must live at the manifest's top level, never in an entry."""
    workflow = (REPO_ROOT / ".github/workflows/release-local-images.yml").read_text()
    assert '"guest_runtimes": guest_fingerprints,' in workflow
    assert '"images": {"workspace": workspace_fingerprint},' in workflow
    entry = workflow[workflow.index("guest_runtimes[target] = {") :]
    entry = entry[: entry.index("}")]
    assert "fingerprint" not in entry
    mod = (REPO_ROOT / "desktop/src/artifact_install/mod.rs").read_text()
    reference = mod[mod.index("pub(crate) struct ArtifactRef") - 80 :]
    assert "deny_unknown_fields" in reference[:200]


@pytest.fixture
def image_sources(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "templates/python").mkdir(parents=True)
    (root / "Dockerfile").write_text("FROM python@sha256:00\n")
    (root / "templates/python/uv.lock").write_text("numpy 2.5\n")
    (root / "floor").mkdir()
    (root / "floor/lemma.py").write_text("print('floor')\n")
    return root


IMAGE_INPUTS = ("Dockerfile", "templates")


def test_the_workspace_fingerprint_moves_with_its_inputs_and_only_them(
    image_sources: Path,
) -> None:
    def fingerprint(epoch: str = "2026-09") -> str:
        return workspace_image_fingerprint(
            package_epoch=epoch, root=image_sources, inputs=IMAGE_INPUTS
        )

    before = fingerprint()
    assert re.fullmatch(r"[0-9a-f]{64}", before)
    assert fingerprint() == before

    (image_sources / "floor/lemma.py").write_text("print('newer floor')\n")
    assert fingerprint() == before, "the floor is not an input"

    assert fingerprint("2026-10") != before
    (image_sources / "templates/python/uv.lock").write_text("numpy 2.6\n")
    changed_lock = fingerprint()
    assert changed_lock != before
    (image_sources / "Dockerfile").write_text("FROM python@sha256:01\n")
    assert fingerprint() != changed_lock


def test_the_build_epoch_is_the_first_second_of_the_package_month() -> None:
    assert source_date_epoch("2026-09") == int(
        datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
    )
    assert source_date_epoch("2026-12") < source_date_epoch("2027-01")


def _dockerfile_sources() -> set[str]:
    """Every repository path `Dockerfile.workspace` copies into the image."""
    text = (REPO_ROOT / WORKSPACE_DOCKERFILE).read_text()
    sources = set()
    for line in text.splitlines():
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        words = [word for word in line.split()[1:] if not word.startswith("--")]
        sources.update(words[:-1])
    return sources


def test_everything_the_workspace_image_copies_is_hashed_or_floor() -> None:
    """A file the image starts copying must be decided about.

    Hashed, it rebuilds the image when it changes. Floor, it is Lemma code the
    runtime overlay supersedes, and a change to it alone reuses the previous
    image. Neither, and a change to it would ship in no image at all -- the
    next release would reuse the old one and nobody would notice.
    """
    sources = _dockerfile_sources()
    assert sources, "the Dockerfile copies nothing from the repository?"

    def under(path: str, prefixes: tuple[str, ...]) -> bool:
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)

    undecided = sorted(
        path
        for path in sources
        if not under(path, WORKSPACE_IMAGE_INPUTS) and not under(path, WORKSPACE_IMAGE_FLOOR)
    )
    assert undecided == []
    workspace_image_fingerprint(package_epoch="2026-09")  # every input exists


def test_an_image_only_script_is_hashed_not_floor() -> None:
    """A script the overlay does not ship has no newer copy to supersede it.

    `start-workspace-runtime` is the container's command, run before any
    overlay can have been delivered. Treated as floor, a change to it would
    wait for the next unrelated image change to ship.
    """
    spec = importlib.util.spec_from_file_location(
        "build_runtime_bundle", REPO_ROOT / "lemma-backend/scripts/build_runtime_bundle.py"
    )
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    scripts = "lemma-backend/sandbox-images/scripts/"
    image_only = {
        path
        for path in _dockerfile_sources()
        if path.startswith(scripts) and path[len(scripts) :] in builder.IMAGE_ONLY_SCRIPTS
    }

    assert image_only, "the workspace image no longer starts from an image-only script?"
    assert image_only <= set(WORKSPACE_IMAGE_INPUTS)


def image_manifest(
    fingerprint: str | None,
    ref: str = "ghcr.io/lemma-work/lemma-workspace:v0.9.0",
    digest: str = "sha256:" + "a" * 64,
) -> dict[str, object]:
    document: dict[str, object] = {"images": {"workspace": {"ref": ref, "digest": digest}}}
    if fingerprint is not None:
        document["input_fingerprints"] = {
            "guest_runtimes": {},
            "images": {"workspace": fingerprint},
        }
    return document


def test_the_newest_image_with_the_same_fingerprint_is_reused_under_its_first_ref() -> None:
    wanted = "1" * 64
    releases = [
        ("desktop-nightly-new", image_manifest("2" * 64)),
        ("desktop-nightly-before-fingerprints", image_manifest(None)),
        ("desktop-nightly-bad-digest", image_manifest(wanted, digest="sha256:nope")),
        ("desktop-nightly-pinned-ref", image_manifest(wanted, ref="x@sha256:" + "b" * 64)),
        (
            "desktop-nightly-match",
            image_manifest(wanted, ref="ghcr.io/lemma-work/lemma-workspace:test-0123456789ab"),
        ),
        ("v0.8.0", image_manifest(wanted)),
    ]

    assert reusable_workspace_image(releases, wanted) == ReusableImage(
        tag="desktop-nightly-match",
        ref="ghcr.io/lemma-work/lemma-workspace:test-0123456789ab",
        digest="sha256:" + "a" * 64,
    )
    assert reusable_workspace_image(releases, "3" * 64) is None


def test_the_release_workflow_reuses_the_workspace_image_by_fingerprint() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release-local-images.yml").read_text()

    assert "rebuild_workspace_image:" in workflow
    assert "runtime_artifacts.py workspace-image" in workflow
    assert "rewrite-timestamp=true" in workflow
    assert "SOURCE_DATE_EPOCH=" in workflow
