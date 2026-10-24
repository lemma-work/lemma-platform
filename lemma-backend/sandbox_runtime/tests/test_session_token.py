"""A session's token, written where its environment says to look.

A workspace session's token lives minutes; ``LEMMA_TOKEN_FILE`` is how a fresh
one reaches a process that is already running, so the runtime rewrites it
before every command and every Python call.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4

import httpx
import pytest

from sandbox_runtime.paths import SESSION_ROOT
from sandbox_runtime.workspace.app import create_app
from sandbox_runtime.workspace.session_token import deliver_token_file

TOKEN = "runtime-session-token-test"
HEADERS = {"X-Lemma-Runtime-Token": TOKEN}


@pytest.fixture
def session_dir() -> Iterator[Path]:
    directory = Path(SESSION_ROOT) / f"test-{uuid4().hex}"
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_token_lands_owner_only_in_a_private_directory(session_dir: Path) -> None:
    target = session_dir / "lemma-token"

    written = deliver_token_file(
        {"LEMMA_TOKEN": "first", "LEMMA_TOKEN_FILE": str(target)}
    )

    assert written
    assert target.read_text() == "first"
    assert _mode(target) == 0o600
    assert _mode(session_dir) == 0o700
    assert deliver_token_file(
        {"LEMMA_TOKEN": "second", "LEMMA_TOKEN_FILE": str(target)}
    )
    assert target.read_text() == "second", "a refresh rewrites it in place"
    assert sorted(os.listdir(session_dir)) == ["lemma-token"], "no staging left"


@pytest.mark.parametrize(
    "name",
    [
        "/home/user/.bashrc",
        f"{SESSION_ROOT}/lemma-token",
        f"{SESSION_ROOT}/a/b/lemma-token",
        f"{SESSION_ROOT}/a/other-name",
        f"{SESSION_ROOT}/../lemma-token",
    ],
)
def test_nothing_but_a_session_token_path_is_written(name: str) -> None:
    """The runtime writes where a request's environment says: only one shape."""
    assert not deliver_token_file({"LEMMA_TOKEN": "t", "LEMMA_TOKEN_FILE": name})


def test_an_environment_without_both_names_writes_nothing(session_dir: Path) -> None:
    target = session_dir / "lemma-token"
    assert not deliver_token_file({"LEMMA_TOKEN_FILE": str(target)})
    assert not deliver_token_file({"LEMMA_TOKEN": "t"})
    assert not target.exists()


@pytest.mark.asyncio
async def test_every_command_and_python_call_refreshes_the_file(
    tmp_path: Path, session_dir: Path
) -> None:
    target = session_dir / "lemma-token"
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    session_id = uuid4()

    def environment(token: str) -> list[dict[str, str]]:
        return [
            {"name": "LEMMA_TOKEN", "value": token},
            {"name": "LEMMA_TOKEN_FILE", "value": str(target)},
        ]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        started = await client.post(
            "/processes",
            headers=HEADERS,
            json={
                "operation_id": str(uuid4()),
                "shell_command": "true",
                "argv": None,
                "cwd": str(tmp_path),
                "environment": environment("from-a-command"),
                "tty": None,
                "output_limit_bytes": 1024,
                "deadline_at": deadline,
            },
        )
        after_command = target.read_text() if target.exists() else None
        await client.put(
            f"/python-sessions/{session_id}",
            headers=HEADERS,
            json={
                "cwd": str(tmp_path),
                "environment_keys": ["LEMMA_TOKEN", "LEMMA_TOKEN_FILE"],
                "deadline_at": deadline,
            },
        )
        ran = await client.post(
            f"/python-sessions/{session_id}:execute",
            headers=HEADERS,
            json={
                "operation_id": str(uuid4()),
                "code": f"open({str(target)!r}).read()",
                "environment": environment("from-python"),
                "output_limit_bytes": 1024,
                "deadline_at": deadline,
            },
        )
        await client.delete(f"/python-sessions/{session_id}", headers=HEADERS)

    assert started.status_code < 300, started.text
    assert after_command == "from-a-command"
    assert ran.json()["result"] == "'from-python'", "written before the code ran"
