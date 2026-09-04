#!/usr/bin/env python3
"""Ratchet on tests that install a double inside the thing they are testing.

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

The subject is what the file imports, not what it is called. Reading it off
`test_<x>.py` put two thirds of the repo's patch calls where no rule could
reach them: 571 test files have a stem no source file answers to, so nothing
they patched could ever match. `test_schedule_idempotency_regression.py` scored
zero while installing twenty doubles on the service module it imports, and
`conftest.py` -- autouse fixtures and all -- was invisible for want of a
`test_` prefix. A conftest is measured against the subjects of the tests it
applies to, since that is whose isolation it is arranging.

Only a stand-in for *behavior* counts. Setting `settings.api_url` to a string
arranges the run rather than doubling a unit, and no rename can hide behind it.

The number only goes down. Existing sites are recorded per module in
`test-doubles-baseline.json`; the gate fails when a module grows one, and
`--write` records a reduction so it cannot come back.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# Lives here, not in the repo-root `scripts/`, because it parses backend source
# and the backend is Python 3.14: a root script run with a bare `python3` reads
# a PEP 758 `except A, B:` handler as a syntax error in working code.
_BACKEND = Path(__file__).resolve().parent.parent
_BASELINE = _BACKEND / "test-doubles-baseline.json"
_CLI = _BACKEND.parent / "lemma-cli"

# The CLI is surveyed from here too, rather than from a second gate under
# `lemma-cli/`, because the rule and the ratchet are one thing and splitting
# them is how one half goes unwatched.
_ROOTS = (
    (_BACKEND / "app", "app"),
    (_CLI / "tests", "lemma-cli"),
)

# Where a patched name has to land to be first-party at all; a double on
# `httpx.AsyncClient` is the collaborator boundary working as intended, and the
# old rule counted those whenever the library's own name happened to carry the
# test's stem.
_PACKAGES = (
    _BACKEND / "app",
    _BACKEND / "scripts",
    _CLI / "lemma_cli",
    _CLI / "lemma_pod_bundle",
)

# The same files under the name a test loads them by: `scripts/` is on the path
# when a script runs, and `spec_from_file_location("import_connector_catalog")`
# followed by 99 patches on its innards is the largest single blind spot here.
_BARE = (_BACKEND / "scripts", _BACKEND)

# Packages that hold the test's own scaffolding rather than a unit.
_SCAFFOLDING = {"tests", "testing", "test_support"}

# The three ways this codebase installs a double by name. `patch("a.b.c")` and
# `patch.object(module, "name")` come from unittest.mock; `monkeypatch.setattr`
# is the pytest one and is by far the most used.
_PATCHERS = {"patch", "object", "setattr"}

# `patch(...)` with any of these builds the stand-in for you.
_STANDIN_KEYWORDS = {
    "new",
    "new_callable",
    "return_value",
    "side_effect",
    "autospec",
    "wraps",
}

# Constructors of values. What they return has no behavior of its own, so
# `settings.api_key = SecretStr("test")` is arranging the run and cannot hide
# the rename this gate looks for. Mock factories are deliberately absent.
_VALUE_TYPES = {
    "SecretStr",
    "Decimal",
    "Path",
    "UUID",
    "bool",
    "bytes",
    "datetime",
    "float",
    "frozenset",
    "int",
    "set",
    "str",
    "timedelta",
}


@dataclass(frozen=True)
class _Site:
    """One installed double: where the patched name lives, and its full path."""

    module: str
    target: str


@dataclass(frozen=True)
class _Names:
    """What the local names in one test file stand for."""

    dotted: dict[str, str]
    literal: frozenset[str]
    loaded: frozenset[str]


@dataclass(frozen=True)
class _Scan:
    subjects: frozenset[str]
    sites: tuple[_Site, ...]


@cache
def _first_party() -> frozenset[str]:
    """Dotted name of every module a patch target could resolve into."""
    found: set[str] = set()
    for package in _PACKAGES:
        if not package.exists():
            continue
        for path in package.rglob("*.py"):
            parts = list(path.relative_to(package.parent).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            if parts:
                found.add(".".join(parts))
    for directory in _BARE:
        found.update(path.stem for path in directory.glob("*.py"))
    return frozenset(found)


def _module_of(path: Path, root: Path, label: str) -> str:
    parts = path.relative_to(root).parts
    if label == "app" and parts[0] == "modules":
        return f"modules/{parts[1]}"
    return label if label != "app" else parts[0]


def _owning_module(dotted: str) -> str | None:
    """The longest first-party *production* module the dotted path passes through.

    Scaffolding is exempt: adjusting a fake you built -- TST-03's `fakes.py`,
    a `testing/` package -- is the fake doing its job, not a double planted in
    the unit.
    """
    parts = dotted.split(".")
    known = _first_party()
    for end in range(len(parts), 0, -1):
        candidate = parts[:end]
        name = ".".join(candidate)
        if name not in known:
            continue
        if _SCAFFOLDING & set(candidate) or candidate[-1] == "fakes":
            return None
        return name
    return None


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> the dotted path it refers to, for import forms."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Without an `as`, `import a.b` binds `a`, and whatever is
                # written out under it is already absolute.
                root = alias.name.split(".")[0]
                found[alias.asname or root] = alias.name if alias.asname else root
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return found


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    """The absolute path an expression names, if it names one."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        text = ast.unparse(node)
        root = text.split(".")[0]
        return aliases[root] + text[len(root) :] if root in aliases else None
    return None


