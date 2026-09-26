#!/usr/bin/env python3
"""Make Lemma Desktop's runtime archives stable across releases.

A release ships two runtime archives per platform: the host pack and the
guest runtime. Every release rebuilt both, and neither build is reproducible
-- the guest image installs packages at build time, `mkfs.ext4` writes a fresh
UUID and hash seed, and the archives carried build-time mtimes -- so two builds
from one commit published different bytes, and every installed Lemma
downloaded the whole guest again on every update.

Three pieces live here:

``fingerprint``
    A digest over exactly what determines the guest runtime's content: the
    build script, the image definition and its overlay, the boot-asset
    preparer, this file (it writes the archive), the guest daemon binary that
    goes into the image, the target, and a package epoch. It is computed before
    the image is built, so a release whose fingerprint matches an earlier one
    can skip the build entirely.

``reuse``
    Find the newest published release whose manifest records the same
    fingerprint for the target, download its archive, check it against that
    manifest's SHA-256 and size, and hand it back to be republished unchanged.

``zip``
    Write an archive whose bytes depend only on the tree: sorted entries, a
    fixed timestamp, normalised permissions, no directory entries and no build
    paths. That makes a rebuild of an unchanged tree stable too, but it is not
    what the reuse depends on -- the guest's *tree* is not reproducible.

Standard library only: the release jobs run it with the runner's own Python.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT_SCHEMA = "lemma-guest-runtime-fingerprint/1"

# Relative to the repository. Every file under a directory counts, with its
# executable bit: the overlay's scripts are installed into the image as they
# are. Changing what goes into the guest means adding it here, and
# `test_every_guest_input_is_named` fails a script change that forgets to.
GUEST_INPUTS = (
    "scripts/build_local_guest_runtime.sh",
    "scripts/runtime_artifacts.py",
    "desktop/scripts/prepare_guest_boot.py",
    "desktop/local-runtime/guest-image",
)

# 1980-01-01, the earliest time a ZIP entry can carry.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_under(root: Path, entry: Path) -> Iterator[Path]:
    if entry.is_symlink():
        raise ValueError(f"fingerprint inputs may not be links: {entry}")
    if entry.is_file():
        yield entry
        return
    if not entry.is_dir():
        raise FileNotFoundError(f"fingerprint input is missing: {entry.relative_to(root)}")
    for child in sorted(entry.iterdir()):
        yield from _files_under(root, child)


def _executable(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


def guest_fingerprint(
    *,
    target: str,
    guestd: Path,
    package_epoch: str,
    root: Path = REPO_ROOT,
    inputs: Iterable[str] = GUEST_INPUTS,
) -> str:
    """The guest runtime's input fingerprint, as lowercase hex.

    The guest daemon is hashed as the built binary, not as its sources: the
    binary is what is copied into the image, and it changes exactly when
    anything that reaches it does -- its crate, a dependency, the toolchain --
    without also changing for every version bump that only touches
    `Cargo.lock`. A build that is not bit-for-bit reproducible costs a rebuild,
    never a stale guest.

    `package_epoch` stands for the one input that cannot be hashed from here:
    the Ubuntu archive `apt-get` installs from. The base image is pinned by
    digest, the package versions are not, so a fixed fingerprint would hold
    the guest's packages still for ever. The release workflow passes the month,
    which caps how stale they can get.
    """
    if not target or not package_epoch:
        raise ValueError("target and package epoch are required")
    lines = [FINGERPRINT_SCHEMA, f"target {target}", f"package-epoch {package_epoch}"]
    for name in inputs:
        for path in _files_under(root, root / name):
            relative = path.relative_to(root).as_posix()
            mode = "x" if _executable(path) else "-"
            lines.append(f"file {mode} {_sha256_file(path)} {relative}")
    lines.append(f"guestd {_sha256_file(guestd)}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _zip_entries(source: Path, prefix: str) -> list[tuple[str, Path]]:
    # A link to a file is stored as the file, as the host pack always has
    # been: the standalone Python links `python3` to its versioned binary, and
    # the installer refuses link entries.
    entries = []
    for path in source.rglob("*"):
        if path.is_file():
            name = path.relative_to(source).as_posix()
            entries.append((f"{prefix}/{name}" if prefix else name, path))
    return sorted(entries)


def _set_compress_level(info: zipfile.ZipInfo, level: int) -> None:
    # Public from Python 3.13; the release runners' 3.12 has only the private
    # slot, which `ZipFile.open` reads either way.
    if hasattr(zipfile.ZipInfo, "compress_level"):
        info.compress_level = level
    else:
        info._compresslevel = level


def write_deterministic_zip(
    source: Path, destination: Path, *, prefix: str = "", compresslevel: int = 6
) -> None:
    """Archive every file under `source`, independent of when and where.

    Only files are stored: the installer creates parent directories as it
    extracts, and a directory entry would carry a mode the checkout decided.
    Permissions are 0755 or 0644 by the executable bit, so the umask of the
    machine that built the tree does not reach the archive.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", allowZip64=True) as archive:
        for name, path in _zip_entries(source, prefix):
            info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
            info.create_system = 3
            mode = 0o755 if _executable(path) else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            _set_compress_level(info, compresslevel)
            # Declared up front so the writer chooses ZIP64 only for an entry
            # that needs it, as `zip` did, rather than for every entry.
            info.file_size = path.stat().st_size
            with path.open("rb") as reader, archive.open(info, "w") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    writer.write(chunk)


