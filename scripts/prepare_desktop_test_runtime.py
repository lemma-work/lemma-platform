#!/usr/bin/env python3
"""Prepare a downloaded Actions runtime bundle for Desktop installer testing."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def localize_artifacts(
    manifest: dict[str, Any],
    artifacts_root: Path,
) -> dict[str, Any]:
    localized = json.loads(json.dumps(manifest))
    groups = (
        ("host_packs", "lemma-host-pack-{target}.zip"),
        ("guest_runtimes", "lemma-guest-runtime-{target}.zip"),
    )
    for group, pattern in groups:
        entries = localized.get(group)
        if not isinstance(entries, dict) or not entries:
            raise ValueError(f"runtime manifest has no {group}")
        for target, metadata in entries.items():
            if not isinstance(metadata, dict):
                raise ValueError(f"runtime manifest has invalid {group}.{target}")
            archive = artifact_path(
                artifacts_root,
                pattern.format(target=target),
            )
            actual_size = archive.stat().st_size
            actual_sha256 = sha256(archive)
            if (
                metadata.get("size") != actual_size
                or metadata.get("sha256") != actual_sha256
                or metadata.get("format") != "zip"
            ):
                raise ValueError(
                    f"{archive.name} does not match the workflow manifest"
                )
            metadata["url"] = archive.as_uri()
    return localized


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
            "developer-gated file:// installer manifest."
        )
    )
    parser.add_argument(
        "--artifacts-dir",
        required=True,
        type=Path,
        help="Directory produced by `gh run download`.",
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
    if manifest.get("version") != desktop_version():
        raise ValueError(
            "runtime version "
            f"{manifest.get('version')!r} does not match Desktop "
            f"{desktop_version()!r}"
        )
    localized = localize_artifacts(manifest, root)
    output = (
        args.output.expanduser().resolve()
        if args.output
        else root / "lemma-local.local.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(localized, indent=2) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