def _names(tree: ast.Module) -> _Names:
    """Resolve the locals a patch call can be pointed at.

    Three bindings beyond the imports, each of which the filename rule could
    never see through: a module-level dotted string reused as a patch target,
    an object built from an imported class and then patched attribute by
    attribute, and a module loaded from a file path rather than imported.
    """
    dotted = _aliases(tree)
    literal: set[str] = set()
    loaded: set[str] = set()
    specs: dict[str, str] = {}

    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if isinstance(node.value.value, str) and "." in node.value.value:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    dotted[target.id] = node.value.value
                    literal.add(target.id)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        function = call.func
        called = function.attr if isinstance(function, ast.Attribute) else ""
        bound: str | None = None
        if called == "spec_from_file_location" and call.args:
            first = call.args[0]
            spec_name = first.value if isinstance(first, ast.Constant) else None
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(spec_name, str):
                    specs[target.id] = spec_name
            continue
        if called in {"module_from_spec", "import_module"} and call.args:
            argument = call.args[0]
            if called == "import_module":
                bound = argument.value if isinstance(argument, ast.Constant) else None
            elif isinstance(argument, ast.Name):
                bound = specs.get(argument.id)
            if isinstance(bound, str):
                loaded.add(bound)
            else:
                bound = None
        else:
            bound = _dotted(function, dotted)
        for target in node.targets:
            if isinstance(target, ast.Name) and bound and target.id not in dotted:
                dotted[target.id] = bound

    return _Names(dotted=dotted, literal=frozenset(literal), loaded=frozenset(loaded))


def _subjects(tree: ast.Module, names: _Names) -> frozenset[str]:
    """The first-party modules this file pulled in to exercise."""
    known = _first_party()
    found: set[str] = set(names.loaded)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                submodule = f"{node.module}.{alias.name}"
                found.add(submodule if submodule in known else node.module)
    return frozenset(name for name in found if name in known)


def _target_of(node: ast.Call, names: _Names) -> tuple[str | None, bool]:
    """The patched path, and whether it was named as a string."""
    function = node.func
    name = (
        function.attr
        if isinstance(function, ast.Attribute)
        else function.id
        if isinstance(function, ast.Name)
        else ""
    )
    if name not in _PATCHERS or not node.args:
        return None, False
    first = node.args[0]
    if isinstance(first, ast.Constant):
        return (first.value if isinstance(first.value, str) else None), True
    if isinstance(first, ast.Name):
        return names.dotted.get(first.id, first.id), first.id in names.literal
    return _dotted(first, names.dotted) or ast.unparse(first), False


def _is_data(node: ast.expr) -> bool:
    """A value the unit reads, as opposed to behavior standing in for its own."""
    if isinstance(node, (ast.Constant, ast.JoinedStr)):
        return True
    if isinstance(node, ast.UnaryOp):
        return _is_data(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_data(node.left) and _is_data(node.right)
    if isinstance(node, ast.Call):
        called = node.func
        return isinstance(called, ast.Name) and called.id in _VALUE_TYPES
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_data(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(value is not None and _is_data(value) for value in node.values)
    return False


def _installs_double(node: ast.Call, string_form: bool) -> bool:
    if any(keyword.arg in _STANDIN_KEYWORDS for keyword in node.keywords):
        return True
    # `patch("a.b.c")` and `patch.object(mod, "name")` stand in with a MagicMock;
    # every other form names its replacement in the last positional argument.
    replacement = (
        node.args[2]
        if len(node.args) > 2
        else node.args[1]
        if len(node.args) == 2 and string_form
        else None
    )
    return replacement is None or not _is_data(replacement)


def _scan(path: Path) -> _Scan:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = _names(tree)
    sites: list[_Site] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target, string_form = _target_of(node, names)
        if not target or not _installs_double(node, string_form):
            continue
        module = _owning_module(target)
        if module:
            sites.append(_Site(module=module, target=target))
    return _Scan(subjects=_subjects(tree, names), sites=tuple(sites))


def _survey() -> Counter[str]:
    scans: dict[Path, tuple[_Scan, str]] = {}
    for root, label in _ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.name.startswith("test_") or path.name == "conftest.py":
                scans[path] = (_scan(path), _module_of(path, root, label))

    # A conftest names no subject of its own; the tests underneath it do, and
    # its fixtures run inside theirs.
    inherited = {
        path: frozenset().union(
            *(
                scan.subjects
                for other, (scan, _) in scans.items()
                if other.name.startswith("test_") and other.is_relative_to(path.parent)
            ),
            scans[path][0].subjects,
        )
        for path in scans
        if path.name == "conftest.py"
    }

    found: Counter[str] = Counter()
    for path, (scan, module) in scans.items():
        subjects = inherited.get(path, scan.subjects)
        stem = path.stem.removeprefix("test_")
        sites = sum(
            1
            for site in scan.sites
            if site.module in subjects
            or (stem and stem in site.target.replace(".", "/").split("/"))
        )
        if sites:
            found[module] += sites
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
