#!/usr/bin/env python3
"""Type-check every API controller, and refuse a new error in any of them.

`make quality`'s `typecheck-critical` names an explicit list of paths. It
includes `agent_surfaces/api/controllers` and not identity's, agent's or pod's,
and that gap has a cost on the record: PR #620 renamed a path parameter with a
word-boundary substitution over whole controller files, which also renamed the
*keyword arguments* of two service calls whose callee still declared `org_id`.
Both routes raised `TypeError: got an unexpected keyword argument`. It reached
CI. `basedpyright` found it in seconds once aimed at the changed files — nothing
aimed it.

A controller is where a signature meets a caller, so it is exactly where this
class of mistake lands, and it is cheap to check: the whole surface is
`app/modules/*/api/controllers`.

**Why a ratchet rather than a wider `typecheck-critical`.** Adding the directory
glob to that target fails immediately — 45 errors across 9 files today. Those
are pre-existing and mostly unrelated to controller signatures (`possibly
unbound`, `Row[...]` assignability). Baselining them per file keeps every
controller checked, makes the dirty nine a visible and shrinking list, and still
fails on error number 46 wherever it appears.

Counts are per file, not per line: an edit above a diagnostic moves its line
number but not its file, so the baseline does not churn on unrelated changes.

Usage::

    uv run python scripts/check_controller_types.py
    uv run python scripts/check_controller_types.py --update-baseline
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "controller-types-baseline.json"

#: The surface. Every module's HTTP controllers, and nothing else — widening
#: this to whole modules would drag in thousands of pre-existing diagnostics and
#: bury the signal this exists for.
CONTROLLER_GLOB = "app/modules/*/api/controllers"


def _targets() -> list[str]:
    return sorted(str(path) for path in ROOT.glob(CONTROLLER_GLOB) if path.is_dir())


def collect() -> Counter[str]:
    """Error count per controller file, relative to the backend root."""
    targets = _targets()
    if not targets:
        raise SystemExit(f"no controller directories matched {CONTROLLER_GLOB!r}")
    result = subprocess.run(
        ["uv", "run", "basedpyright", "--outputjson", "--level", "error", *targets],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    # basedpyright exits non-zero when it finds errors, which is the normal case
    # here. A missing or unparseable payload is the real failure.
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(
            "basedpyright produced no JSON. stderr:\n" + (result.stderr or "(empty)")
        ) from None

    counts: Counter[str] = Counter()
    for diagnostic in payload.get("generalDiagnostics", []):
        if diagnostic.get("severity") != "error":
            continue
        path = Path(diagnostic["file"])
        try:
            counts[str(path.relative_to(ROOT))] += 1
        except ValueError:
            counts[str(path)] += 1
    return counts


def _load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("files", {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree. Shrinking is always fine.",
    )
    args = parser.parse_args()

    counts = collect()

    if args.update_baseline:
        payload = {
            "_comment": (
                "Pre-existing basedpyright errors in API controllers, per file. "
                "This file may shrink freely; growing it means a new type error "
                "in a controller. See scripts/check_controller_types.py."
            ),
            "files": dict(sorted(counts.items())),
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(
            f"✓ baseline written: {sum(counts.values())} error(s) "
            f"across {len(counts)} file(s)"
        )
        return 0

    baseline = _load_baseline(args.baseline)
    grew = {
        name: (baseline.get(name, 0), count)
        for name, count in counts.items()
        if count > baseline.get(name, 0)
    }
    fixed = sum(
        max(0, allowed - counts.get(name, 0)) for name, allowed in baseline.items()
    )

    if fixed:
        print(f"✓ {fixed} baselined controller error(s) gone — run --update-baseline")

    if grew:
        print("New type errors in API controllers:\n")
        for name, (was, now) in sorted(grew.items()):
            print(f"  {name}: {was} -> {now}")
        print(
            "\nRun `uv run basedpyright --level error <file>` to see them. A "
            "controller is where a\nsignature meets a caller: a keyword argument "
            "that no longer matches its callee looks\nexactly like this, and "
            "reaches production as a 500 rather than a failing test."
        )
        return 1

    print(
        f"✓ controller types: no new errors "
        f"({sum(baseline.values())} baselined across {len(baseline)} file(s))"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
