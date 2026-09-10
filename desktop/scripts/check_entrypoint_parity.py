#!/usr/bin/env python3
"""The Makefile and desktop.ps1 offer the same verbs, or say why not.

`desktop.ps1` opens by claiming that "the same verbs live here, one per
`desktop-*` target ... Neither entrypoint reimplements the other, so they cannot
drift." They had: the Makefile carried twenty-two `desktop-*` targets and the
script eleven verbs, and four of the missing ones -- the file-size ratchet, the
baked-concepts check, the browser journeys, and `check` itself -- are not
platform-specific at all. A Windows contributor could not run the gate that
decides whether their change is acceptable.

Platform-specific targets are real and stay: a DMG is macOS, a guest image is
Linux, and cross-compiling the Windows paths only means something from a Mac.
Each is recorded below with the reason, so the difference is a decision rather
than an omission -- and a new target is a failure here until somebody says
which it is.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent.parent
REPO = DESKTOP.parent

MAKEFILE = REPO / "Makefile"
POWERSHELL = DESKTOP / "scripts/desktop.ps1"

MAKE_TARGET = re.compile(r"^desktop-([a-z0-9-]+):", re.MULTILINE)
ANY_TARGET = re.compile(r"^([a-z0-9-]+):", re.MULTILINE)
VALIDATE_SET = re.compile(r"\[ValidateSet\((?P<body>.*?)\)\]", re.DOTALL)

# Targets that exist on one entrypoint for a reason the other cannot share.
ONLY_ON_THE_MAKEFILE = {
    "dmg": "a macOS disk image",
    "dev": "dev-local.sh has no Windows counterpart; install the .exe instead",
    "guestd": "builds the Linux guest daemon",
    "host-pack": "assembles the macOS/Linux host pack",
    "host-pack-check": "checks that pack",
    "verify-guest": "boots a macOS guest",
    "verify-agents": "drives agents against a macOS install",
    "check-windows": "cross-compiles the Windows paths from a Mac; on Windows they are the build",
    "fmt-fix": "the script spells this `fmt -Fix`",
    "e2e": "drives a running macOS install",
    "e2e-temp": "stands up a throwaway stack beside a running macOS install",
    "agent-host-e2e": "drives agents against a macOS install",
    "agent-host-browser-e2e": "the same, through a browser",
}

# Verbs that pair with a repository-level target rather than a `desktop-` one,
# because what they check is not desktop-specific.
REPO_WIDE = {"version-check": "every component in the repository, not only desktop"}


def makefile_targets() -> set[str]:
    return set(MAKE_TARGET.findall(MAKEFILE.read_text()))


def powershell_verbs() -> set[str]:
    match = VALIDATE_SET.search(POWERSHELL.read_text())
    if not match:
        raise SystemExit(
            f"{POWERSHELL.name} has no [ValidateSet] of verbs; if the parameter was "
            f"restructured, teach this file how to read it rather than deleting it"
        )
    return set(re.findall(r"'([a-z0-9-]+)'", match.group("body")))


def failures() -> list[str]:
    targets = makefile_targets()
    verbs = powershell_verbs() - {"help"}
    problems: list[str] = []

    for missing in sorted(targets - verbs - set(ONLY_ON_THE_MAKEFILE)):
        problems.append(
            f"`make desktop-{missing}` has no verb in desktop.ps1. Add one, or "
            f"record it in ONLY_ON_THE_MAKEFILE with the reason it cannot exist "
            f"on Windows."
        )
    repo_targets = set(ANY_TARGET.findall(MAKEFILE.read_text()))
    for extra in sorted(verbs - targets):
        if extra in REPO_WIDE and extra in repo_targets:
            continue
        problems.append(
            f"`desktop.ps1 {extra}` has no `desktop-{extra}` target in the "
            f"Makefile, so the two entrypoints do not offer the same thing."
        )
    for recorded, reason in sorted(REPO_WIDE.items()):
        if recorded not in repo_targets:
            problems.append(
                f"REPO_WIDE records `make {recorded}` ({reason}), and the "
                f"Makefile has no such target. Remove the entry."
            )
    for recorded, reason in sorted(ONLY_ON_THE_MAKEFILE.items()):
        if recorded not in targets:
            problems.append(
                f"ONLY_ON_THE_MAKEFILE records desktop-{recorded} ({reason}), and "
                f"the Makefile has no such target. Remove the entry."
            )
    return problems


def main() -> int:
    problems = failures()
    for problem in problems:
        print(f"✗ {problem}", file=sys.stderr)
    if problems:
        return 1
    print("✓ Entrypoints: the Makefile and desktop.ps1 offer the same verbs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
