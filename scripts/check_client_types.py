#!/usr/bin/env python3
"""Record what a type checker finds in the two packages users install.

Neither `lemma-cli` nor `lemma-python` had a `[tool.basedpyright]` section, and
no gate ran a type checker over either. For the CLI that is ordinary debt. For
the SDK it is a broken promise: `lemma_sdk` ships a `py.typed` marker, which
tells a consumer's type checker to believe its annotations rather than fall
back to `Any` -- and `lemma_sdk/resources/base.py:_call` returns `Any`, so every
typed resource method is an annotation sitting on top of an unchecked
transport. A wrong annotation there is a wrong answer in someone else's build,
in a package they cannot edit. `docs/engineering/types.md` names this as TYP-15;
until now nothing enforced it, or even counted it.

So this counts it. It runs basedpyright in each client project's own
environment -- not `uvx`, which would see none of `typer`, `textual` or `httpx`
and report a few hundred unresolved imports instead of anything real -- and
records the errors per file against a baseline that may shrink freely.

Advisory today, on purpose. The point of this pass is to put a number on the
gap, and a number that blocks unrelated work on the day it is first taken is a
number people route around. `--advisory` prints the growth and exits 0; drop
that flag from the Makefile to arm the ratchet.

Errors only, matching `make -C lemma-backend typecheck-critical`, which runs
`basedpyright --level error`. `typeCheckingMode = "standard"` in each
pyproject is the same setting the backend holds itself to; the whole value of
these counts is that they can be compared with the backend's, and they cannot
be if the checker is set to something stricter here than there.

Usage::

    python3 scripts/check_client_types.py
    python3 scripts/check_client_types.py --advisory
    python3 scripts/check_client_types.py --update-baseline

Run from the repository root. The driver itself does no parsing of 3.14 source
-- it shells out to `uv run`, which picks each project's pinned interpreter --
so it stays inside the `scripts/` portability rule.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "client-typecheck-baseline.json"

#: Project directory -> the shipped package inside it. Only the package that is
#: published: tests, examples and codegen scripts are not what a user imports,
#: and each pyproject's `exclude` says the same thing for a bare
#: `uv run basedpyright`.
PROJECTS = (
    ("lemma-cli", "lemma_cli"),
    ("lemma-python", "lemma_sdk"),
)


def _run(project: str, package: str) -> list[dict]:
    """Type-check one project and return its diagnostics.

    `uv run` rather than `uvx`: basedpyright resolves imports against the
    environment it runs in, and outside the project's own environment every
    third-party import is an error that says nothing about this code.
    """
    result = subprocess.run(
        ["uv", "run", "basedpyright", "--outputjson", package],
        cwd=str(ROOT / project),
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"basedpyright produced no JSON for {project}. "
            "Run `uv sync` there, or `uv run basedpyright` to see the error."
        )
    return payload.get("generalDiagnostics", [])


def snapshot() -> dict:
    by_file: Counter = Counter()
    by_rule: Counter = Counter()
    for project, package in PROJECTS:
        for diagnostic in _run(project, package):
            if diagnostic.get("severity") != "error":
                continue
            path = Path(diagnostic["file"])
            try:
                relative = path.relative_to(ROOT).as_posix()
            except ValueError:
                relative = path.name
            by_file[relative] += 1
            by_rule[diagnostic.get("rule") or "(no rule)"] += 1
    return {
        "errors_by_file": dict(sorted(by_file.items())),
        # Recorded for the reader, not for the ratchet: a rule's total moves
        # whenever a file's does, so gating on both would report one regression
        # twice.
        "errors_by_rule": dict(sorted(by_rule.items())),
    }


def check(current: dict, baseline: dict) -> list[str]:
    allowed = baseline.get("errors_by_file", {})
    failures = []
    for name, count in sorted(current["errors_by_file"].items()):
        before = allowed.get(name, 0)
        if count > before:
            failures.append(f"type errors grew: {name} ({before} -> {count})")
    return failures


def _summary(current: dict) -> str:
    total = sum(current["errors_by_file"].values())
    files = len(current["errors_by_file"])
    return f"{total} type errors across {files} files"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree. Shrinking is always fine.",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Report growth and exit 0. How `make quality` runs this today.",
    )
    args = parser.parse_args()

    current = snapshot()

    if args.snapshot:
        print(json.dumps(current, indent=2, sort_keys=True))
        return 0

    if args.update_baseline:
        payload = dict(current)
        payload["_comment"] = (
            "basedpyright errors in the published client packages, at the "
            "`standard` setting lemma-backend uses. This file may shrink freely. "
            "lemma_sdk's entries are the ones that reach users: the package "
            "ships py.typed, so its annotations are what a consumer's checker "
            "believes. See scripts/check_client_types.py."
        )
        args.baseline.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"✓ baseline written: {_summary(current)}")
        return 0

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    failures = check(current, baseline)

    if not failures:
        print(f"✓ client types: no growth ({_summary(current)})")
        return 0

    marker = "!" if args.advisory else "✗"
    print(f"{marker} client types: {len(failures)} file(s) grew")
    for failure in failures:
        print(f"  - {failure}")
    if args.advisory:
        print(
            "  advisory: not failing the build. Re-record with "
            "`--update-baseline` if the growth is deliberate."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
