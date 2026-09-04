"""Skip a test when its prerequisite is genuinely absent — but never in CI.

A test whose prerequisite is missing should say which one, not fail as whatever
its first timeout happens to be. Before this,
``test_desktop_local_journey_converts_and_indexes_with_the_real_xberg_wheel``
had no guard at all: without the optional ``xberg`` wheel it waited ninety
seconds and reported "Timed out waiting for file /real-paper.pdf to reach
['COMPLETED']", which names neither the cause nor the fix.

The reason this is a helper rather than a bare ``pytest.skip`` is the other half
of the problem. A skipped test is silent, and silence in CI is how coverage
disappears without anyone deciding to drop it: CI installs these prerequisites
on purpose (``uv sync --extra local`` in e2e.yml exists for that xberg test), so
their absence there is a regression in the workflow, not a reason to skip. Skip
locally, fail loudly in CI.
"""

from __future__ import annotations

import importlib.util
import os

import pytest


def _in_ci() -> bool:
    # GitHub Actions sets CI=true; so does essentially every other runner.
    return os.getenv("CI", "").lower() in {"1", "true", "yes"}


def require(*, available: bool, what: str, fix: str) -> None:
    """Skip unless ``available``; in CI, fail instead."""
    if available:
        return
    if _in_ci():
        pytest.fail(
            f"{what} is unavailable, but CI installs it deliberately — so this "
            f"is a regression in the workflow, not a reason to skip. {fix}"
        )
    pytest.skip(f"{what} is unavailable. {fix}")


def require_module(name: str, *, fix: str) -> None:
    """Skip unless an optional dependency is importable; in CI, fail."""
    require(
        available=importlib.util.find_spec(name) is not None,
        what=f"the optional '{name}' package",
        fix=fix,
    )
