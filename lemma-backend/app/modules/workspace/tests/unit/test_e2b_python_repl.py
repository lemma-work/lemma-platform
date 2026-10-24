"""The REPL promise, checked by running the runner E2B actually runs.

`execute_python` documents "session continuity: variables and imports defined
in one call are available in the next". On E2B there is no resident
interpreter, so each call is a fresh interpreter and the namespace is carried in
a pickle between them. Modules cannot be pickled, so every one of them was
silently dropped -- `import pandas as pd` bound a name that was gone by the
next call, and the agent got a bare `NameError` for something it had just
imported.

These execute the real `PYTHON_RUNNER` template in a subprocess, because the
bug lived in the generated script rather than in any Python this suite would
otherwise import.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import pickle
import subprocess
import sys

import pytest

from app.modules.workspace.providers.e2b_python_runner import PYTHON_RUNNER
from app.modules.workspace.providers.e2b_python_sessions import (
    python_command,
    python_session_dir,
)

_KEY = "this-sessions-key"


def _run(
    tmp_path: Path, code: str, *, key: str = _KEY, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """One `execute_python` call: same paths, so state carries between calls."""
    code_path = tmp_path / "code.py"
    code_path.write_text(code)
    runner_path = tmp_path / "runner.py"
    runner_path.write_text(
        PYTHON_RUNNER.format(
            key=key,
            state_path=str(tmp_path / "state.pkl"),
            code_path=str(code_path),
            result_path=str(tmp_path / "result.txt"),
            user_site=str(tmp_path / "user-site"),
        )
    )
    return subprocess.run(
        [sys.executable, "-I", str(runner_path)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd or tmp_path,
    )


def _sealed(payload: bytes, key: str = _KEY) -> bytes:
    return hmac.new(key.encode(), payload, hashlib.sha256).digest() + payload


class _Planted:
    """A pickle that runs code when it is loaded, as any pickle can."""

    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self):
        return (Path.touch, (self.marker,))


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
    state = tmp_path / "state.pkl"
    saved = pickle.loads(state.read_bytes()[32:])
    assert saved["modules"] == {"j": "json"}, "module names travel with the state"
    saved["modules"] = {"gone": "a_module_that_does_not_exist"}
    state.write_bytes(_sealed(pickle.dumps(saved)))

    result = _run(tmp_path, "print(value)")

    assert result.returncode == 0, result.stderr
    assert "3" in result.stdout


@pytest.mark.parametrize(
    "seal",
    [
        pytest.param(lambda payload: payload, id="unsealed"),
        pytest.param(lambda payload: _sealed(payload, "another-key"), id="other-key"),
    ],
)
def test_a_state_this_session_did_not_write_is_never_unpickled(
    tmp_path: Path, seal
) -> None:
    """Unpickling is running code, so only this session's own state is loaded.

    The state used to be ``/tmp/lemma-python-<session>.pkl``, and whatever was
    there was loaded: anything else in the sandbox could leave a pickle for
    another session's next call to run.
    """
    marker = tmp_path / "ran"
    (tmp_path / "state.pkl").write_bytes(
        seal(pickle.dumps({"names": {"x": _Planted(marker)}}))
    )

    result = _run(tmp_path, "print('x' in dir())")

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "the planted pickle ran"
    assert "False" in result.stdout
    assert "failed their integrity check" in result.stderr


def test_a_state_sealed_for_this_session_is_loaded(tmp_path: Path) -> None:
    """The other half: the check must not refuse the session's own writes."""
    _run(tmp_path, "kept = 'yes'")

    result = _run(tmp_path, "print(kept)")

    assert result.returncode == 0, result.stderr
    assert "yes" in result.stdout
    assert "integrity" not in result.stderr


def test_the_runner_and_its_code_are_gone_once_it_has_started(tmp_path: Path) -> None:
    """The runner carries the session's key, so it does not outlive its start."""
    result = _run(tmp_path, "print('ran')")

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "runner.py").exists()
    assert not (tmp_path / "code.py").exists()


def test_a_module_planted_in_the_working_directory_is_not_what_the_runner_imports(
    tmp_path: Path,
) -> None:
    """The working directory is the conversation's folder and the runner's was
    ``/tmp``, both of which anything in the sandbox can write. The runner's own
    imports must come from the interpreter."""
    cwd = tmp_path / "conversation"
    cwd.mkdir()
    marker = tmp_path / "shadowed"
    for directory in (cwd, tmp_path):
        (directory / "hmac.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    (cwd / "helper.py").write_text("VALUE = 7\n")

    result = _run(tmp_path, "import helper\nprint(helper.VALUE)", cwd=cwd)

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "the runner imported the planted hmac"
    assert "7" in result.stdout, "the user's code still imports beside its work"


def test_each_session_runs_in_a_private_directory_from_the_image_interpreter() -> None:
    """Not `python3` from a `PATH` the home leads, and not under bare `/tmp`."""
    directory = python_session_dir("abc")
    command = python_command(directory, f"{directory}/op.py")

    assert directory == "/tmp/lemma-sess/python-abc"
    assert python_session_dir("xyz") != directory
    assert "umask 077" in command
    assert f"/bin/chmod 700 {directory}" in command
    assert "_py=/opt/lemma-python/bin/python" in command
    assert f'exec "$_py" -I {directory}/op.py' in command
    assert "python3 " not in command


@pytest.mark.parametrize("literal", ["timeout=None", "timeout = None"])
def test_the_interpreter_run_is_bounded(literal: str) -> None:
    """`timeout_seconds` has to reach the sandbox, not just the backend wait.

    Unbounded, a runaway loop kept running after the tool returned, holding the
    only vCPU -- and the idle sweeper refuses to release a sandbox with live
    processes, so it stayed up around it.
    """
    source = Path("app/modules/workspace/providers/e2b_ops.py").read_text()
    assert literal not in source, "the python run must carry the request deadline"
