#!/usr/bin/env python3
"""Balance the backend e2e shards from measured test time.

The shard layout used to be hand-written in `.github/workflows/e2e.yml`, and it
drifted: the slowest shard ran 467s while the fastest ran 172s, so more than
half of the fan-out sat idle waiting for one runner. This script reads the
JUnit XML every shard already uploads and re-packs the modules so the shards
finish together.

Usage:

    # from the artifacts of a green run
    python scripts/plan_e2e_shards.py --run-id 32460493224

    # or from an already-downloaded directory of junit-*.xml
    python scripts/plan_e2e_shards.py --junit-dir /tmp/e2e-junit

The result is written to `.github/e2e-shards.json`, which the workflow reads
with `fromJSON`. Regenerating it is a reviewable diff on purpose: shard balance
is a fact about the suite, and a fact about the suite belongs in the tree
rather than in a step that recomputes it invisibly on every run.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / ".github" / "e2e-shards.json"

# Marker filter for the ordinary fast lane. Kept byte-identical to the string
# the workflow falls back to so the two cannot drift apart.
FAST_MARKERS = (
    "e2e and not slow and not workspace and not provider "
    "and not local_cli and not indexing and not protected"
)
# The real-Docker lane keeps `workspace` tests in: function execution and
# workspace execution are the things under test there and neither has an
# in-process fake that proves the runtime contract.
SANDBOX_MARKERS = (
    "e2e and not slow and not provider and not local_cli "
    "and not indexing and not protected"
)

# Groups that cannot be bin-packed, and why. Each runs as its own shard with a
# pinned worker count; the packer never puts anything else in them.
PINNED = [
    {
        "name": "sandbox",
        "dirs": [
            "app/modules/function/tests/e2e",
            "app/modules/workspace/tests/e2e",
        ],
        "workers": 1,
        "markers": SANDBOX_MARKERS,
        "needs_sandbox_images": True,
        # Two xdist workers each cold-provisioning a real Docker sandbox raced
        # for the runner's four cores and blew the runtime-endpoint-ready
        # deadline. Confirmed on the CI runner, not just under local
        # contention. Merged into one shard because these are the only two
        # groups that need the built images, and splitting them meant paying
        # the same image build twice against one racing cache key.
        "why": "real Docker sandbox provisioning contends for the runner's CPU",
    },
    {
        "name": "agent",
        "dirs": ["app/modules/agent/tests/e2e"],
        "workers": 1,
        "markers": FAST_MARKERS,
        "needs_sandbox_images": False,
        # Under xdist the production worker subprocess's coverage files race
        # and are lost, swinging the agent e2e-union floor by several points
        # run to run. Raising this needs the coverage race fixed first.
        "why": "worker-subprocess coverage files race under xdist",
    },
]

# Everything else is packed into this many shards at this many xdist workers.
# Five total shards keeps every bin under the pinned `sandbox` shard's
# wall-clock, which is the floor for the whole workflow, while staying well
# inside the free plan's twenty-concurrent-job budget.
PACKED_SHARDS = 3
PACKED_WORKERS = 3

# The catch-all shard collects these roots and ignores every directory that was
# explicitly assigned, so a new module's e2e tests land somewhere by default
# instead of being silently skipped.
CATCH_ALL_ROOTS = ["app/modules", "app/core"]

CLASSNAME = re.compile(r"(app\.(?:modules\.\w+|core(?:\.\w+)*?))\.tests\.e2e")

# A packed shard is named after its heaviest module. Two of those names read
# badly as check names, so they get a shorter one.
SHARD_ALIASES = {"agent_surfaces": "surfaces", "pod_bundle": "bundle"}


def _download_junit(run_id: str, into: Path) -> None:
    if not shutil.which("gh"):
        sys.exit("gh is required to download artifacts; pass --junit-dir instead")
    listing = subprocess.run(
        [
            "gh", "api",
            f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/artifacts",
            "-q", ".artifacts[].name",
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    shards = [n for n in listing if n.startswith("backend-e2e-")]
    if not shards:
        sys.exit(f"run {run_id} has no backend-e2e-*-coverage artifacts")
    for name in shards:
        subprocess.run(
            ["gh", "run", "download", run_id, "-n", name, "-D", str(into / name)],
            cwd=REPO_ROOT, check=True,
        )


def _measure(junit_dir: Path) -> tuple[Counter, Counter]:
    """Sum test time and test count per module e2e directory."""
    seconds: Counter = Counter()
    tests: Counter = Counter()
    files = list(junit_dir.rglob("junit-*.xml"))
    if not files:
        sys.exit(f"no junit-*.xml under {junit_dir}")
    for path in files:
        for case in ET.parse(path).getroot().iter("testcase"):
            match = CLASSNAME.match(case.get("classname", ""))
            if not match:
                continue
            key = match.group(1).replace(".", "/") + "/tests/e2e"
            seconds[key] += float(case.get("time") or 0)
            tests[key] += 1
    return seconds, tests


def _pack(weights: list[tuple[str, float]], bins: int) -> list[list[str]]:
    """Longest-processing-time-first bin packing.

    Greedy LPT is within 4/3 of optimal and, unlike an exact solver, produces
    the same answer every time it is run on the same input -- which matters
    more here than the last few percent, because the output is committed.
    """
    loads = [[0.0, []] for _ in range(bins)]
    for name, weight in sorted(weights, key=lambda item: -item[1]):
        target = min(loads, key=lambda entry: entry[0])
        target[0] += weight
        target[1].append(name)
    return [entry[1] for entry in loads]



def _collectors(directory: str, shards: list[dict]) -> list[str]:
    """Which shards would collect `directory`, by the same rules pytest uses."""
    hits = []
    for shard in shards:
        args = shard["args"].split()
        ignored = [a.split("=", 1)[1] for a in args if a.startswith("--ignore=")]
        roots = [a for a in args if not a.startswith("--")]
        if any(directory == i or directory.startswith(i + "/") for i in ignored):
            continue
        if any(directory == r or directory.startswith(r + "/") for r in roots):
            hits.append(shard["name"])
    return hits


def verify() -> int:
    """Assert every e2e directory on disk lands in exactly one shard.

    This is the one mistake in the layout that is invisible: a shard that
    silently collects nothing still reports success, so a new module's e2e
    tests can stop running without anything going red. Static and sub-second,
    so it belongs in `make quality` rather than in a job.
    """
    if not OUTPUT.exists():
        print(f"::error::{OUTPUT.relative_to(REPO_ROOT)} is missing")
        return 1
    shards = json.loads(OUTPUT.read_text())["shards"]
    backend = REPO_ROOT / "lemma-backend"
    on_disk = sorted(
        str(path.relative_to(backend))
        for path in backend.glob("app/**/tests/e2e")
        if path.is_dir()
    )
    problems = []
    for directory in on_disk:
        hits = _collectors(directory, shards)
        if len(hits) != 1:
            problems.append((directory, hits))
    for directory, hits in problems:
        where = ", ".join(hits) if hits else "no shard"
        print(f"::error::{directory} is collected by {where}; expected exactly one")
    if problems:
        print(f"\nRegenerate with: python scripts/plan_e2e_shards.py --run-id <green run>")
        return 1
    print(f"{len(on_disk)} e2e directories, each in exactly one of "
          f"{len(shards)} shards")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-id", help="a green 'Backend E2E' run to measure")
    source.add_argument("--junit-dir", type=Path, help="directory of junit-*.xml")
    source.add_argument("--verify", action="store_true",
                        help="check the committed layout still covers every e2e "
                             "directory on disk (needs no timing data)")
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed layout is not what this produces")
    args = parser.parse_args()

    if args.verify:
        return verify()

    with tempfile.TemporaryDirectory() as tmp:
        junit_dir = args.junit_dir
        if args.run_id:
            junit_dir = Path(tmp)
            _download_junit(args.run_id, junit_dir)
        seconds, tests = _measure(junit_dir)

    pinned_dirs = {d for group in PINNED for d in group["dirs"]}
    packable = [(d, s) for d, s in seconds.items() if d not in pinned_dirs]
    bins = _pack(packable, PACKED_SHARDS)
    # The catch-all goes to the lightest bin, since collecting the whole tree
    # to filter it costs a little on top of the tests it actually runs.
    bins.sort(key=lambda group: sum(seconds[d] for d in group))
    assigned = sorted(pinned_dirs.union(*(set(b) for b in bins)))

    shards = []
    for group in PINNED:
        shards.append({
            "name": group["name"],
            "args": " ".join(sorted(group["dirs"])),
            "workers": group["workers"],
            "markers": group["markers"],
            "needs_sandbox_images": group["needs_sandbox_images"],
            "why_serial": group["why"],
            "serial_seconds": round(sum(seconds[d] for d in group["dirs"]), 1),
            "tests": sum(tests[d] for d in group["dirs"]),
        })
    for index, group in enumerate(bins):
        heaviest = max(group, key=lambda d: seconds[d])
        name = heaviest.split("/")[2] if heaviest.startswith("app/modules") else "core"
        name = SHARD_ALIASES.get(name, name)
        catch_all = index == 0
        if catch_all:
            arg_list = CATCH_ALL_ROOTS + [f"--ignore={d}" for d in assigned
                                          if d not in group]
        else:
            arg_list = sorted(group)
        shards.append({
            "name": name,
            "args": " ".join(arg_list),
            "workers": PACKED_WORKERS,
            "markers": FAST_MARKERS,
            "needs_sandbox_images": False,
            "catch_all": catch_all,
            "serial_seconds": round(sum(seconds[d] for d in group), 1),
            "tests": sum(tests[d] for d in group),
        })

    payload = {
        "_comment": "Generated by scripts/plan_e2e_shards.py. Do not hand-edit; "
                    "regenerate from a green Backend E2E run instead.",
        "measured": {
            "serial_seconds": round(sum(seconds.values()), 1),
            "tests": sum(tests.values()),
            "directories": len(seconds),
        },
        "shards": shards,
    }
    rendered = json.dumps(payload, indent=2) + "\n"

    if args.check:
        current = OUTPUT.read_text() if OUTPUT.exists() else ""
        if current != rendered:
            print("::error::.github/e2e-shards.json is stale; regenerate it")
            return 1
        return 0

    OUTPUT.write_text(rendered)
    widest = max(s["serial_seconds"] for s in shards)
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    for shard in shards:
        bar = "#" * round(shard["serial_seconds"] / widest * 40)
        print(f"  {shard['name']:<10} {shard['serial_seconds']:>7.1f}s "
              f"{shard['tests']:>4d}t n={shard['workers']} {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
