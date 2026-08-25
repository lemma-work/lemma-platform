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
from typing import NamedTuple

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
        # Split across runners rather than across xdist workers. The reason
        # above is about two workers inside *one* four-vCPU runner; separate
        # jobs each get their own, so the parallelism is free of the
        # contention. One bin here keeps today's layout; raise it once the
        # 200s file inside this group has been split.
        "bins": 1,
    },
    {
        "name": "agent",
        "dirs": ["app/modules/agent/tests/e2e"],
        "workers": 1,
        "markers": FAST_MARKERS,
        # True, even though the agent module is not the sandbox module. Its
        # `fast_workspace` journeys provision a real Docker workspace, and
        # conftest exempts them from the `workspace` marker precisely so they
        # stay in the fast lane -- so this shard needs the image too. Without
        # this it still got one: the `workspace_image` fixture built it on
        # demand, inside the test step, uncached, and invisibly (pytest
        # captures the build output, so the job log shows no build at all). The
        # sandbox shard pays 79s to build and 27s to restore from cache; this
        # shard was paying the 79s every run with nothing to show for it.
        "needs_sandbox_images": True,
        # MEASURED 2026-08-22, kept. The original reason -- "worker-subprocess
        # coverage files race under xdist" -- is false: .coveragerc sets
        # parallel=true and relative_files=true, so filenames cannot collide.
        # Four runs of this shard: workers=1 gave agent 77.0% in 192s;
        # workers=2 gave 76.9%, 77.2%, 76.8% in 116s, 145s, 129s. The mean is
        # identical (76.97 vs 77.0) and the variance is symmetric -- xdist
        # landed above the serial number as often as below -- so there is no
        # systematic coverage loss. But the per-run spread is 0.4 points
        # against a 0.2 bar set before measuring, so this is not evidence
        # enough to flip. ~62s of shard time is the prize; re-measure on a
        # quiet machine before taking it.
        #
        # This note lived only in the generated JSON, where the next
        # regeneration would have deleted it and restored the false claim
        # above it. It belongs here, in the input.
        "why": (
            "MEASURED 2026-08-22: xdist coverage race disproven, but the "
            "per-run spread is 0.4pt against a 0.2pt bar -- re-measure before "
            "raising"
        ),
        "bins": 1,
    },
]

# What a shard pays before its first test runs: checkout, uv sync, image
# restore, container startup, collection. From CI job/step timings on
# 2026-08-25 (sandbox 74+60, packed 36+76).
#
# Good to about +-20s and no better. The image lanes vary most, because
# `docker load` of the 2.5GB workspace tar measured 27s on one shard and 40s on
# another in the same run. So treat `estimated_seconds` as "which shard is the
# wall", not as a prediction -- it is here because balancing `serial_seconds`
# balances the wrong thing. Shards finish together or they do not, and a serial
# figure divided by different worker counts, on top of different fixed costs,
# says nothing about when.
FIXED_SECONDS = {True: 134.0, False: 112.0}  # keyed by needs_sandbox_images

# Everything else is packed into this many shards at this many xdist workers.
# Five total shards keeps every bin under the pinned `sandbox` shard's
# wall-clock, which is the floor for the whole workflow, while staying well
# inside the free plan's twenty-concurrent-job budget.
PACKED_SHARDS = 3
# Two, not three. Three was generalised from the one shard ever measured at it
# -- the old `pod` shard, 94 tests of pure API work -- and it does not hold for
# a packed bin. Each xdist worker runs its own SuperTokens container and its
# own streaq worker subprocess, so three of them on a four-vCPU runner starve
# the async workers the tests are waiting on. It shows up as a *condition*
# wait timing out with a generous budget rather than as a slow assert:
# test_apply_destructive_requires_confirmation blew a 90-second wait for a
# bundle import, intermittently -- green on one run, red on the next with the
# same shard contents.
#
# This costs no wall-clock. The `sandbox` shard runs serially at ~550s and is
# the floor for the whole workflow; a packed bin at two workers lands near
# 420s, still comfortably underneath it.
PACKED_WORKERS = 2

# The catch-all shard collects these roots and ignores every directory that was
# explicitly assigned, so a new module's e2e tests land somewhere by default
# instead of being silently skipped.
CATCH_ALL_ROOTS = ["app/modules", "app/core"]

# Captures the e2e directory *and* the test module inside it. It used to stop
# at `.tests.e2e`, which threw the module away and left the packer able to
# balance only whole directories. That was the ceiling on the whole layout: one
# 200s file inside a 310s directory could not be split from it, so the shard it
# sat in was the floor for the workflow no matter how the rest was arranged.
# The trailing `\w+` stops at the first dot, so a class-based test
# (`...test_records_e2e.TestDatastoreRlsRows`) keys on the module, not the class.
CLASSNAME = re.compile(r"(app\.(?:modules\.\w+|core(?:\.\w+)*?))\.tests\.e2e\.(\w+)")

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


