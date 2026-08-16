"""The REPL promise, checked by running the runner E2B actually runs.

`execute_python` documents "session continuity: variables and imports defined
in one call are available in the next". On E2B there is no resident
interpreter, so each call is a fresh `python3` and the namespace is carried in
a pickle between them. Modules cannot be pickled, so every one of them was
silently dropped -- `import pandas as pd` bound a name that was gone by the
next call, and the agent got a bare `NameError` for something it had just
imported.

These execute the real `_PYTHON_RUNNER` template in a subprocess, because the
bug lived in the generated script rather than in any Python this suite would
otherwise import.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from app.modules.workspace.providers.e2b_python_runner import _PYTHON_RUNNER


def _run(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    """One `execute_python` call: same paths, so state carries between calls."""
    code_path = tmp_path / "code.py"
    code_path.write_text(code)
    runner_path = tmp_path / "runner.py"
    runner_path.write_text(
        _PYTHON_RUNNER.format(
            state_path=str(tmp_path / "state.pkl"),
            code_path=str(code_path),
            result_path=str(tmp_path / "result.txt"),
        )
    )
    return subprocess.run(
        [sys.executable, str(runner_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_an_imported_module_survives_into_the_next_call(tmp_path: Path) -> None:
    """The case that was broken, in the form an agent writes it."""
    first = _run(tmp_path, "import json as j\nprint('imported')")
    assert first.returncode == 0, first.stderr
    assert "imported" in first.stdout

    second = _run(tmp_path, "print(j.dumps({'ok': True}))")

    assert second.returncode == 0, second.stderr
    assert '{"ok": true}' in second.stdout
    assert "NameError" not in second.stderr


def test_a_submodule_import_keeps_the_name_it_was_bound_to(tmp_path: Path) -> None:
    """`import os.path as p` binds `p` to the submodule, not to `os`."""
    _run(tmp_path, "import os.path as p")

    result = _run(tmp_path, "print(p.basename('/a/b/c.txt'))")

    assert result.returncode == 0, result.stderr
    assert "c.txt" in result.stdout


def test_ordinary_values_still_carry(tmp_path: Path) -> None:
    """The behaviour that already worked must not regress."""
    _run(tmp_path, "total = 6 * 7\nname = 'lemma'")

    result = _run(tmp_path, "print(total, name)")

    assert result.returncode == 0, result.stderr
    assert "42 lemma" in result.stdout


def test_a_value_that_cannot_travel_is_dropped_rather_than_faked(
    tmp_path: Path,
) -> None:
    """An open file handle cannot cross a process boundary.

    Dropping it is right; the alternative is a name bound to something that
    would fail on use in a way that reads as a bug in the agent's own code.
    """
    _run(tmp_path, f"handle = open({str(tmp_path / 'f.txt')!r}, 'w')\nkept = 1")

    result = _run(tmp_path, "print('kept' in dir(), 'handle' in dir())")

    assert result.returncode == 0, result.stderr
    assert "True False" in result.stdout


def test_a_module_uninstalled_between_calls_does_not_break_the_session(
    tmp_path: Path,
) -> None:
    """Re-import is best effort: one missing module must not fail the call."""
    _run(tmp_path, "import json as j\nvalue = 3")
    modules = tmp_path / "state.pkl.modules"
    assert modules.exists(), "module names must be recorded beside the state"
    import pickle

    modules.write_bytes(pickle.dumps({"gone": "a_module_that_does_not_exist"}))

    result = _run(tmp_path, "print(value)")

    assert result.returncode == 0, result.stderr
    assert "3" in result.stdout


@pytest.mark.parametrize("literal", ["timeout=None", "timeout = None"])
def test_the_interpreter_run_is_bounded(literal: str) -> None:
    """`timeout_seconds` has to reach the sandbox, not just the backend wait.

    Unbounded, a runaway loop kept running after the tool returned, holding the
    only vCPU -- and the idle sweeper refuses to release a sandbox with live
    processes, so it stayed up around it.
    """
    source = Path("app/modules/workspace/providers/e2b_ops.py").read_text()
    assert literal not in source, "the python run must carry the request deadline"
