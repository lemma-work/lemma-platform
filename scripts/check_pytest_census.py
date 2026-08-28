#!/usr/bin/env python3
"""Prove no test suite has quietly stopped running, or started skipping green.

Two failures this repo has actually had, neither of which any check could see:

* A whole directory was collected by nobody, because `testpaths` did not name
  it. pytest.ini carries the comment: the version-compatibility guard stopped
  running and the API and SDK versions drifted apart with CI green throughout.
* The 17 `local_guest` tests were marked `integration` rather than `e2e`, so
  the unit lane collected all of them and skipped all of them -- and a run that
  skips every test in a suite reports exactly the same "passed" as a run that
  proves something.

Both are the same shape: a number that went to zero where nobody was looking.
So this counts, and compares against a committed floor.

    python3 scripts/check_pytest_census.py            # verify
    python3 scripts/check_pytest_census.py --update   # record a new floor

A floor rather than an exact count, deliberately. Exact counts fail on every
added test, and a check that fails for a good reason ten times a week is a
check people learn to re-run until it passes. Growth is silent, shrinkage is
loud, and shrinkage is the direction the bug lives in.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "lemma-backend"
BASELINE = REPO_ROOT / ".github" / "pytest-census.json"

# Where each unit lane's `-m` expression is declared. Read out of the Makefiles
# rather than restated here, because a second copy of a marker expression is
# exactly how these drifted in the first place: `make coverage-backend-unit`
# exists in *both* files, CI runs the backend one (its job sets
# `working-directory: lemma-backend`), and a fix that excluded `local_guest`
# went into the root copy only. So both are read, and both are checked.
LANE_VARIABLES = {
    "root Makefile": (REPO_ROOT / "Makefile", "UNIT_MARKERS"),
    "lemma-backend/Makefile (what CI runs)": (
        BACKEND / "Makefile",
        "UNIT_MARKERS",
    ),
}


def lanes() -> dict[str, str]:
    """Each lane's `-m` expression, from the Makefile variable it uses."""
    resolved = {}
    for lane, (makefile, variable) in LANE_VARIABLES.items():
        match = re.search(
            rf"^{variable}\s*\??=\s*(.+)$", makefile.read_text(), re.MULTILINE
        )
        if match is None:
            raise SystemExit(f"::error::{makefile} declares no {variable}")
        resolved[lane] = match.group(1).strip()
    return resolved

# Markers meaning "this test needs something a CI runner does not have". A test
# carrying one of these skips at runtime, and a lane that reports "passed" must
# therefore not select it -- otherwise the lane's green covers tests that never
# executed a single assertion. Deselected tests are counted as deselected in
# pytest's own summary line; skipped ones are not distinguished from run ones
# by anything a person reads.
ENVIRONMENT_GATED = {
    "local_guest",
    "local_cli",
    "human",
    "provider",
    "surface_live",
    "protected",
}

TOKENS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\(|\)")
OPERATORS = {"not", "and", "or"}

# pytest's own markers, which every parametrised or async test carries. They
# describe how a test is written, not which suite it belongs to, so a floor on
# them measures nothing. Two are the exception and get a ceiling instead: a
# rising number of `skip`/`skipif` is precisely the failure this file is about,
# so it has to be recorded deliberately rather than accumulate.
BUILTIN_MARKERS = {"anyio", "asyncio", "parametrize", "timeout", "usefixtures"}
CEILING_MARKERS = {"skip", "skipif"}


def selects(expression: str, markers: set[str]) -> bool:
    """Evaluate a pytest `-m` expression against one test's markers.

    Only names and `not`/`and`/`or`/parentheses are accepted, so this never
    evaluates arbitrary text as Python.
    """
    rendered = []
    for token in TOKENS.findall(expression):
        if token in OPERATORS or token in "()":
            rendered.append(token)
        else:
            rendered.append(str(token in markers))
    if "".join(TOKENS.sub("", expression).split()):
        raise ValueError(f"unsupported marker expression: {expression}")
    return bool(eval(" ".join(rendered), {"__builtins__": {}}, {}))  # noqa: S307


def registered_markers() -> list[str]:
    """Marker names from pytest.ini, which `--strict-markers` makes exhaustive."""
    text = (BACKEND / "pytest.ini").read_text()
    body = text.split("markers =", 1)[1].split("\n\n", 1)[0]
    names = []
    for line in body.splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            names.append(line.split(":", 1)[0].strip())
    return names