class Measured(NamedTuple):
    """Test time and count, keyed both by e2e directory and by test file.

    Both granularities come from the same pass because the packer needs both:
    directories are the unit it prefers (short `args`, and a new file inside one
    lands somewhere by default), and files are the escape hatch for a directory
    too heavy to be one bin.
    """

    seconds: Counter
    tests: Counter
    file_seconds: Counter
    file_tests: Counter

    def files_in(self, directory: str) -> list[str]:
        return sorted(
            path for path in self.file_seconds
            if path.startswith(directory + "/")
        )

    def weight(self, path: str) -> float:
        return self.file_seconds[path] if path.endswith(".py") else self.seconds[path]

    def count(self, path: str) -> int:
        return self.file_tests[path] if path.endswith(".py") else self.tests[path]


def _measure(junit_dir: Path) -> Measured:
    """Sum test time and test count per e2e directory and per test file."""
    seconds: Counter = Counter()
    tests: Counter = Counter()
    file_seconds: Counter = Counter()
    file_tests: Counter = Counter()
    files = list(junit_dir.rglob("junit-*.xml"))
    if not files:
        sys.exit(f"no junit-*.xml under {junit_dir}")
    for path in files:
        for case in ET.parse(path).getroot().iter("testcase"):
            match = CLASSNAME.match(case.get("classname", ""))
            if not match:
                continue
            key = match.group(1).replace(".", "/") + "/tests/e2e"
            elapsed = float(case.get("time") or 0)
            seconds[key] += elapsed
            tests[key] += 1
            file_key = f"{key}/{match.group(2)}.py"
            file_seconds[file_key] += elapsed
            file_tests[file_key] += 1
    return Measured(seconds, tests, file_seconds, file_tests)


def _module_of(path: str) -> str:
    """`app/modules/pod/tests/e2e/test_x.py` -> `pod`; anything else -> `core`."""
    parts = path.split("/")
    return parts[2] if path.startswith("app/modules/") and len(parts) > 2 else "core"


def _session_worker_conflict(backend: Path) -> tuple[set[str], set[str]]:
    """Modules that own a session streaq worker, and modules that use the base one.

    Two streaq workers in one pytest process consume from the same per-xdist
    Redis database, and `production_worker_process` calls `flushdb` on entry --
    so the second to start wipes the first one's queue. `agent_surfaces`
    legitimately overrides the base `worker` fixture, so it must never share a
    bin with a module that uses the base one.

    Until now this was only a tripwire: a unit test rejected an illegal layout
    after the packer had already produced it, and the layout that shipped
    avoided the collision by luck. At file granularity the packer gets many more
    chances to pair them, so it now avoids the collision by construction. The
    unit test stays as the backstop.
    """
    def module(path: Path) -> str:
        # From the front (`app/modules/<name>/...`). The nested glob below
        # reaches files at more than one depth, so counting backwards from the
        # filename returns "e2e" for anything in a subpackage.
        return path.relative_to(backend).parts[2]

    owners = {
        module(path)
        for path in backend.glob("app/modules/*/tests/e2e/conftest.py")
        if "async def worker" in path.read_text()
    }
    # Requesting it as a fixture, not merely mentioning the word: "worker"
    # appears in comments and docstrings all over this suite.
    requests = re.compile(r"(?m)^\s+worker[,:)]|pytest\.mark\.worker")
    base_users = {
        module(path)
        for path in backend.glob("app/modules/*/tests/e2e/**/test_*.py")
        if requests.search(path.read_text())
    } - owners
    return owners, base_users


def _pack(
    weights: list[tuple[str, float]],
    bins: int,
    conflict: tuple[set[str], set[str]] = (frozenset(), frozenset()),
) -> list[list[str]]:
    """Longest-processing-time-first bin packing, honouring one conflict rule.

    Greedy LPT is within 4/3 of optimal and, unlike an exact solver, produces
    the same answer every time it is run on the same input -- which matters
    more here than the last few percent, because the output is committed.

    `conflict` is `(owners, base_users)` from `_session_worker_conflict`: an
    item from either side never lands in a bin already holding the other.
    """
    owners, base_users = conflict
    loads: list[list] = [[0.0, [], set()] for _ in range(bins)]

    def _allowed(entry: list, module: str) -> bool:
        held = entry[2]
        if module in owners:
            return not (held & base_users)
        if module in base_users:
            return not (held & owners)
        return True

    for name, weight in sorted(weights, key=lambda item: (-item[1], item[0])):
        module = _module_of(name)
        candidates = [entry for entry in loads if _allowed(entry, module)]
        if not candidates:
            sys.exit(
                f"::error::cannot place {name}: every bin already holds a "
                "module whose session worker would fight it. Add a bin, or pin "
                "the module into its own shard."
            )
        target = min(candidates, key=lambda entry: entry[0])
        target[0] += weight
        target[1].append(name)
        target[2].add(module)
    return [entry[1] for entry in loads]


