"""Guards on the suite itself.

These are the rules that make the suite worth having. They are enforced here,
in the suite, rather than in a lint script, because they are about what a
scenario *is* — and because breaking one is easy to do by accident and
impossible to spot in review once there are two hundred journeys.

They need no stack, so they run in milliseconds and fail before anything is
booted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parents[1]
JOURNEYS = SUITE / "journeys"

#: Importing the application under test would make this a unit test wearing a
#: black-box costume: it could pass against code paths no real client can reach.
FORBIDDEN_ROOTS = {"app", "sandbox_runtime", "lemma_backend"}

#: A scenario that patches something is asserting against a system that does not
#: exist in production. The only substitutions permitted are the ones the stack
#: itself is booted with, chosen in harness/stack.py and visible to everyone.
FORBIDDEN_NAMES = {"monkeypatch", "MagicMock", "AsyncMock", "mock", "patch"}


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in SUITE.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    )


def _scenario_files() -> list[Path]:
    """Journey files, excluding this one — these guards are not scenarios."""
    return sorted(
        path for path in JOURNEYS.rglob("test_*.py") if path != Path(__file__).resolve()
    )


def test_suite_is_black_box():
    """No file in the suite imports the application under test."""
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root in FORBIDDEN_ROOTS:
                    offenders.append(
                        f"{path.relative_to(SUITE)}:{node.lineno} imports {root!r}"
                    )
    assert not offenders, (
        "the scenario suite must reach Lemma only over HTTP:\n  "
        + "\n  ".join(offenders)
    )


def test_scenarios_do_not_mock():
    """No scenario substitutes part of the system it is testing."""
    offenders: list[str] = []
    for path in _scenario_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.arg):
                name = node.arg
            if name in FORBIDDEN_NAMES:
                offenders.append(
                    f"{path.relative_to(SUITE)}:{node.lineno} uses {name!r}"
                )
    assert not offenders, (
        "a scenario asserts against the real system, never a substituted one:\n  "
        + "\n  ".join(offenders)
    )


def test_scenarios_do_not_sleep():
    """No scenario waits by sleeping.

    A sleep is either too short (flaky under load) or too long (everyone pays
    for it, every run). Waiting is what the harness waiters are for, and they
    fail with what they were waiting for and what they last saw.
    """
    offenders: list[str] = []
    for path in _scenario_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Attribute) and function.attr == "sleep":
                    offenders.append(f"{path.relative_to(SUITE)}:{node.lineno}")
    assert not offenders, (
        "scenarios must wait on a condition, not on the clock:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", _scenario_files(), ids=lambda p: p.name)
def test_every_scenario_declares_what_it_proves(path: Path):
    """Every test in a journey carries @scenario and @proves.

    Without both, the test is invisible to the coverage document and to the
    gates — it runs, it passes, and it contributes nothing to knowing whether
    the product does what it says.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        decorators = {
            decorator.func.id
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
        }
        absent = {"scenario", "proves"} - decorators
        if absent:
            missing.append(f"{node.name} is missing {', '.join(sorted(absent))}")
    assert not missing, f"{path.name}:\n  " + "\n  ".join(missing)
