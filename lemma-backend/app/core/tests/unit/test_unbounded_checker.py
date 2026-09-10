"""The gate that keeps the unbounded-resource category closed.

Written against the four shapes from the incident: a queue with no ceiling, a
cache keyed by caller input with no ceiling, and CPU-heavy calls made on the
event loop.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "check_unbounded.py"


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location("check_unbounded", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_unbounded"] = module
    spec.loader.exec_module(module)
    return module


def _violations(checker, tmp_path: Path, source: str) -> list[str]:
    path = tmp_path / "subject.py"
    path.write_text(source, encoding="utf-8")
    monkey = checker.ROOT
    try:
        checker.ROOT = tmp_path
        return [f"{v.rule}:{v.detail}" for v in checker.collect([path])]
    finally:
        checker.ROOT = monkey


def test_a_queue_without_a_ceiling_is_a_leak_with_a_producer(checker, tmp_path):
    found = _violations(
        checker,
        tmp_path,
        "import asyncio\n\nasync def f():\n    q = asyncio.Queue()\n",
    )

    assert "unbounded-queue:asyncio.Queue" in found


def test_a_bounded_queue_is_the_fix_not_the_offence(checker, tmp_path):
    found = _violations(
        checker,
        tmp_path,
        "import asyncio\n\nasync def f():\n    q = asyncio.Queue(maxsize=256)\n",
    )

    assert found == []


def test_a_cache_keyed_by_caller_input_needs_a_ceiling(checker, tmp_path):
    found = _violations(
        checker,
        tmp_path,
        "from functools import lru_cache\n\n@lru_cache\ndef f(key):\n    return key\n",
    )

    assert "unbounded-cache:lru_cache" in found


def test_argument_free_memoization_is_a_singleton_and_fine(checker, tmp_path):
    found = _violations(
        checker,
        tmp_path,
        "from functools import lru_cache\n\n@lru_cache\ndef f():\n    return 1\n",
    )

    assert found == []


def test_a_slow_call_on_the_loop_is_reported(checker, tmp_path):
    found = _violations(
        checker,
        tmp_path,
        "import importlib\n\nasync def f(n):\n    importlib.import_module(n)\n",
    )

    assert "cpu-on-loop:importlib.import_module" in found


def test_the_same_call_in_a_sync_helper_is_another_gate_s_job(checker, tmp_path):
    """Scope stated in the docstring, pinned here so it is not widened by accident."""
    found = _violations(
        checker,
        tmp_path,
        "import importlib\n\ndef f(n):\n    importlib.import_module(n)\n",
    )

    assert found == []


def test_the_repository_baseline_is_honoured(checker):
    """The gate must be green on the tree it ships with."""
    baseline = checker._load_baseline(checker.DEFAULT_BASELINE)

    assert baseline, "baseline missing — run --update-baseline"
    assert all(count > 0 for count in baseline.values())
