#!/usr/bin/env python3
"""Run one CI e2e shard locally, exactly as CI runs it.

`.github/e2e-shards.json` holds each shard's paths, worker count *and* marker
filter, and the workflow reads all three from it. Reproducing a shard by hand
means copying all three, and copying the marker filter is the part that goes
wrong quietly.

Two of the seven shards -- `sandbox` and `sandbox-2` -- run with a different
filter from the rest: they keep `workspace` tests in, because function and
workspace execution against real Docker are the things those shards exist to
prove. `make test-e2e-shard` used to hard-code the *other* filter, the one that
excludes `workspace`, and ignore the `E2E_SHARD_MARKERS` variable it appeared to
accept. So a developer reproducing the `sandbox` shard locally silently ran a
strictly smaller set than CI: every Docker-sandbox test was deselected, the run
went green, and the shard failed on the pull request anyway.

Naming the shard instead of the filter removes the copying, and with it the
class of mistake:

    make -C lemma-backend test-e2e-shard-ci SHARD=sandbox
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARDS = REPO_ROOT / ".github" / "e2e-shards.json"
BACKEND = REPO_ROOT / "lemma-backend"


def _shards() -> dict[str, dict[str, object]]:
    payload = json.loads(SHARDS.read_text(encoding="utf-8"))
    return {str(shard["name"]): shard for shard in payload["shards"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard", nargs="?", help="shard name, e.g. sandbox")
    parser.add_argument(
        "--list", action="store_true", help="print the shard names and exit"
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="print the pytest command without running it",
    )
    args = parser.parse_args()

    shards = _shards()
    if args.list or not args.shard:
        for name, shard in shards.items():
            print(f"{name:16} workers={shard.get('workers')}  {shard.get('markers')}")
        return 0 if args.list else 2

    shard = shards.get(args.shard)
    if shard is None:
        print(
            f"no shard named {args.shard!r}. Known: {', '.join(shards)}",
            file=sys.stderr,
        )
        return 2

    # `args` is a single string of pytest paths and --ignore flags, the same way
    # the workflow passes it. Splitting on whitespace matches how the shell in
    # the workflow step expands it.
    command = [
        "uv",
        "run",
        "pytest",
        "-n",
        str(shard.get("workers", 1)),
        "--dist",
        "loadscope",
        *str(shard.get("args", "")).split(),
        "-m",
        str(shard["markers"]),
        "-o",
        "timeout=120",
    ]

    if args.print_only:
        print(" ".join(command))
        return 0

    print(f"→ shard {args.shard}: {shard.get('markers')}\n", flush=True)
    return subprocess.run(command, cwd=BACKEND, env=os.environ).returncode


if __name__ == "__main__":
    sys.exit(main())