def _explode(paths: list[str], measured: Measured, target: float) -> list[str]:
    """Replace directories heavier than `target` with the test files inside them.

    Mixed granularity on purpose. Directories are the better unit -- `args` stay
    readable, and a newly added `test_*.py` is collected by whichever shard
    holds its directory instead of by nobody. So a directory is only broken open
    when it is too heavy to be one bin, which today is true of exactly one.
    """
    out = []
    for path in paths:
        files = measured.files_in(path)
        if measured.seconds[path] > target and len(files) > 1:
            out.extend(files)
        else:
            out.append(path)
    return out


def _render_args(members: list[str], roots: list[str], given_away: list[str]) -> str:
    """Args for a shard: explicit members, or roots minus what went elsewhere.

    The roots form is what keeps a new test file from landing in no shard at
    all. Exactly one shard per group uses it; the rest name their members.
    """
    if roots:
        return " ".join(roots + [f"--ignore={path}" for path in sorted(given_away)])
    return " ".join(sorted(members))



def _collectors(path: str, shards: list[dict]) -> list[str]:
    """Which shards would collect `path`, by the same rules pytest uses.

    `path` is a directory or a test file; the prefix rules are the same for
    both, since an exact match covers a file named as a root or as an ignore.
    """
    hits = []
    for shard in shards:
        args = shard["args"].split()
        ignored = [a.split("=", 1)[1] for a in args if a.startswith("--ignore=")]
        roots = [a for a in args if not a.startswith("--")]
        if any(path == i or path.startswith(i + "/") for i in ignored):
            continue
        if any(path == r or path.startswith(r + "/") for r in roots):
            hits.append(shard["name"])
    return hits


