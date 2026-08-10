#!/usr/bin/env python3
"""Stage a downloaded Actions runtime bundle for Desktop installer testing.

Desktop ships two shapes of the same manifest. The *online* one names URLs and
is what a release installs from; the app downloads the host pack and the guest
runtime on first launch. The *bundled* one names app resources instead, and the
payloads ride inside the installer — that is the build worth handing someone to
try a branch, because it installs on a machine with no network and against no
published release.

Both CI and `make desktop-dmg` stage the bundled shape, and they call this. That
is the point: a locally built test DMG and CI's are then produced by one piece
of code, so a green local build and a green CI build mean the same thing.

    # bundled: copy the payloads next to a manifest that names them
    prepare_desktop_test_runtime.py --artifacts-dir desktop/runtime/download \
        --mode bundled --host-target aarch64-apple-darwin \
        --guest-target macos-aarch64 --stage-dir desktop/runtime/bundled \
        --output desktop/runtime/bundled/lemma-local.json

    # file-url: leave the payloads where they are and point at them
    prepare_desktop_test_runtime.py --artifacts-dir /tmp/run-artifacts
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

ARTIFACT_GROUPS = (
    ("host_packs", "lemma-host-pack-{target}.zip", "host-runtime.zip"),
    ("guest_runtimes", "lemma-guest-runtime-{target}.zip", "guest-runtime.zip"),
)

# Tauri copies `resources` into the bundle by name. A stray extracted tree left
# in desktop/runtime would be copied in beside them and silently ship, so the
# staging step refuses rather than producing a quietly enormous installer.
STRAY_TREES = ("local-runtime", "managed-runtime")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_path(root: Path, name: str) -> Path:
    matches = sorted(path for path in root.rglob(name) if path.is_file())
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name}, found {len(matches)}")
    return matches[0].resolve()


def verify_artifacts(
    manifest: dict[str, Any],
    artifacts_root: Path,
    targets: dict[str, str] | None = None,
) -> dict[tuple[str, str], Path]:
    """Locate each archive the manifest names and prove it is the right bytes.

    `targets` restricts a group to one target; without it every target the
    manifest lists must be present, which is what the file-url path wants.
    """
    located: dict[tuple[str, str], Path] = {}
    for group, pattern, _resource in ARTIFACT_GROUPS:
        entries = manifest.get(group)
        if not isinstance(entries, dict) or not entries:
            raise ValueError(f"runtime manifest has no {group}")
        wanted = entries
        if targets and group in targets:
            target = targets[group]
            if target not in entries:
                raise ValueError(f"runtime manifest has no {group}.{target}")
            wanted = {target: entries[target]}
        for target, metadata in wanted.items():
            if not isinstance(metadata, dict):
                raise ValueError(f"runtime manifest has invalid {group}.{target}")
            archive = artifact_path(artifacts_root, pattern.format(target=target))
            if (
                metadata.get("size") != archive.stat().st_size
                or metadata.get("sha256") != sha256(archive)
                or metadata.get("format") != "zip"
            ):
                raise ValueError(f"{archive.name} does not match the workflow manifest")
            located[(group, target)] = archive
    return located


def localize_artifacts(
    manifest: dict[str, Any],
    artifacts_root: Path,
) -> dict[str, Any]:
    """Point every entry at the downloaded file with a `file://` URL.

    Loading one is gated at runtime behind LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS
    plus an explicit LEMMA_DESKTOP_RELEASE_MANIFEST (see
    desktop/src/artifact_install.rs), because a manifest that can name a local
    path is a manifest that can be pointed at anything on the disk.
    """
    localized = json.loads(json.dumps(manifest))
    located = verify_artifacts(localized, artifacts_root)
    for (group, target), archive in located.items():
        localized[group][target]["url"] = archive.as_uri()
    return localized


def bundle_artifacts(
    manifest: dict[str, Any],
    artifacts_root: Path,
    host_target: str,
    guest_target: str,
    stage_dir: Path,
) -> dict[str, Any]:
    """Copy the payloads into the bundle and name them as app resources.

    A bundled build needs no `file://` gate: the payload is inside the signed
    app, so the manifest carries a resource name and no URL at all. Each group
    is narrowed to the one target being packaged — shipping a Windows guest
    runtime inside a macOS DMG would just be several hundred megabytes of dead
    weight.
    """
    bundled = json.loads(json.dumps(manifest))
    targets = {"host_packs": host_target, "guest_runtimes": guest_target}
    located = verify_artifacts(bundled, artifacts_root, targets)

    stage_dir.mkdir(parents=True, exist_ok=True)
    for group, _pattern, resource in ARTIFACT_GROUPS:
        target = targets[group]
        entry = bundled[group][target]
        shutil.copyfile(located[(group, target)], stage_dir / resource)
        bundled[group] = {target: {**entry, "url": None, "resource": resource}}
    return bundled


def reject_stray_runtime_trees() -> None:
    for stray in STRAY_TREES:
        if (REPO_ROOT / "desktop/runtime" / stray).exists():
            raise ValueError(
                f"desktop/runtime/{stray} exists and would be bundled as a stray "
                "tree; remove it before packaging"
            )


def desktop_version() -> str:
    config = json.loads((REPO_ROOT / "desktop/tauri.conf.json").read_text())
    version = config.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("desktop/tauri.conf.json has no version")
    return version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a downloaded lemma-local-test Actions artifact into a "
            "manifest Desktop can be built against."
        )
    )
    parser.add_argument(
        "--artifacts-dir",
        required=True,
        type=Path,
        help="Directory produced by `gh run download`.",
    )
    parser.add_argument(
        "--mode",
        choices=("file-url", "bundled"),
        default="file-url",
        help=(
            "file-url points at the downloaded files for a developer-gated "
            "install; bundled stages them as app resources for a "
            "self-contained installer."
        ),
    )
    parser.add_argument(
        "--host-target",
        help="Host pack target triple to bundle (bundled mode).",
    )
    parser.add_argument(
        "--guest-target",
        help="Guest runtime target to bundle (bundled mode).",
    )
    parser.add_argument(
        "--stage-dir",
        type=Path,
        help="Where to copy the payloads (bundled mode).",
    )
    parser.add_argument(
        "--expect-version",
        help="Fail unless the manifest declares this version (a leading v is fine).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path (default: <artifacts-dir>/lemma-local.local.json).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.artifacts_dir.expanduser().resolve()
    source = artifact_path(root, "lemma-local.json")
    manifest = json.loads(source.read_text())

    # The runtime and the shell are released together and the app asserts they
    # agree, so a mismatch here is a packaging mistake, not a warning.
    expected = (args.expect_version or desktop_version()).removeprefix("v")
    if manifest.get("version") != expected:
        raise ValueError(
            f"runtime version {manifest.get('version')!r} does not match "
            f"Desktop {expected!r}"
        )

    if args.mode == "bundled":
        missing = [
            name
            for name, value in (
                ("--host-target", args.host_target),
                ("--guest-target", args.guest_target),
                ("--stage-dir", args.stage_dir),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"bundled mode requires {', '.join(missing)}")
        reject_stray_runtime_trees()
        staged = bundle_artifacts(
            manifest,
            root,
            args.host_target,
            args.guest_target,
            args.stage_dir.expanduser().resolve(),
        )
        default_output = args.stage_dir / "lemma-local.json"
    else:
        staged = localize_artifacts(manifest, root)
        default_output = root / "lemma-local.local.json"

    output = (args.output or default_output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(staged, indent=2) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
