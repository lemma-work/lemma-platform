"""Every settings singleton in the tree must be one `check_settings_attrs` checks.

That gate validates each `settings.<name>` and `getattr(settings, "<name>")`
against the real fields of the object being read. It can only do that for the
objects named in `SETTINGS_SOURCES`, and that list is hand-maintained.

It fell four behind while `app/core/config.py` was being split. `identity`,
`usage`, `workflow` and `function` all gained a settings class, and every field
that moved onto one left the gate's sight on the way -- a stale
`identity_settings.<removed_field>` would have been an `AttributeError` at
runtime under a green gate, which is the exact failure the gate exists to
prevent.

So the list is asserted against the tree rather than remembered.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BACKEND = Path(__file__).resolve().parents[4]


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_settings_attrs", _BACKEND / "scripts/check_settings_attrs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the script defines a frozen dataclass, and
    # `dataclasses` looks its module up in `sys.modules` while building it.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _singletons_in(path: Path) -> set[str]:
    """Module-level `name = SomethingSettings()` bindings in one file.

    By AST rather than by import: a settings class instantiates on import and
    reads the environment, and this test should not depend on what happens to
    be set.
    """
    found: set[str] = set()
    for node in ast.parse(path.read_text()).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        call = node.value
        if not isinstance(target, ast.Name) or not isinstance(call, ast.Call):
            continue
        callee = call.func
        name = getattr(callee, "id", None) or getattr(callee, "attr", None)
        if name and name.endswith("Settings"):
            found.add(target.id)
    return found


def _config_files() -> list[Path]:
    return sorted(
        path
        for path in _BACKEND.glob("app/**/config.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    )


def test_every_settings_singleton_is_checked() -> None:
    checker = _load_checker()
    watched = {name for names in checker.SETTINGS_SOURCES.values() for name in names}

    on_disk: dict[str, Path] = {}
    for path in _config_files():
        for name in _singletons_in(path):
            on_disk[name] = path

    missing = {
        name: str(path.relative_to(_BACKEND))
        for name, path in on_disk.items()
        if name not in watched
    }
    assert not missing, (
        "settings singletons no gate is watching: "
        f"{missing}. Add each to SETTINGS_SOURCES in "
        "scripts/check_settings_attrs.py -- until it is there, a reader of a "
        "field that moved or was renamed on it fails at runtime, not in CI."
    )


def test_every_watched_source_still_exists() -> None:
    # The other direction: a stale entry makes the gate import a module that is
    # gone, which fails as "the check is broken" rather than as a real finding.
    checker = _load_checker()
    for module_path, names in checker.SETTINGS_SOURCES.items():
        path = _BACKEND / Path(module_path.replace(".", "/") + ".py")
        assert path.exists(), f"{module_path} no longer exists"
        declared = _singletons_in(path)
        for name in names:
            # `app.core.config` builds its singleton through a helper rather
            # than a bare `Settings()` call, so the AST scan does not see it.
            if module_path == "app.core.config":
                continue
            assert name in declared, f"{module_path} no longer defines {name}"
