#!/usr/bin/env python3
"""Enforce total and per-module coverage floors.

Two lanes are measured separately and hold different floors. The *combined*
lane is unit plus e2e and says how well the code is covered at all; the
*e2e-union* lane is the e2e shards alone and says how much of a module a real
request actually reaches. A module can be well covered in the first and barely
exercised in the second, which is why the second exists.

Floors live in ``coverage-baseline.json`` rather than in the command line.
Four were spelled out as flags and the rest of the repo had none, so any module
without a flag could lose all of its coverage while the whole-repo number --
one figure over ~190k statements -- barely moved. A floor per module, recorded
from what is actually measured, is the same ratchet the architecture and
swallowed-error gates already use: it cannot be met by accident and it cannot
drift down unnoticed.

``--write`` refreshes the file from a run. It only ever *raises* a floor, so
regenerating after a good run locks the gain in, and regenerating after a bad
one changes nothing and leaves the failure to be dealt with.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

# How far below a measured value its recorded floor sits. Coverage moves by
# fractions between runs for reasons that are not regressions -- a shard
# ordering, a skip that fired -- and a floor set to the exact measurement turns
# each of those into a red build. Half a point absorbs that and still catches
# any drop worth a person's attention.
_MARGIN = 0.5


def _module(filename: str) -> str:
    parts = filename.replace("\\", "/").split("app/", 1)[-1].split("/")
    if len(parts) >= 2 and parts[0] == "modules":
        return parts[1]
    return parts[0]


def _skip(filename: str) -> bool:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return "/tests/" in filename or name.startswith("test_") or name == "conftest.py"


def _percentage(covered: int, statements: int) -> float:
    return 100.0 if statements == 0 else covered / statements * 100


def _floor_for(percentage: float) -> float:
    return max(0.0, math.floor(percentage * 10) / 10 - _MARGIN)


def _measure(report: dict[str, Any]) -> tuple[dict[str, float], dict[str, int], float]:
    """Per-module percentages, per-module statement counts, and the total."""
    modules: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    total_statements = 0
    total_covered = 0
    for filename, metadata in report.get("files", {}).items():
        if _skip(filename):
            continue
        summary = metadata["summary"]
        statements = int(summary["num_statements"])
        covered = statements - int(summary["missing_lines"])
        total_statements += statements
        total_covered += covered
        values = modules[_module(filename)]
        values[0] += statements
        values[1] += covered
    percentages = {
        name: _percentage(covered, statements)
        for name, (statements, covered) in modules.items()
    }
    counts = {name: statements for name, (statements, _) in modules.items()}
    return percentages, counts, _percentage(total_covered, total_statements)


# A module this small says nothing when it moves: one statement is worth whole
# percentage points, so a floor on it reports noise rather than coverage.
_FLOOR_MIN_STATEMENTS = 100


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--min-total", type=float, default=70.0)
    parser.add_argument(
        "--lane",
        default="combined",
        help="Which set of floors in the baseline to enforce.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "coverage-baseline.json",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Raise any floor the run beats, then exit without enforcing.",
    )
    args = parser.parse_args()

    report: dict[str, Any] = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    percentages, counts, total = _measure(report)

    baseline: dict[str, Any] = (
        json.loads(args.baseline.read_text(encoding="utf-8"))
        if args.baseline.exists()
        else {}
    )
    floors: dict[str, float] = dict(baseline.get(args.lane) or {})

    if args.write:
        raised: list[str] = []
        for name, percentage in sorted(percentages.items()):
            if counts.get(name, 0) < _FLOOR_MIN_STATEMENTS:
                continue
            candidate = _floor_for(percentage)
            if candidate > floors.get(name, -1.0):
                if name in floors:
                    raised.append(f"{name} {floors[name]:.1f} -> {candidate:.1f}")
                floors[name] = candidate
        baseline[args.lane] = dict(sorted(floors.items()))
        args.baseline.write_text(
            json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"Wrote {args.baseline} ({args.lane}: {len(floors)} module floors)")
        for line in raised:
            print(f"  raised {line}")
        return 0

    failures: list[str] = []
    if total < args.min_total:
        failures.append(f"total coverage {total:.2f}% is below {args.min_total:.2f}%")
    for name, floor in sorted(floors.items()):
        if name not in percentages:
            # A module that stopped being measured is not a pass. Renaming or
            # removing one is fine; it just has to be said in the baseline too.
            failures.append(
                f"{name} has a floor of {floor:.2f}% but was not measured — "
                f"remove it from {args.baseline.name} if the module is gone"
            )
            continue
        if percentages[name] < floor:
            failures.append(
                f"{name} coverage {percentages[name]:.2f}% is below {floor:.2f}%"
            )

    if failures:
        print(f"Coverage gate failed ({args.lane}):")
        for failure in failures:
            print(f"- {failure}")
        print(
            "\nAdd tests for the module named above. Regenerating the baseline "
            "cannot clear this: --write only raises floors."
        )
        return 1
    print(
        f"Coverage gate passed ({args.lane}): total {total:.2f}%, "
        f"{len(floors)} module floors held"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
