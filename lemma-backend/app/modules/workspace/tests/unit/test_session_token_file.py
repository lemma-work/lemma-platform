"""Where a session's token file is, and the shell that writes it on E2B."""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import shutil
import stat
import subprocess
from uuid import uuid4

import pytest

from app.modules.workspace.contracts.session_token_file import (
    SESSION_ROOT,
    TOKEN_FILE_SHELL_PREFIX,
    session_token_dir,
    session_token_path,
    with_token_file,
)
from sandbox_runtime.workspace.session_token import _session_token_path

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "session_id", ["shell-abc", "../../etc", "a/b", "x" * 200, "python-3f2a"]
)
def test_every_session_id_gets_a_path_the_runtime_agrees_to_write(
    session_id: str,
) -> None:
    path = session_token_path(session_id)

    assert path.startswith(f"{SESSION_ROOT}/")
    assert _session_token_path(path) is not None
    assert ".." not in path.split("/")


def test_two_sessions_never_share_a_directory() -> None:
    assert session_token_dir("a/b") != session_token_dir("a_b")
    assert session_token_path("one") != session_token_path("two")


def test_only_an_environment_with_both_names_gets_the_prefix() -> None:
    both = {"LEMMA_TOKEN": "t", "LEMMA_TOKEN_FILE": session_token_path("s")}

    assert with_token_file("ls", both) == TOKEN_FILE_SHELL_PREFIX + "ls"
    assert with_token_file("ls", {"LEMMA_TOKEN": "t"}) == "ls"


@pytest.fixture
def session_id() -> Iterator[str]:
    value = f"test-{uuid4().hex}"
    yield value
    shutil.rmtree(session_token_dir(value), ignore_errors=True)


def _run(command: str, environment: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", command],
        env={"PATH": os.environ.get("PATH", ""), **environment},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_the_prefix_writes_the_token_privately_then_runs_the_command(
    session_id: str,
) -> None:
    target = Path(session_token_path(session_id))

    result = _run(
        TOKEN_FILE_SHELL_PREFIX + 'cat "$LEMMA_TOKEN_FILE"',
        {"LEMMA_TOKEN": "fresh", "LEMMA_TOKEN_FILE": str(target)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "fresh"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert os.listdir(target.parent) == ["lemma-token"]


def test_the_prefix_writes_nowhere_else_and_never_fails_the_command(
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "lemma-token"

    result = _run(
        TOKEN_FILE_SHELL_PREFIX + "echo ran",
        {"LEMMA_TOKEN": "t", "LEMMA_TOKEN_FILE": str(elsewhere)},
    )
    traversal = _run(
        TOKEN_FILE_SHELL_PREFIX + "echo ran",
        {
            "LEMMA_TOKEN": "t",
            "LEMMA_TOKEN_FILE": f"{SESSION_ROOT}/../{tmp_path.name}/lemma-token",
        },
    )

    assert (result.stdout, traversal.stdout) == ("ran\n", "ran\n")
    assert not elsewhere.exists()
