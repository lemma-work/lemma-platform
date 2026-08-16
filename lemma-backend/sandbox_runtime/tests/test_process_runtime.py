from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shlex
import struct
import sys
from uuid import uuid4

import httpx
import pytest

from sandbox_runtime.workspace.app import create_app


pytestmark = pytest.mark.asyncio
TOKEN = "runtime-test-token"
HEADERS = {"X-Lemma-Runtime-Token": TOKEN}


def start_body(
    cwd: Path,
    command: str,
    *,
    operation_id=None,
    tty: dict[str, int] | None = None,
    initial_input: bytes | None = None,
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
        "initial_input_base64": (
            base64.b64encode(initial_input).decode()
            if initial_input is not None
            else None
        ),
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
    # Generous on purpose: this waits for a real forked process to be reaped,
    # and the property under test is that it becomes terminal at all. A tight
    # budget turns a loaded machine into a red build, which is how a timing
    # assertion teaches people to re-run rather than read.
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
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


async def test_initial_input_is_delivered_as_part_of_process_start(tmp_path: Path):
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        started = await client.post(
            "/processes",
            headers=HEADERS,
            json=start_body(
                tmp_path,
                "IFS= read -r ticket; printf 'ticket:%s' \"$ticket\"",
                initial_input=b"single-use-ticket\n",
            ),
        )
        assert started.status_code == 201
        operation_id = started.json()["operation_id"]
        terminal = await wait_for_terminal(client, operation_id)
        output = await client.get(
            f"/processes/{operation_id}/output?after_seq=0", headers=HEADERS
        )
        combined = b"".join(frame[2] for frame in decode_output(output.content))

    assert terminal["state"] == "succeeded"
    assert combined.endswith(b"ticket:single-use-ticket")


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
                    f"{shlex.quote(sys.executable)} -c "
                    + shlex.quote(
                        "import os,time; pid=os.fork(); "
                        "print(f'child:{pid}', flush=True) "
                        "if pid else time.sleep(60)"
                    )
                ),
            ),
        )
        operation_id = started.json()["operation_id"]
        terminal = await wait_for_terminal(client, operation_id)
        output = await client.get(
            f"/processes/{operation_id}/output?after_seq=0", headers=HEADERS
        )
        combined = b"".join(frame[2] for frame in decode_output(output.content))
        assert b"child:" in combined, (combined, terminal)
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
    # Same reasoning as wait_for_terminal: the claim is that the detached
    # descendant does not survive, not that the kernel reaps it inside one
    # second. The old 100 x 10ms budget failed on a machine that was busy
    # running other tests.
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("detached descendant survived exact process termination")