def collect() -> dict[str, list[str]]:
    """One collection pass over the backend, tallied by marker."""
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / "census.json"
        environment = {
            **os.environ,
            "PYTEST_CENSUS_OUT": str(out),
            "PYTHONPATH": os.pathsep.join(
                filter(None, [str(REPO_ROOT / "scripts"), os.environ.get("PYTHONPATH")])
            ),
        }
        result = subprocess.run(
            [
                "uv", "run", "pytest",
                "--collect-only", "-q", "-p", "_pytest_census_plugin",
                "-p", "no:cacheprovider", "--no-header",
            ],
            cwd=BACKEND,
            env=environment,
            capture_output=True,
            text=True,
        )
        if not out.exists():
            sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
            raise SystemExit("::error::collection produced no census; see output above")
        return json.loads(out.read_text())


def counts(census: dict[str, list[str]]) -> dict[str, int]:
    return {
        name: len(ids)
        for name, ids in census.items()
        if name != "_collected" and name not in BUILTIN_MARKERS
    }


def check(census: dict[str, list[str]], baseline: dict) -> list[str]:
    problems: list[str] = []
    total = int(census["_collected"][0])
    if total == 0:
        return ["collection found no tests at all"]

    # The strongest single signal, and the one the `testpaths` bug would have
    # tripped: fewer tests reached collection than last time anyone looked.
    floor = baseline.get("collected", 0)
    if total < floor:
        problems.append(
            f"collection found {total} tests, down from {floor}. A directory "
            f"dropping out of `testpaths` looks exactly like this and nothing "
            f"else reports it"
        )

    observed = counts(census)
    for name, recorded in sorted(baseline.get("markers", {}).items()):
        found = observed.get(name, 0)
        if name in CEILING_MARKERS:
            if found > recorded:
                problems.append(
                    f"{found} tests are marked '{name}', up from {recorded}. An "
                    f"unconditional skip is invisible in a green run, so each "
                    f"one is a deliberate decision; re-record with --update"
                )
        elif found < recorded:
            problems.append(
                f"marker '{name}' collects {found} tests, down from {recorded}. "
                f"Either they moved, or a suite stopped being collected -- which "
                f"is invisible in a passing run. Re-record with --update if the "
                f"drop is intended"
            )

    for name in registered_markers():
        if observed.get(name, 0) == 0 and name not in baseline.get("empty_markers", []):
            problems.append(
                f"marker '{name}' is registered but no test carries it. A marker "
                f"nothing uses is either a typo at the use site or a suite that "
                f"is gone"
            )

    # Membership, not counts: a lane's green must not be covering tests that
    # only ever skip.
    for lane, expression in sorted(lanes().items()):
        for marker in sorted(ENVIRONMENT_GATED):
            for nodeid in census.get(marker, []):
                markers = {name for name, ids in census.items() if nodeid in ids}
                if selects(expression, markers):
                    problems.append(
                        f"`{lane}` selects {nodeid}, which is marked '{marker}' "
                        f"and can only skip on a runner without that "
                        f"environment. A suite that skips reports the same "
                        f"green as a suite that passes; deselect it instead"
                    )
                    break
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true", help="record the current counts as the floor"
    )
    arguments = parser.parse_args()

    census = collect()
    observed = counts(census)

    if arguments.update:
        BASELINE.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Floors, not exact counts: a marker collecting fewer "
                        "tests than recorded fails, more is fine. `skip` and "
                        "`skipif` are the other way round -- those are ceilings. "
                        "Regenerate with "
                        "`python3 scripts/check_pytest_census.py --update`."
                    ),
                    "collected": int(census["_collected"][0]),
                    "markers": dict(sorted(observed.items())),
                    "empty_markers": sorted(
                        name for name in registered_markers() if not observed.get(name)
                    ),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Recorded {len(observed)} markers over {census['_collected'][0]} tests.")
        return 0

    if not BASELINE.exists():
        print(f"::error::{BASELINE} is missing; run with --update")
        return 1
    problems = check(census, json.loads(BASELINE.read_text()))
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print(
        f"Census holds: {census['_collected'][0]} tests, "
        f"{len(observed)} markers, no lane skipping green."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
