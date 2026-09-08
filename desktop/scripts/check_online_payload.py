"""Shared size gate for online desktop builds, including every native helper."""

import argparse
import json
from pathlib import Path


MAX_ONLINE_BYTES = 40 * 1024 * 1024


def checked_files(root: Path, paths: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        if not path.exists():
            raise ValueError(f"Online payload is missing: {path}")
        candidates = path.rglob("*") if path.is_dir() else [path]
        for candidate in candidates:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise ValueError(f"Online payload points outside its root: {candidate}")
            if resolved.is_file():
                files.add(resolved)
    return sorted(files)


def windows_payload(root: Path) -> list[Path]:
    platform = json.loads((root / "tauri.windows.conf.json").read_text())
    online = json.loads((root / "tauri.online.conf.json").read_text())
    helpers = platform["bundle"]["externalBin"]
    resources = online["bundle"]["resources"]
    if not isinstance(helpers, list) or not all(isinstance(p, str) for p in helpers):
        raise ValueError("Windows sidecars must be a list of paths")
    if not isinstance(resources, dict) or not all(
        isinstance(p, str) for p in resources
    ):
        raise ValueError("Online resources must be a path mapping")
    paths = [root / "target/release/lemma-desktop.exe"]
    paths.extend(root / f"{helper}-x86_64-pc-windows-msvc.exe" for helper in helpers)
    paths.extend(root / resource for resource in resources)
    return checked_files(root, paths)


def macos_payload(app: Path) -> list[Path]:
    for relative in [
        "Contents/MacOS/lemma-desktop",
        "Contents/MacOS/lemma-locald",
        "Contents/MacOS/lemma-agent-host",
        "Contents/MacOS/lemma-runtime",
        "Contents/Resources/lemma-vz",
        "Contents/Resources/lemma-local.json",
    ]:
        if not (app / relative).is_file():
            raise ValueError(f"Online payload is missing: {relative}")
    return checked_files(app, [app])


def check_size(files: list[Path]) -> int:
    size = sum(path.stat().st_size for path in files)
    if size > MAX_ONLINE_BYTES:
        raise ValueError(
            f"Online application payload is {size} bytes; limit is {MAX_ONLINE_BYTES} bytes (40 MiB)"
        )
    return size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--windows-root", type=Path)
    target.add_argument("--macos-app", type=Path)
    args = parser.parse_args()
    try:
        files = (
            windows_payload(args.windows_root)
            if args.windows_root
            else macos_payload(args.macos_app)
        )
        size = check_size(files)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")
    print(
        f"Online payload verified: {len(files)} files, {size} bytes; budget {MAX_ONLINE_BYTES} bytes"
    )


if __name__ == "__main__":
    main()
