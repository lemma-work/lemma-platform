#!/usr/bin/env python3
"""Fail the build on a default argument a test believes it can replace.

A default argument is evaluated **once**, when the module is imported. So::

    from app.modules.x.y import do_the_thing

    def run(*, doer=do_the_thing): ...

captures the function object at import. A test that later does
``monkeypatch.setattr(y, "do_the_thing", fake)`` rebinds the *module attribute*
and the default keeps pointing at the original. The two never meet, the real
collaborator runs, and — this is the part that makes it worth a gate — **the
test usually still passes**, because a real dependency failing often looks
exactly like a fake dependency failing.

That is not hypothetical. Four instances turned up in one day:

* ``from … import verify_webhook`` at module scope in the Composio webhook
  source. Two e2e suites fake Composio verification by patching the published
  contract; neither reached the bound name, the real SDK ran, and CI answered
  403.
* A ``Protocol`` annotation on a FastStream handler dependency, bound when the
  worker builds its dependency model. The worker refused to start, and no unit
  test could see it because calling a handler never builds that model.
* Two sets of constructor defaults. One of them silenced five patches at once
  in ``conversation_title_service`` and turned an e2e title assertion into
  ``"[mock] User's first message:"``; the other made a double unreachable while
  the test went on passing, and was only noticed because two *sibling* tests
  failed.

**The rule this enforces.** A name that tests replace by patching a module
attribute must not also be bound as a default argument. Deliberately *not*
narrowed to "patched on this very module": that stricter reading finds nothing
today, because the way this bites is that somebody reasonably expects a patch
to work and writes it later. Every one of the four incidents above had that
shape. If a name is one this codebase doubles, binding it at import is a trap
already set.

**Why there is no baseline.** The rule is quiet by construction. A default
written as ``Enum.MEMBER`` is an attribute rather than a bare name, so it is
skipped structurally, and a constant nobody patches drops out for want of a
patch site. Measured when this was written: 66 bare-name defaults across the
backend, of which **2** name something tests patch — both in one file, and both
in the file that had already produced two of the four incidents. A gate that
reports nothing for months and then catches the one that matters is doing its
job; a baseline would only offer somewhere to hide it.

**What this does not cover.** Two of the four shapes above: a module-scope
``from x import name`` that is *called* rather than defaulted (too broad to
flag — it is most of the import graph), and the FastStream ``Protocol``
annotation, which is not a default at all. The first is partly covered by
preferring ``import module`` over ``from module import name`` at call sites;
the second by ``app/core/tests/unit/test_worker_subscribers_build.py``, which
builds every subscriber's dependency model in the unit lane.

Usage::

    uv run python scripts/check_import_bound_defaults.py
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOT = ROOT / "app"

#: Test trees are excluded from the *production* half only: a default bound in
#: a test fixture is that test's own business.
TEST_PARTS = {"tests", "test_support"}
EXCLUDED_PARTS = {"__pycache__", ".venv", "node_modules"}


@dataclass(frozen=True)
class Violation:
    module: str
    name: str
    path: str
    line: int
    scope: str
    #: Modules this name is patched on. When it includes `module`, a test is
    #: already reaching for something it cannot have.
    patched_on: tuple[str, ...]

    @property
    def already_broken(self) -> bool:
        return self.module in self.patched_on

    def render(self) -> str:
        where = (
            "on this module"
            if self.already_broken
            else f"on {len(self.patched_on)} other module(s)"
        )
        return f"{self.path}:{self.line}  {self.scope}(…, {self.name}=…)  — patched {where}"


def _python_files() -> list[Path]:
    return [
        path
        for path in SCAN_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    ]


def _is_test(path: Path) -> bool:
    return any(part in TEST_PARTS for part in path.parts)


def _dotted_module(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _module_level_names(tree: ast.Module) -> set[str]:
    """Names this module binds at module scope: imports and defs.

    Assignments are deliberately absent. A default naming a module-level
    constant is the common, harmless case, and including them would swamp the
    intersection with things nobody patches.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _defaults_bound_at_import(tree: ast.Module) -> list[tuple[str, int, str]]:
    """``(name, line, enclosing_scope)`` for each default that is a bare name.

    An `Enum.MEMBER` default is an `ast.Attribute`, not an `ast.Name`, so it
    never reaches here — which is why this needs no allowlist of value types.
    """
    bound = _module_level_names(tree)
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        for default in (*args.defaults, *(d for d in args.kw_defaults if d)):
            if isinstance(default, ast.Name) and default.id in bound:
                found.append((default.id, node.lineno, node.name))
    return found


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name → dotted module path, for every module a test file imports."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                # `from app.a import b as c` — `c` may be a module or a symbol;
                # treating it as a module is right when it is one and harmless
                # when it is not, since the intersection needs a real match.
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _patch_targets(tree: ast.Module) -> set[tuple[str, str]]:
    """``(module, attribute)`` pairs this test file replaces.

    Covers the two forms in use: `monkeypatch.setattr(module, "name", …)`,
    which is overwhelmingly the local idiom, and the dotted-string form that
    `patch`/`setattr` also accept.
    """
    aliases = _module_aliases(tree)
    targets: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in {"setattr", "patch", "object"}:
            continue
        first = node.args[0]
        if (
            isinstance(first, ast.Name)
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            module = aliases.get(first.id)
            if module:
                targets.add((module, node.args[1].value))
        elif isinstance(first, ast.Constant) and isinstance(first.value, str):
            dotted = first.value
            if "." in dotted:
                module, _, attribute = dotted.rpartition(".")
                targets.add((module, attribute))
    return targets


def main() -> int:
    production: list[tuple[str, str, Path, int, str]] = []
    #: attribute name -> the modules tests patch it on
    patched: dict[str, set[str]] = {}

    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if _is_test(path):
            for target_module, attribute in _patch_targets(tree):
                patched.setdefault(attribute, set()).add(target_module)
            continue
        module = _dotted_module(path)
        for name, line, scope in _defaults_bound_at_import(tree):
            production.append((module, name, path, line, scope))

    violations = [
        Violation(
            module=module,
            name=name,
            path=str(path.relative_to(ROOT)),
            line=line,
            scope=scope,
            patched_on=tuple(sorted(patched[name])),
        )
        for module, name, path, line, scope in production
        if name in patched
    ]

    if violations:
        print("Default arguments bound at import that a test tries to replace:\n")
        for violation in sorted(violations, key=lambda v: (v.path, v.line)):
            print(f"  {violation.render()}")
        print(
            "\nA default is evaluated once, at import, so patching the module "
            "attribute later\ndoes not reach it — and the test usually still "
            "passes, because a real\ncollaborator failing looks like a fake one "
            "failing. Default to `None` and\nresolve the module-level name at "
            "call time, or take the collaborator as a\nrequired argument."
        )
        return 1

    print(
        f"✓ import-bound defaults: none patched elsewhere "
        f"({len(production)} defaults, {len(patched)} patched names)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
