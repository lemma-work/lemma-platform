#!/usr/bin/env python3
"""Ratchet on tests that replace a part of the thing they are named after.

`test_exporter.py` patched five of the exporter's own row-shaping functions, so
the exporter's chief job -- producing the bundle's field shapes -- was stubbed
out in the tests named after it. `test_conversation_controller.py` patched the
controller's service factory nine times, so nine tests exercised routing and
serialization and none of them the composition the controller exists to do.

A double put in front of a collaborator is how a unit test isolates. A double
put *inside* the unit under test certifies the half you did not write: a rename
or a signature change behind the patched name lands green, and the test keeps
reading as though it covered the thing it is named for. These are already
constructor and factory seams, so the fix is to inject the collaborator rather
than patch it.

The number only goes down. Existing sites are recorded per module in
`test-doubles-baseline.json`; the gate fails when a module grows one, and
`--write` records a reduction so it cannot come back.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from pathlib import Path

# Lives here, not in the repo-root `scripts/`, because it parses backend source
# and the backend is Python 3.14: a root script run with a bare `python3` reads
# a PEP 758 `except A, B:` handler as a syntax error in working code.
_BACKEND = Path(__file__).resolve().parent.parent
_BASELINE = _BACKEND / "test-doubles-baseline.json"

# The CLI is surveyed from here too, rather than from a second gate under
# `lemma-cli/`, because the rule and the ratchet are one thing and splitting
# them is how one half goes unwatched.
_ROOTS = (
    (_BACKEND / "app", "app"),
    (_BACKEND.parent / "lemma-cli" / "tests", "lemma-cli"),
)

# The three ways this codebase installs a double by name. `patch("a.b.c")` and
# `patch.object(module, "name")` come from unittest.mock; `monkeypatch.setattr`
# is the pytest one and is by far the most used.
_PATCHERS = {"patch", "object", "setattr"}


def _module_of(path: Path, root: Path, label: str) -> str:
    parts = path.relative_to(root).parts
    if label == "app" and parts[0] == "modules":
        return f"modules/{parts[1]}"
    return label if label != "app" else parts[0]


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> the dotted path it refers to, for import forms."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return found


def _target_of(node: ast.Call, aliases: dict[str, str]) -> str | None:
    function = node.func
    name = (
        function.attr
        if isinstance(function, ast.Attribute)
        else function.id
        if isinstance(function, ast.Name)
        else ""
    )
    if name not in _PATCHERS or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant):
        return first.value if isinstance(first.value, str) else None
    if isinstance(first, ast.Name):
        return aliases.get(first.id, first.id)
    if isinstance(first, ast.Attribute):
        return ast.unparse(first)
    return None


def _sites(path: Path) -> int:
    """How many doubles this file installs inside its own subject."""
    subject = path.stem.removeprefix("test_")
    if not subject:
        return 0
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _aliases(tree)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _target_of(node, aliases)
        if target and subject in target.replace(".", "/").split("/"):
            count += 1
    return count


def _survey() -> Counter[str]:
    found: Counter[str] = Counter()
    for root, label in _ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("test_*.py")):
            sites = _sites(path)
            if sites:
                found[_module_of(path, root, label)] += sites
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="record the current counts as the ceiling"
    )
    arguments = parser.parse_args()

    found = _survey()
    baseline: dict[str, int] = (
        json.loads(_BASELINE.read_text(encoding="utf-8")) if _BASELINE.exists() else {}
    )
    baseline.pop("_comment", None)

    if arguments.write:
        recorded = dict(sorted(found.items()))
        _BASELINE.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Ceilings, not targets: a module installing more doubles "
                        "inside its own subject than recorded fails. Regenerate "
                        "with `uv run python scripts/check_test_doubles.py --write` "
                        "only to record a reduction."
                    ),
                    **recorded,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Recorded {sum(recorded.values())} sites over {len(recorded)} modules.")
        return 0

    grown = [
        f"{name}: {count} (was {baseline.get(name, 0)})"
        for name, count in sorted(found.items())
        if count > baseline.get(name, 0)
    ]
    if grown:
        print("Tests replacing part of their own subject increased:")
        for line in grown:
            print(f"- {line}")
        print(
            "\nA double in front of a collaborator isolates the unit; one inside "
            "it certifies the half you did not write, and survives a rename that "
            "should have failed. These are constructor and factory seams — inject "
            "the collaborator instead of patching it."
        )
        return 1

    total = sum(found.values())
    print(f"Test doubles gate passed: {total} in-subject sites, none new.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
