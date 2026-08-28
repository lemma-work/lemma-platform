#!/usr/bin/env python3
"""Stop the import graph growing without anybody deciding to grow it.

Importing ``app.app`` loads ~5,400 modules and costs ~323 MiB of resident memory
and ~16 s of wall time before the process can serve anything. Both the api and
the worker pay it, on every rollout and every restart.

An audit looked for the usual cause -- work happening at import time -- and found
none: one AST pass over 1,128 non-test files turned up a single hit, and that one
was ``asyncio.run`` under ``if __name__ == "__main__"``. Module-level
``re.compile`` is correct and is not what this is about.

The cost is breadth. Some of it is genuinely not ours to move --
``supertokens_python`` (367 modules) is needed before the first authenticated
request, and fastapi and sqlalchemy are needed to answer anything at all. So
this gate does not ask anyone to shrink the graph. It asks that growth be a
decision somebody made rather than something a new dependency did quietly.

**But do not read that as "nothing here is movable".** This docstring used to
say ``openai``, ``mcp`` and ``fastmcp`` arrived transitively through
``pydantic_ai`` and therefore "cannot be deferred by editing our own import
lines". Measured again in August 2026, that was wrong: ``openai`` arrived
through ``composio``, which arrived from a single module-scope line in
``connectors/services/auth/composio_auth_provider.py`` -- ours, and reached
from ``app.app`` through the connector router on every start. Deferring that one
line into the method that uses it removed **993 modules**, and cut the packed
import from 3.42s to 2.17s.

The lesson is not about composio. It is that a graph this wide hides its own
causes: the library that shows up in a profile is rarely the one that imported
it, and a note in a gate asserting something is immovable will be believed by
the next person instead of re-measured. Use ``python -X importtime`` and walk
the parent chain before concluding anything is structural.

Module count, not wall time. Count is deterministic across machines; import time
on a CI runner varies by more than the thing being measured, and a gate that
flaps gets switched off. Resident memory is reported alongside for context but
is not enforced, for the same reason.

Usage::

    uv run python scripts/check_import_budget.py
    uv run python scripts/check_import_budget.py --update-baseline
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "import-budget-baseline.json"

# Entrypoints worth watching: the api app and the worker's event wiring. Both
# are what a pod actually imports before it is ready.
ENTRYPOINTS = {
    "app.app": "the FastAPI application",
    "app.events": "the worker's event and task wiring",
}

# Headroom before the gate complains. A handful of modules is a normal
# consequence of ordinary work; a few hundred is a new dependency tree.
TOLERANCE = 50

_PROBE = """
import importlib, json, sys
importlib.import_module({target!r})
rss = None
try:
    with open("/proc/self/statm", "rb") as handle:
        rss = int(handle.read().split()[1]) * 4096
except Exception:
    pass
print(json.dumps({{"modules": len(sys.modules), "rss": rss}}))
"""


def measure(target: str) -> dict[str, int | None]:
    """Import *target* in a clean interpreter and report what it cost."""
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE.format(target=target)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"import budget: could not import {target}\n{completed.stderr[-2000:]}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from this checkout. Shrinking is always fine.",
    )
    args = parser.parse_args()

    measured = {target: measure(target) for target in ENTRYPOINTS}

    if args.update_baseline:
        payload = {
            "_comment": (
                "Modules loaded by each entrypoint before it can serve. This file "
                "may shrink freely; growing it past the tolerance means a new "
                "dependency tree arrived. See scripts/check_import_budget.py."
            ),
            "tolerance": TOLERANCE,
            "entrypoints": {
                target: {"modules": result["modules"], "rss_mib": _mib(result["rss"])}
                for target, result in measured.items()
            },
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"✓ baseline written: {len(measured)} entrypoint(s)")
        return 0

    if not args.baseline.exists():
        print(f"✗ import budget: no baseline at {args.baseline}", file=sys.stderr)
        print("  Run with --update-baseline to create it.", file=sys.stderr)
        return 1

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    tolerance = int(baseline.get("tolerance", TOLERANCE))
    recorded = baseline.get("entrypoints", {})

    regressions: list[str] = []
    for target, result in measured.items():
        allowed = recorded.get(target, {}).get("modules")
        modules = result["modules"]
        rss = _mib(result["rss"])
        detail = f"{modules} modules" + (f", {rss} MiB" if rss else "")
        if allowed is None:
            regressions.append(f"  {target}: not in the baseline ({detail})")
        elif modules > allowed + tolerance:
            regressions.append(
                f"  {target}: {modules} modules, baseline {allowed} "
                f"(+{modules - allowed}, tolerance {tolerance})"
            )
        else:
            print(f"  {target}: {detail} (baseline {allowed})")

    if regressions:
        print("\n✗ import budget: the graph grew", file=sys.stderr)
        print("\n".join(regressions), file=sys.stderr)
        print(
            "\nImport the new dependency inside the function that needs it, or "
            "accept the cost with --update-baseline.",
            file=sys.stderr,
        )
        return 1

    print("✓ import budget: within baseline")
    return 0


def _mib(rss: int | None) -> int | None:
    return None if rss is None else round(rss / (1024 * 1024))


if __name__ == "__main__":
    raise SystemExit(main())
