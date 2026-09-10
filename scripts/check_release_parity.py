#!/usr/bin/env python3
"""Both DMG pipelines verify the same things.

Two workflows build a signed, notarized macOS DMG: `release-desktop.yml` for a
tagged release, and `release-local-images.yml` for the nightly. They were
written separately and they drifted, in both directions -- and the drift is
invisible, because each one is green on its own terms.

What it cost, measured at the time this was written: the nightly did not verify
that the virtualization helper still carried its entitlement, so a nightly with
a guest that could not boot would have shipped; and the *release* did not verify
`NSLocalNetworkUsageDescription`, so a release that could never reach its own
backend would have shipped. Each pipeline was checking something the other had
decided mattered.

The durable fix is not a longer checklist in each file. It is that a check lives
in a script under `desktop/scripts/`, tested there, and that both pipelines call
the same set of them -- which is what this asserts. A check added to one
pipeline's inline shell instead of to a script fails here, with the name of the
pipeline that has it and the one that does not.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# The two jobs that build and publish a macOS DMG.
DMG_JOBS = [
    (".github/workflows/release-desktop.yml", "build-dmg", "the tagged release"),
    (".github/workflows/release-local-images.yml", "share-desktop-dmg", "the nightly"),
]

SCRIPT_CALL = re.compile(r"desktop/scripts/(?P<name>[A-Za-z0-9_]+\.py)")

# Scripts one pipeline runs for a reason the other does not share. Each entry
# says which pipeline, which script, and why -- an unexplained asymmetry is the
# thing this file exists to catch, so there is no way to record one silently.
ALLOWED_ASYMMETRY = {
    (
        "the nightly",
        "nightly_update.py",
    ): "stages the nightly's own rewritten-in-place feed, which a tag does not have",
}


def scripts_called(workflow: Path, job: str) -> set[str]:
    document = yaml.safe_load(workflow.read_text())
    try:
        steps = document["jobs"][job]["steps"]
    except KeyError as error:
        raise SystemExit(
            f"{workflow.relative_to(REPO)} has no job {job!r}: if it was renamed, "
            f"rename it in DMG_JOBS too, or this file is checking nothing"
        ) from error
    found: set[str] = set()
    for step in steps:
        run = step.get("run") or ""
        found.update(match.group("name") for match in SCRIPT_CALL.finditer(run))
    return found


def failures() -> list[str]:
    called = {
        label: scripts_called(REPO / path, job) for path, job, label in DMG_JOBS
    }
    labels = list(called)
    problems: list[str] = []
    for label in labels:
        for other in labels:
            if other == label:
                continue
            for script in sorted(called[label] - called[other]):
                if (label, script) in ALLOWED_ASYMMETRY:
                    continue
                problems.append(
                    f"{label} runs desktop/scripts/{script} and {other} does not. "
                    f"Either call it there too, or record why not in "
                    f"ALLOWED_ASYMMETRY with the reason."
                )
    # And the record stays honest: an allowance for a script nobody calls any
    # more is a stale exemption, which is how the next drift hides.
    for (label, script), reason in ALLOWED_ASYMMETRY.items():
        if script not in called.get(label, set()):
            problems.append(
                f"ALLOWED_ASYMMETRY says {label} runs {script} ({reason}), and it "
                f"does not. Remove the entry."
            )
    return problems


def main() -> int:
    problems = failures()
    for problem in problems:
        print(f"✗ {problem}", file=sys.stderr)
    if problems:
        return 1
    print("✓ Release parity: both DMG pipelines run the same verification scripts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
