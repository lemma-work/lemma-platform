#!/usr/bin/env python3
"""Ratchet the size of the desktop crates' Rust files.

`docs/engineering/design.md` DES-09 says no file over 600 lines, and names the
failure it prevents: every service in this repo that passed roughly 600 lines
also acquired a state bug that a reviewer had to reconstruct the whole file to
find. The gate that enforces it -- `check_architecture.py` -- reads Python
only, so the desktop crates were never measured, and `main.rs` reached 11,297
lines without anything objecting.

A ratchet rather than a hard limit, for the reason the standards document
gives: the debt is real and rewriting it at once is not a plan. What this
forbids is *more* of it. A file already over the limit may only shrink; a file
that is not over it may not cross it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MAX_FILE_LINES = 600
ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
BASELINE = Path(__file__).with_name("file-size-baseline.json")


def measure() -> dict[str, int]:
    """Every Rust file under desktop/ that is over the limit, and by how much."""
    sizes: dict[str, int] = {}
    for path in sorted(DESKTOP.rglob("*.rs")):
        if "target" in path.parts:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MAX_FILE_LINES:
            sizes[path.relative_to(ROOT).as_posix()] = lines
    return sizes


def check() -> int:
    current = measure()
    baseline: dict[str, int] = json.loads(BASELINE.read_text(encoding="utf-8"))

    grown = {
        path: (baseline[path], size)
        for path, size in current.items()
        if path in baseline and size > baseline[path]
    }
    added = {path: size for path, size in current.items() if path not in baseline}

    if grown or added:
        print("Rust file-size ratchet failed:")
        for path, (was, now) in sorted(grown.items()):
            print(f"- grew past its baseline: {path} ({was} -> {now})")
        for path, size in sorted(added.items()):
            print(f"- new file over {MAX_FILE_LINES} lines: {path} ({size})")
        print(
            "\nDES-09: split it by use case, not by layer. If a file genuinely "
            "shrank elsewhere and this is a re-record, run with --update-baseline."
        )
        return 1

    shrunk = sorted(set(baseline) - set(current))
    if shrunk:
        print(
            f"✓ Rust file size: {len(current)} over {MAX_FILE_LINES} lines "
            f"({len(shrunk)} baselined file(s) now under it — "
            "run --update-baseline to lock that in)"
        )
        return 0
    print(f"✓ Rust file size: no growth ({len(current)} baselined over {MAX_FILE_LINES})")
    return 0


def update() -> int:
    BASELINE.write_text(json.dumps(measure(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Recorded {len(measure())} file(s) over {MAX_FILE_LINES} lines.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-baseline", action="store_true")
    arguments = parser.parse_args()
    return update() if arguments.update_baseline else check()


if __name__ == "__main__":
    raise SystemExit(main())
