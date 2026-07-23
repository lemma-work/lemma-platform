from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import struct
from uuid import uuid4

import httpx
import pytest

from agentbox.workspace_runtime.app import create_app


pytestmark = pytest.mark.asyncio
TOKEN = "runtime-test-token"
HEADERS = {"X-AgentBox-Runtime-Token": TOKEN}


def start_body(
    cwd: Path,
    command: str,
    *,
    operation_id=None,
    tty: dict[str, int] | None = None,
) -> dict[str, object]:
    return {
        "operation_id": str(operation_id or uuid4()),
        "shell_command": command,
        "argv": None,
        "cwd": str(cwd),
        "environment": [],
        "tty": tty,
        "output_limit_bytes": 65536,
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
    }


def decode_output(content: bytes) -> list[tuple[int, int, bytes]]:
    frames: list[tuple[int, int, bytes]] = []
    offset = 0
    while offset < len(content):
        sequence, channel, size = struct.unpack_from("!QBI", content, offset)
        offset += 13
        frames.append((sequence, channel, content[offset : offset + size]))
        offset += size
    return frames


async def wait_for_terminal(
    client: httpx.AsyncClient, operation_id: str
) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(f"/processes/{operation_id}", headers=HEADERS)
        payload = response.json()
        if payload["state"] != "running":
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("process did not become terminal")


async def test_shell_output_is_binary_framed_and_reconnectable(tmp_path: Path):
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        started = await client.post(
            "/processes",
            headers=HEADERS,
            json=start_body(tmp_path, "printf 'alpha'; printf 'beta' >&2"),
        )
        assert started.status_code == 201
        operation_id = started.json()["operation_id"]
        terminal = await wait_for_terminal(client, operation_id)
        first = await client.get(
            f"/processes/{operation_id}/output?after_seq=0", headers=HEADERS
        )
        frames = decode_output(first.content)
        last_sequence = max(frame[0] for frame in frames)
        reconnected = await client.get(
            f"/processes/{operation_id}/output?after_seq={last_sequence}",
            headers=HEADERS,
        )

    assert terminal["state"] == "succeeded"
    assert terminal["exit_code"] == 0
    assert b"alpha" in b"".join(frame[2] for frame in frames if frame[1] == 1)
    assert b"beta" in b"".join(frame[2] for frame in frames if frame[1] == 2)
    assert reconnected.content == b""


async def test_pty_input_and_resize(tmp_path: Path):
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        body = start_body(
            tmp_path,
            'read line; size=$(stty size); printf \'got:%s size:%s\\n\' "$line" "$size"',
            tty={"cols": 80, "rows": 24},
        )
        started = await client.post("/processes", headers=HEADERS, json=body)
        operation_id = started.json()["operation_id"]
        resized = await client.post(
            f"/processes/{operation_id}:resize",
            headers=HEADERS,
            json={"cols": 100, "rows": 40},
        )
        sent = await client.post(
            f"/processes/{operation_id}:input",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            content=b"hello\n",
        )
        terminal = await wait_for_terminal(client, operation_id)
        output = await client.get(
            f"/processes/{operation_id}/output?after_seq=0", headers=HEADERS
        )
        combined = b"".join(frame[2] for frame in decode_output(output.content))

    assert resized.status_code == 204
    assert sent.status_code == 204
    assert terminal["state"] == "succeeded"
    assert b"got:hello size:40 100" in combined


async def test_terminate_kills_process_group_and_requires_auth(tmp_path: Path):
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        unauthorized = await client.get("/health")
        started = await client.post(
            "/processes",
            headers=HEADERS,
            json=start_body(tmp_path, "sleep 60 & echo child:$!; wait"),
        )
        operation_id = started.json()["operation_id"]
        combined = b""
        for _ in range(100):
            output = await client.get(
                f"/processes/{operation_id}/output?after_seq=0&wait_seconds=0.1",
                headers=HEADERS,
            )
            combined = b"".join(frame[2] for frame in decode_output(output.content))
            if b"child:" in combined:
                break
            await asyncio.sleep(0.01)
        assert b"child:" in combined
        child_pid = int(combined.split(b"child:", 1)[1].splitlines()[0])
        terminated = await client.request(
            "DELETE",
            f"/processes/{operation_id}",
            headers=HEADERS,
            json={"grace_seconds": 0.1},
        )

    assert unauthorized.status_code == 401
    assert terminated.status_code == 200
    assert terminated.json()["state"] == "cancelled"
    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("descendant process survived group termination")


async def test_direct_exit_is_terminal_when_descendant_holds_output_pipe(
    tmp_path: Path,
):
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    child_pid = None
    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        started = await client.post(
            "/processes",
            headers=HEADERS,
            json=start_body(
                tmp_path,
                (
                    'python -c "import os,time; pid=os.fork(); '
                    "print(f'child:{pid}', flush=True) if pid else time.sleep(60)\""
                ),
            ),
        )
        operation_id = started.json()["operation_id"]
        terminal = await wait_for_terminal(client, operation_id)
        output = await client.get(
            f"/processes/{operation_id}/output?after_seq=0", headers=HEADERS
        )
        combined = b"".join(frame[2] for frame in decode_output(output.content))
        child_pid = int(combined.split(b"child:", 1)[1].splitlines()[0])
        terminated = await client.request(
            "DELETE",
            f"/processes/{operation_id}",
            headers=HEADERS,
            json={"grace_seconds": 0.1},
        )

    assert terminal["state"] == "succeeded"
    assert terminated.json()["state"] == "succeeded"
    assert child_pid is not None
    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("detached descendant survived exact process termination")
