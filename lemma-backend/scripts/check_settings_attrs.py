#!/usr/bin/env python3
"""Verify every `settings.<name>` access names a field that exists.

Settings objects are pydantic models, so a stale name raises at the moment it
is read -- not at import. A field that only some e2e path touches can therefore
survive a full unit run and a type check, and fail hours later on real
infrastructure. That is exactly what happened when the workspace fields moved
out of the core Settings: the harness set them by string key, and nothing
noticed until a Docker sandbox was already running.

This walks the AST for attribute access on any known settings singleton and
checks the name against the live object, so the same mistake costs a second
rather than a test run.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Import path -> exported singleton name. Each entry is a module-level
# `BaseSettings` instance that code reads configuration from.
SETTINGS_SOURCES = {
    "app.core.config": "settings",
    "app.modules.workspace.config": "workspace_settings",
}


def _known_attributes() -> dict[str, set[str]]:
    import importlib

    known: dict[str, set[str]] = {}
    for module_path, name in SETTINGS_SOURCES.items():
        module = importlib.import_module(module_path)
        known[name] = set(dir(getattr(module, name)))
    return known


def _local_aliases(tree: ast.Module) -> dict[str, str]:
    """Map the name a file uses locally back to the canonical singleton.

    `from app.core.config import settings as app_settings` is common in tests,
    so the local name is not reliably the exported one.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        exported = SETTINGS_SOURCES.get(node.module)
        if exported is None:
            continue
        for alias in node.names:
            if alias.name == exported:
                aliases[alias.asname or alias.name] = exported
    return aliases


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and "alembic" not in path.parts
    )


def check() -> list[str]:
    known = _known_attributes()
    failures: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError, UnicodeDecodeError:
            continue
        aliases = _local_aliases(tree)
        if not aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            exported = aliases.get(node.value.id)
            if exported is None:
                continue
            if node.attr in known[exported]:
                continue
            failures.append(
                f"{path.relative_to(ROOT)}:{node.lineno}: "
                f"{node.value.id}.{node.attr} is not a field on {exported}"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    failures = check()
    if failures:
        print("Unknown settings attributes:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"Settings attribute check passed ({len(SETTINGS_SOURCES)} objects).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