def guest_sidecar(archive: Path, fingerprint: str) -> dict[str, object]:
    """What the manifest job reads about a guest archive, beside it."""
    with zipfile.ZipFile(archive) as opened:
        infos = opened.infolist()

    def total(*suffixes: str) -> int:
        return sum(entry.file_size for entry in infos if entry.filename.endswith(suffixes))

    return {
        "sha256": _sha256_file(archive),
        "size": archive.stat().st_size,
        "expanded_size": sum(entry.file_size for entry in infos),
        "input_fingerprint": fingerprint,
        "breakdown": {
            "kernel_bytes": total("/vmlinuz"),
            "initrd_bytes": total("/initrd"),
            "root_bytes": total("/disk.raw", "/rootfs.tar"),
            "metadata_bytes": total("/runtime.json", "/packages.txt", "/kernel-release"),
        },
    }


@dataclass(frozen=True)
class ReusableGuest:
    tag: str
    sha256: str
    size: int


def reusable_guest(
    manifests: Iterable[tuple[str, dict[str, object]]], target: str, fingerprint: str
) -> ReusableGuest | None:
    """The newest release whose manifest built this target's guest from the
    same inputs, given `(tag, manifest)` newest first.

    Only a manifest that states the fingerprint counts. One from before this
    field, or one naming a different fingerprint, is not evidence of anything.
    """
    for tag, manifest in manifests:
        fingerprints = manifest.get("input_fingerprints")
        if not isinstance(fingerprints, dict):
            continue
        guests = fingerprints.get("guest_runtimes")
        if not isinstance(guests, dict) or guests.get(target) != fingerprint:
            continue
        runtimes = manifest.get("guest_runtimes")
        entry = runtimes.get(target) if isinstance(runtimes, dict) else None
        if not isinstance(entry, dict):
            continue
        sha256, size = entry.get("sha256"), entry.get("size")
        if (
            isinstance(sha256, str)
            and len(sha256) == 64
            and all(char in "0123456789abcdef" for char in sha256)
            and type(size) is int
            and size > 0
        ):
            return ReusableGuest(tag=tag, sha256=sha256, size=size)
    return None


def _gh(*args: str) -> bytes:
    return subprocess.run(["gh", *args], check=True, capture_output=True).stdout


def _published_manifests(limit: int) -> Iterator[tuple[str, dict[str, object]]]:
    listed = json.loads(
        _gh(
            "release", "list", "--limit", str(limit), "--exclude-drafts",
            "--json", "tagName,createdAt",
        )
    )
    for release in sorted(listed, key=lambda item: item["createdAt"], reverse=True):
        tag = release["tagName"]
        try:
            raw = _gh("release", "download", tag, "--pattern", "lemma-local.json", "--output", "-")
        except subprocess.CalledProcessError:
            # A release with no runtime manifest -- an SDK or CLI release in the
            # same repository -- has nothing to offer, and saying so per tag
            # would bury the one line that matters.
            continue
        try:
            yield tag, json.loads(raw)
        except json.JSONDecodeError:
            print(f"{tag}: lemma-local.json is not JSON; skipped", file=sys.stderr)


def reuse_published_guest(
    *,
    target: str,
    fingerprint: str,
    output: Path,
    manifests: Iterable[tuple[str, dict[str, object]]],
    download: Callable[[str, str, Path], None],
) -> str | None:
    """Put a published guest archive with this fingerprint at `output`.

    Returns the tag it came from, or None when there is none -- or when the
    one there is does not match its own manifest, which is reported and then
    treated the same way: the caller builds.
    """
    found = reusable_guest(manifests, target, fingerprint)
    if found is None:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        download(found.tag, output.name, Path(scratch))
        fetched = Path(scratch) / output.name
        if fetched.stat().st_size != found.size or _sha256_file(fetched) != found.sha256:
            print(
                f"{found.tag}/{output.name} does not match its manifest; building instead",
                file=sys.stderr,
            )
            return None
        os.replace(fetched, output)
    return found.tag


def _gh_download(tag: str, name: str, directory: Path) -> None:
    _gh("release", "download", tag, "--pattern", name, "--dir", str(directory))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    fingerprint = commands.add_parser("fingerprint", help="print the guest input fingerprint")
    fingerprint.add_argument("--target", required=True)
    fingerprint.add_argument("--guestd", type=Path, required=True)
    fingerprint.add_argument("--package-epoch", required=True)

    reuse = commands.add_parser(
        "reuse", help="fetch a published guest built from the same inputs"
    )
    reuse.add_argument("--target", required=True)
    reuse.add_argument("--fingerprint", required=True)
    reuse.add_argument("--output", type=Path, required=True)
    reuse.add_argument("--releases", type=int, default=40)

    archive = commands.add_parser("zip", help="write a deterministic archive")
    archive.add_argument("--source", type=Path, required=True)
    archive.add_argument("--output", type=Path, required=True)
    archive.add_argument("--compresslevel", type=int, default=6)

    sidecar = commands.add_parser("guest-sidecar", help="describe a guest archive")
    sidecar.add_argument("--archive", type=Path, required=True)
    sidecar.add_argument("--fingerprint", required=True)

    args = parser.parse_args(argv)
    if args.command == "fingerprint":
        print(
            guest_fingerprint(
                target=args.target, guestd=args.guestd, package_epoch=args.package_epoch
            )
        )
        return 0
    if args.command == "reuse":
        tag = reuse_published_guest(
            target=args.target,
            fingerprint=args.fingerprint,
            output=args.output,
            manifests=_published_manifests(args.releases),
            download=_gh_download,
        )
        if tag is None:
            print(f"no published {args.target} guest has fingerprint {args.fingerprint}")
            return 1
        print(f"reused {args.output.name} from {tag}")
        return 0
    if args.command == "zip":
        write_deterministic_zip(args.source, args.output, compresslevel=args.compresslevel)
        return 0
    description = guest_sidecar(args.archive, args.fingerprint)
    args.archive.with_suffix(args.archive.suffix + ".json").write_text(
        json.dumps(description, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