def verify() -> int:
    """Assert every e2e test file on disk lands in exactly one shard.

    This is the one mistake in the layout that is invisible: a shard that
    silently collects nothing still reports success, so a new module's e2e
    tests can stop running without anything going red. Static and sub-second,
    so it belongs in `make quality` rather than in a job.

    Files, not just directories, because the layout can now split a directory
    across shards -- so "this directory is collected" stopped being the same
    claim as "these tests run". The directory check stays too: a directory that
    no shard collects is the same bug with a much clearer error message, and
    reporting it once beats reporting it once per file inside it.
    """
    if not OUTPUT.exists():
        print(f"::error::{OUTPUT.relative_to(REPO_ROOT)} is missing")
        return 1
    shards = json.loads(OUTPUT.read_text())["shards"]
    backend = REPO_ROOT / "lemma-backend"
    directories = sorted(
        str(path.relative_to(backend))
        for path in backend.glob("app/**/tests/e2e")
        if path.is_dir()
    )
    on_disk = sorted(
        str(path.relative_to(backend))
        for path in backend.glob("app/**/tests/e2e/**/test_*.py")
    )
    problems = []
    for directory in directories:
        # A directory is "collected" if anything under it is. With a split
        # directory the root itself may be named by no shard while every file
        # inside it is, which is correct rather than a problem.
        if not any(_collectors(path, shards)
                   for path in on_disk if path.startswith(directory + "/")):
            problems.append((directory, []))
    for path in on_disk:
        hits = _collectors(path, shards)
        if len(hits) != 1:
            problems.append((path, hits))
    for path, hits in problems:
        where = ", ".join(hits) if hits else "no shard"
        print(f"::error::{path} is collected by {where}; expected exactly one")
    if problems:
        print("\nRegenerate with: python scripts/plan_e2e_shards.py --run-id <green run>")
        return 1
    print(f"{len(on_disk)} e2e test files in {len(directories)} directories, "
          f"each in exactly one of {len(shards)} shards")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-id", help="a green 'Backend E2E' run to measure")
    source.add_argument("--junit-dir", type=Path, help="directory of junit-*.xml")
    source.add_argument("--verify", action="store_true",
                        help="check the committed layout still runs every e2e "
                             "test file on disk (needs no timing data)")
    # There is deliberately no --check. It compared the committed JSON against a
    # freshly rendered one, which meant it could only pass while someone kept
    # regenerating from the newest green run -- so it sat stale for months with
    # nothing running it, and it was sensitive to json.dumps formatting rather
    # than to anything true about the layout. --verify is the invariant that
    # matters and the one CI enforces.
    args = parser.parse_args()

    if args.verify:
        return verify()

    with tempfile.TemporaryDirectory() as tmp:
        junit_dir = args.junit_dir
        if args.run_id:
            junit_dir = Path(tmp)
            _download_junit(args.run_id, junit_dir)
        measured = _measure(junit_dir)

    seconds = measured.seconds
    conflict = _session_worker_conflict(REPO_ROOT / "lemma-backend")

    # (name, members, roots) per shard. `roots` is non-empty for the one shard
    # in each group that collects by directory and subtracts the rest, which is
    # what keeps a newly added test file from landing in no shard at all.
    planned: list[tuple[str, list[str], list[str], dict]] = []

    pinned_dirs = {d for group in PINNED for d in group["dirs"]}
    for group in PINNED:
        dirs = sorted(group["dirs"])
        count = group.get("bins", 1)
        if count == 1:
            planned.append((group["name"], dirs, dirs, group))
            continue
        target = sum(seconds[d] for d in dirs) / count
        members = _explode(dirs, measured, target)
        bins = _pack([(m, measured.weight(m)) for m in members], count, conflict)
        # The roots shard also pays for collecting the directory to filter it,
        # so it goes to the lightest bin.
        bins.sort(key=lambda bin_: sum(measured.weight(m) for m in bin_))
        for index, bin_ in enumerate(bins):
            name = group["name"] if index == 0 else f"{group['name']}-{index + 1}"
            planned.append((name, bin_, dirs if index == 0 else [], group))

    packable_dirs = [d for d in seconds if d not in pinned_dirs]
    packable = _explode(
        sorted(packable_dirs),
        measured,
        sum(seconds[d] for d in packable_dirs) / PACKED_SHARDS,
    )
    bins = _pack([(m, measured.weight(m)) for m in packable], PACKED_SHARDS, conflict)
    # The catch-all goes to the lightest bin, since collecting the whole tree
    # to filter it costs a little on top of the tests it actually runs.
    bins.sort(key=lambda bin_: sum(measured.weight(m) for m in bin_))
    for index, bin_ in enumerate(bins):
        heaviest = max(bin_, key=measured.weight)
        name = SHARD_ALIASES.get(_module_of(heaviest), _module_of(heaviest))
        planned.append((name, bin_, CATCH_ALL_ROOTS if index == 0 else [], None))

    everything = sorted({member for _, members, _, _ in planned for member in members})

    shards = []
    for name, members, roots, group in planned:
        serial = round(sum(measured.weight(m) for m in members), 1)
        workers = group["workers"] if group else PACKED_WORKERS
        needs_images = group["needs_sandbox_images"] if group else False
        shard = {
            "name": name,
            "args": _render_args(
                members,
                roots,
                # A roots shard subtracts everything assigned elsewhere that it
                # would otherwise collect -- for a pinned group that is only its
                # own siblings, for the catch-all it is every other shard.
                [path for path in everything
                 if path not in members
                 and any(path == r or path.startswith(r + "/") for r in roots)],
            ),
            "workers": workers,
            "markers": group["markers"] if group else FAST_MARKERS,
            "needs_sandbox_images": needs_images,
            "serial_seconds": serial,
            "tests": sum(measured.count(m) for m in members),
            # What this shard's job is predicted to take: the fixed cost before
            # its first test plus the work divided by its workers. Balancing
            # `serial_seconds` is not the goal -- shards finish together or they
            # do not, and only this number says which.
            "estimated_seconds": round(FIXED_SECONDS[needs_images] + serial / workers, 1),
        }
        if group:
            shard["why_serial"] = group["why"]
        else:
            shard["catch_all"] = bool(roots)
        shards.append(shard)

    payload = {
        "_comment": "Generated by scripts/plan_e2e_shards.py. Do not hand-edit; "
                    "regenerate from a green Backend E2E run instead.",
        "measured": {
            "serial_seconds": round(sum(seconds.values()), 1),
            "tests": sum(measured.tests.values()),
            "directories": len(seconds),
            "files": len(measured.file_seconds),
        },
        "shards": shards,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n")

    widest = max(s["estimated_seconds"] for s in shards)
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    for shard in shards:
        bar = "#" * round(shard["estimated_seconds"] / widest * 40)
        print(f"  {shard['name']:<12} {shard['serial_seconds']:>7.1f}s serial "
              f"{shard['tests']:>4d}t n={shard['workers']} "
              f"-> ~{shard['estimated_seconds']:>6.1f}s {bar}")
    print(f"  {'':12} {'':>7}  predicted wall clock: ~{widest:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
