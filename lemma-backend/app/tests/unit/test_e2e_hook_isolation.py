"""Every e2e test hook production reads must be isolated from other tests.

The e2e bootstrap sets its hooks on the process-wide settings singletons and
never restores them -- it has to, because the worker subprocess inherits them
through ``os.environ`` and the in-process singleton is built at import. So the
root ``conftest`` restores them for any test not marked ``e2e``.

That list is the thing that rots. ``e2e_llm_mode`` was on it and
``e2e_disable_worker_file_autoindex`` was not, which is why
``test_enqueue_file_processing_defers_content_updates`` failed with
``await_args`` being ``None`` whenever an e2e suite ran first in the same
process -- three suites away from the cause, and green in CI, which runs the
lanes separately.

So the list is checked against the bootstrap rather than trusted, and the
exemption ("no production code reads it") is verified rather than declared.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = ROOT / "app" / "modules" / "test_support" / "e2e_base.py"


def _registered() -> set[str]:
    from conftest import _E2E_PROCESS_WIDE_HOOKS

    return {attribute for _module, _holder, attribute in _E2E_PROCESS_WIDE_HOOKS}


def _hooks_the_bootstrap_sets() -> set[str]:
    """Settings attributes named ``e2e_*`` that the bootstrap assigns."""
    tree = ast.parse(BOOTSTRAP.read_text(encoding="utf-8"), filename=str(BOOTSTRAP))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr.startswith("e2e_"):
                found.add(target.attr)
    return found


def _production_files():
    for path in (ROOT / "app").rglob("*.py"):
        parts = path.parts
        if "tests" in parts or "test_support" in parts:
            continue
        yield path


def _read_by_production(attribute: str) -> list[str]:
    """Production files that read ``attribute`` off a settings object."""
    pattern = re.compile(rf"settings\.{re.escape(attribute)}\b")
    return [
        path.relative_to(ROOT).as_posix()
        for path in _production_files()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]


def test_the_bootstrap_sets_hooks_and_the_isolation_list_is_not_empty() -> None:
    """Guard the guard: both halves have to find something to compare."""
    assert _hooks_the_bootstrap_sets()
    assert _registered()


@pytest.mark.parametrize("attribute", sorted(_hooks_the_bootstrap_sets()))
def test_a_hook_production_reads_is_isolated_from_non_e2e_tests(
    attribute: str,
) -> None:
    """Register it, or prove nothing outside the tests reads it.

    A hook that only the e2e suite reads can leak harmlessly, which is why
    ``e2e_sandbox_mode`` is not on the list. The moment production starts
    reading one, a unit test that merely runs after an e2e test starts taking
    the e2e branch -- so this fails here rather than three suites later.
    """
    readers = _read_by_production(attribute)
    if not readers:
        assert attribute not in _registered(), (
            f"`{attribute}` is isolated but nothing outside the tests reads it; "
            "drop it from _E2E_PROCESS_WIDE_HOOKS or say why it stays."
        )
        return
    assert attribute in _registered(), (
        f"`{attribute}` is set process-wide by the e2e bootstrap and read by "
        f"{', '.join(readers)}. Add it to _E2E_PROCESS_WIDE_HOOKS in "
        "conftest.py, or a unit test that happens to run after an e2e test "
        "will take the e2e branch."
    )


def test_this_test_sees_the_shipped_default_for_every_isolated_hook() -> None:
    """The isolation, observed rather than described.

    Compared against each field's declared default -- an independent source of
    truth from the session baseline the fixture restores to, so this is an
    assertion rather than a tautology. Passes trivially in a unit-only session
    and fails in the mixed one the fixture exists for.
    """
    from conftest import _E2E_PROCESS_WIDE_HOOKS
    from importlib import import_module

    for module, holder, attribute in _E2E_PROCESS_WIDE_HOOKS:
        obj = getattr(import_module(module), holder)
        declared = type(obj).model_fields[attribute].default
        assert getattr(obj, attribute) == declared, (
            f"`{attribute}` is {getattr(obj, attribute)!r} in a non-e2e test; "
            f"the e2e bootstrap leaked it and the isolation did not restore it."
        )
