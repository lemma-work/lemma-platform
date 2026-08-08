from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

import httpx
import pytest

from sandbox_runtime.workspace.app import create_app


pytestmark = pytest.mark.asyncio
TOKEN = "runtime-python-test-token"
HEADERS = {"X-AgentBox-Runtime-Token": TOKEN}


def deadline(seconds: float = 10) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def create_body(cwd: Path, marker: str) -> dict[str, object]:
    del marker
    return {
        "cwd": str(cwd),
        "environment_keys": ["SESSION_MARKER"],
        "deadline_at": deadline(),
    }


def execute_body(
    code: str,
    *,
    operation_id: UUID | None = None,
    seconds: float = 10,
    marker: str | None = None,
) -> dict[str, object]:
    return {
        "operation_id": str(operation_id or uuid4()),
        "code": code,
        "environment": (
            [{"name": "SESSION_MARKER", "value": marker}] if marker is not None else []
        ),
        "output_limit_bytes": 65536,
        "deadline_at": deadline(seconds),
    }


async def test_python_sessions_are_stateful_isolated_concurrent_and_restartable(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_id = uuid4()
    second_id = uuid4()
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        first_created = await client.put(
            f"/python-sessions/{first_id}",
            headers=HEADERS,
            json=create_body(first_dir, "alpha"),
        )
        second_created = await client.put(
            f"/python-sessions/{second_id}",
            headers=HEADERS,
            json=create_body(second_dir, "beta"),
        )
        await client.post(
            f"/python-sessions/{first_id}:execute",
            headers=HEADERS,
            json=execute_body("value = 41"),
        )
        stateful = await client.post(
            f"/python-sessions/{first_id}:execute",
            headers=HEADERS,
            json=execute_body(
                "import os, subprocess, sys\n"
                "os.write(1, b'native-out\\n')\n"
                "subprocess.run([sys.executable, '-c', "
                "\"import os; os.write(2, b'child-err\\\\n')\"], check=True)\n"
                "(value + 1, os.environ['SESSION_MARKER'], os.getcwd())",
                marker="alpha",
            ),
        )

        started = time.monotonic()
        first_overlap, second_overlap = await asyncio.gather(
            client.post(
                f"/python-sessions/{first_id}:execute",
                headers=HEADERS,
                json=execute_body("import time; time.sleep(0.3); 'first'"),
            ),
            client.post(
                f"/python-sessions/{second_id}:execute",
                headers=HEADERS,
                json=execute_body("import time; time.sleep(0.3); 'second'"),
            ),
        )
        elapsed = time.monotonic() - started
        restarted = await client.post(
            f"/python-sessions/{first_id}:restart", headers=HEADERS
        )
        reset = await client.post(
            f"/python-sessions/{first_id}:execute",
            headers=HEADERS,
            json=execute_body("value"),
        )
        quiesced = await client.post("/quiesce", headers=HEADERS)

    assert first_created.status_code == 201
    assert second_created.status_code == 201
    assert stateful.status_code == 200
    assert stateful.json()["state"] == "succeeded"
    assert stateful.json()["stdout"] == "native-out\n"
    assert stateful.json()["stderr"] == "child-err\n"
    assert "42" in stateful.json()["result"]
    assert "alpha" in stateful.json()["result"]
    assert str(first_dir) in stateful.json()["result"]
    assert elapsed < 0.55
    assert first_overlap.json()["result"] == "'first'"
    assert second_overlap.json()["result"] == "'second'"
    assert restarted.status_code == 200
    assert reset.json()["state"] == "failed"
    assert reset.json()["error_name"] == "NameError"
    assert quiesced.json()["terminated_python_sessions"] == 2


async def test_python_timeout_resets_only_that_worker_and_delete_kills_descendants(
    tmp_path: Path,
) -> None:
    session_id = uuid4()
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        await client.put(
            f"/python-sessions/{session_id}",
            headers=HEADERS,
            json=create_body(tmp_path, "timeout"),
        )
        await client.post(
            f"/python-sessions/{session_id}:execute",
            headers=HEADERS,
            json=execute_body("sentinel = 42"),
        )
        timed_out = await client.post(
            f"/python-sessions/{session_id}:execute",
            headers=HEADERS,
            json=execute_body("import time; time.sleep(30)", seconds=0.2),
        )
        reset = await client.post(
            f"/python-sessions/{session_id}:execute",
            headers=HEADERS,
            json=execute_body("sentinel"),
        )
        child = await client.post(
            f"/python-sessions/{session_id}:execute",
            headers=HEADERS,
            json=execute_body(
                "import subprocess\nchild = subprocess.Popen(['sleep', '30'])\nchild.pid"
            ),
        )
        child_pid = int(child.json()["result"])
        deleted = await client.delete(f"/python-sessions/{session_id}", headers=HEADERS)

    assert timed_out.status_code == 200
    assert timed_out.json()["state"] == "timed_out"
    assert reset.json()["error_name"] == "NameError"
    assert deleted.status_code == 204
    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("Python session descendant survived deletion")
