"""The workspace image's runtime, started as a container starts it, against a
home somebody else has written to.

Every session of every agent runs as the sandbox's one Linux user, and on
Docker the home outlives the container, so anything in it can have been left
there by any of them. The runtime and each Python worker see every session's
delegated token pass through them; these check, in the real image, that neither
imports from the home before its own code, and that the runtime rewrites each
session's token file -- owner-only, in a private directory -- with the token
every command and Python call arrives with.

No backend: the runtime is driven over its own HTTP API from inside the
container, the way the backend drives it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(600)]

_SANDBOX_UID = "10001:10001"
_RUNTIME_TOKEN = "e2e-runtime-token"
_USER_SITE = "/home/user/.python/lib/python3.14/site-packages"
_MARKER = "/tmp/planted-imports"
_SESSION_DIR = "/tmp/lemma-sess/shell-e2e"
_TOKEN_FILE = f"{_SESSION_DIR}/lemma-token"

#: What the home is made to say. A `sitecustomize` is imported by every
#: interpreter that has the directory on `sys.path` when `site` runs, before
#: any of its own code: it records which process imported it.
_PLANTED = (
    "import os\n"
    "with open('/proc/self/cmdline', 'rb') as f:\n"
    "    line = f.read().replace(b'\\0', b' ').decode()\n"
    f"with open({_MARKER!r}, 'a') as f:\n"
    "    f.write(line + '\\n')\n"
)


def _docker(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, check=False
    )


def _exec(container: str, *argv: str, stdin: bytes | None = None):
    return _docker("exec", "-i", "-u", _SANDBOX_UID, container, *argv, stdin=stdin)


def _write(container: str, path: str, data: str) -> None:
    written = _exec(
        container,
        "sh",
        "-c",
        f'mkdir -p "$(dirname {path})" && cat > {path}',
        stdin=data.encode(),
    )
    assert written.returncode == 0, written.stderr.decode()


def _api(container: str, method: str, path: str, body: object | None = None):
    argv = [
        "curl",
        "-s",
        "-X",
        method,
        "-H",
        f"X-Lemma-Runtime-Token: {_RUNTIME_TOKEN}",
        "-H",
        "Content-Type: application/json",
        "-w",
        "\n%{http_code}",
        f"http://127.0.0.1:8080{path}",
    ]
    if body is not None:
        argv[-1:-1] = ["--data-binary", "@-"]
    result = _exec(
        container, *argv, stdin=json.dumps(body).encode() if body is not None else None
    )
    text, _, status = result.stdout.decode().rpartition("\n")
    return int(status or 0), (json.loads(text) if text.strip() else None)


def _deadline(seconds: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _environment(token: str) -> list[dict[str, str]]:
    return [
        {"name": "LEMMA_TOKEN", "value": token},
        {"name": "LEMMA_TOKEN_FILE", "value": _TOKEN_FILE},
    ]


@pytest.fixture(scope="module")
def runtime(workspace_image: str) -> Iterator[str]:
    name = f"lemma-token-e2e-{uuid.uuid4().hex[:10]}"
    started = _docker(
        "run",
        "-d",
        "--rm",
        "--network",
        "none",
        "--name",
        name,
        "--entrypoint",
        "sleep",
        workspace_image,
        "infinity",
    )
    assert started.returncode == 0, started.stderr.decode()
    try:
        _write(name, f"{_USER_SITE}/sitecustomize.py", _PLANTED)
        _write(name, f"{_USER_SITE}/userpkg.py", "VALUE = 'from-the-home'\n")
        _write(name, "/run/lemma-bootstrap/token", _RUNTIME_TOKEN)
        launched = _docker(
            "exec", "-d", "-u", _SANDBOX_UID, name, "start-workspace-runtime"
        )
        assert launched.returncode == 0, launched.stderr.decode()

        async def health() -> int:
            return _api(name, "GET", "/health")[0]

        asyncio.run(
            eventually(
                label="the workspace runtime answering",
                probe=health,
                done=lambda status: status == 200,
                timeout_seconds=30,
            )
        )
        yield name
    finally:
        _docker("rm", "-f", name)


def _planted_imports(container: str) -> str:
    result = _exec(container, "sh", "-c", f"cat {_MARKER} 2>/dev/null || true")
    return result.stdout.decode()


def test_the_runtime_runs_the_images_interpreter_isolated(runtime: str) -> None:
    processes = _exec(runtime, "ps", "-eo", "args").stdout.decode()

    assert (
        "/opt/lemma-python/bin/python -I -m uvicorn sandbox_runtime.workspace.server:app"
        in processes
    ), processes
    assert "uvicorn" not in _planted_imports(runtime), (
        "the runtime imported a module the home planted"
    )


def test_a_python_worker_imports_its_own_code_and_then_opens_the_users(
    runtime: str,
) -> None:
    session_id = str(uuid.uuid4())
    status, _ = _api(
        runtime,
        "PUT",
        f"/python-sessions/{session_id}",
        {"cwd": "/home/user/lemma", "environment_keys": [], "deadline_at": _deadline()},
    )
    assert status in (200, 201)
    status, result = _api(
        runtime,
        "POST",
        f"/python-sessions/{session_id}:execute",
        {
            "operation_id": str(uuid.uuid4()),
            "code": "import sys, userpkg\n(sys.flags.isolated, userpkg.VALUE)",
            "environment": [],
            "output_limit_bytes": 4096,
            "deadline_at": _deadline(),
        },
    )
    _api(runtime, "DELETE", f"/python-sessions/{session_id}")

    assert status == 200 and result["state"] == "succeeded", result
    assert result["result"] == "(1, 'from-the-home')", (
        "the user's code keeps the packages they installed"
    )
    assert "python_worker" not in _planted_imports(runtime), (
        "the worker imported a module the home planted"
    )


def test_each_command_and_python_call_rewrites_the_sessions_token_file(
    runtime: str,
) -> None:
    status, _ = _api(
        runtime,
        "POST",
        "/processes",
        {
            "operation_id": str(uuid.uuid4()),
            "shell_command": "true",
            "argv": None,
            "cwd": "/home/user/lemma",
            "environment": _environment("from-a-command"),
            "tty": None,
            "output_limit_bytes": 1024,
            "deadline_at": _deadline(),
        },
    )
    assert status in (200, 201)
    described = _exec(
        runtime, "stat", "-c", "%a %u %n", _SESSION_DIR, _TOKEN_FILE
    ).stdout.decode()
    first = _exec(runtime, "cat", _TOKEN_FILE).stdout.decode()

    session_id = str(uuid.uuid4())
    _api(
        runtime,
        "PUT",
        f"/python-sessions/{session_id}",
        {
            "cwd": "/home/user/lemma",
            "environment_keys": ["LEMMA_TOKEN", "LEMMA_TOKEN_FILE"],
            "deadline_at": _deadline(),
        },
    )
    status, result = _api(
        runtime,
        "POST",
        f"/python-sessions/{session_id}:execute",
        {
            "operation_id": str(uuid.uuid4()),
            "code": ("from lemma_sdk.config import _token_from_env\n_token_from_env()"),
            "environment": _environment("from-python"),
            "output_limit_bytes": 4096,
            "deadline_at": _deadline(),
        },
    )
    _api(runtime, "DELETE", f"/python-sessions/{session_id}")

    assert described.splitlines() == [
        f"700 10001 {_SESSION_DIR}",
        f"600 10001 {_TOKEN_FILE}",
    ]
    assert first == "from-a-command"
    assert status == 200 and result["result"] == "'from-python'", result
