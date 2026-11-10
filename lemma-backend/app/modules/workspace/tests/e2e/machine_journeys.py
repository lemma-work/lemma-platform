"""The journeys every machine must pass, whatever fabric it is on.

Written against the provider seam -- what the ``/machines`` routes, agent tools
and apps all go through -- so the same functions run against an E2B or Docker
sandbox and against a connected machine in host or image mode. The parity
suite runs them, unchanged, on each kind of connected machine.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx

from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.providers.base import ProviderInstance
from app.modules.workspace.providers.desktop_tunnel import sandbox_transport
from sandbox_runtime.protocol import (
    ByteRange,
    ProcessState,
    StartProcessRequest,
    TerminalSize,
)

PROJECT = "/home/user/lemma/parity"
_ENDED = {
    ProcessState.SUCCEEDED,
    ProcessState.FAILED,
    ProcessState.CANCELLED,
    ProcessState.TIMED_OUT,
}


def deadline(seconds: float = 60) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def _bytes(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def start(
    provider, instance: ProviderInstance, command: str, *, tty: bool = False
) -> str:
    return await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command=command,
            argv=None,
            cwd=PROJECT,
            environment=(),
            tty=TerminalSize(cols=80, rows=24) if tty else None,
            output_limit_bytes=1 << 20,
            deadline_at=deadline(600),
        ),
        deadline_at=deadline(),
    )


async def read_until(
    provider, instance, process_id: str, *, contains: bytes | None = None
) -> tuple[ProcessState, int | None, bytes]:
    output, after = b"", 0
    for _ in range(120):
        snapshot = await provider.read_process_output(
            instance,
            process_id=process_id,
            after_sequence=after,
            wait_seconds=1,
            deadline_at=deadline(),
        )
        for chunk in snapshot.chunks:
            output += chunk.data
            after = max(after, chunk.sequence)
        if contains is not None and contains in output:
            return snapshot.state, snapshot.exit_code, output
        if snapshot.state in _ENDED:
            return snapshot.state, snapshot.exit_code, output
    raise AssertionError(f"the process never got there: {output[-400:]!r}")


async def files(provider, instance) -> None:
    await provider.create_directory(instance, path=PROJECT, deadline_at=deadline())
    written = await provider.write_file(
        instance,
        path=f"{PROJECT}/notes.txt",
        data=_bytes(b"first line\nsecond line\n"),
        expected_sha256=None,
        deadline_at=deadline(),
    )
    assert written.path == f"{PROJECT}/notes.txt"
    assert written.size_bytes == 23
    ranged = [
        chunk
        async for chunk in provider.open_file(
            instance,
            path=f"{PROJECT}/notes.txt",
            byte_range=ByteRange(offset=11, length=6),
            deadline_at=deadline(),
        )
    ]
    assert b"".join(ranged) == b"second"
    await provider.move_file(
        instance,
        source=f"{PROJECT}/notes.txt",
        destination=f"{PROJECT}/moved.txt",
        deadline_at=deadline(),
    )
    names = {
        item.path
        for item in await provider.list_files(
            instance, path=PROJECT, deadline_at=deadline()
        )
    }
    assert f"{PROJECT}/moved.txt" in names
    assert f"{PROJECT}/notes.txt" not in names
    assert await provider.delete_file(
        instance, path=f"{PROJECT}/moved.txt", recursive=False, deadline_at=deadline()
    )
    assert not await provider.delete_file(
        instance, path=f"{PROJECT}/moved.txt", recursive=False, deadline_at=deadline()
    )


async def exec_and_stream(provider, instance) -> None:
    await provider.create_directory(instance, path=PROJECT, deadline_at=deadline())
    process_id = await start(
        provider, instance, "for i in 1 2 3; do echo line-$i; sleep 0.2; done; exit 3"
    )
    state, code, output = await read_until(provider, instance, process_id)
    assert state is ProcessState.FAILED, state
    assert code == 3
    # Only its own lines: on a host-mode machine the shell is the person's
    # login shell, and their profile may say things of its own.
    said = [line for line in output.decode().split() if line.startswith("line-")]
    assert said == ["line-1", "line-2", "line-3"]


async def stdin_on_a_terminal(provider, instance) -> None:
    await provider.create_directory(instance, path=PROJECT, deadline_at=deadline())
    process_id = await start(
        provider, instance, "read answer; echo got-$answer", tty=True
    )
    await asyncio.sleep(0.5)
    await provider.send_process_input(
        instance, process_id=process_id, data=b"yes\n", deadline_at=deadline()
    )
    _, _, output = await read_until(provider, instance, process_id, contains=b"got-yes")
    assert b"got-yes" in output


async def kill(provider, instance) -> None:
    await provider.create_directory(instance, path=PROJECT, deadline_at=deadline())
    process_id = await start(provider, instance, "echo started; sleep 600")
    await read_until(provider, instance, process_id, contains=b"started")
    listed = await provider.list_processes(instance, deadline_at=deadline())
    assert process_id in {item.process_id for item in listed}
    await provider.terminate_process(
        instance, process_id=process_id, grace_seconds=1, deadline_at=deadline()
    )
    state, _, _ = await read_until(provider, instance, process_id)
    assert state in _ENDED and state is not ProcessState.SUCCEEDED


async def preview(provider, instance, *, port: int) -> None:
    """A server the machine runs, reached the way a preview reaches it."""
    await provider.create_directory(instance, path=PROJECT, deadline_at=deadline())
    await provider.write_file(
        instance,
        path=f"{PROJECT}/index.html",
        data=_bytes(b"<h1>parity</h1>"),
        expected_sha256=None,
        deadline_at=deadline(),
    )
    process_id = await start(
        provider, instance, f"exec python3 -m http.server {port} --bind 127.0.0.1"
    )
    endpoint = await provider.reach_port(instance, port=port, deadline_at=deadline())
    try:
        async with httpx.AsyncClient(
            transport=sandbox_transport(), headers=dict(endpoint.headers), timeout=10
        ) as client:
            response = await eventually(
                label="the preview answers",
                probe=lambda: client.get(f"{endpoint.url}/index.html"),
                done=lambda answer: answer.status_code == 200,
                timeout_seconds=15,
                interval_seconds=0.2,
                retry_exceptions=(httpx.HTTPError,),
            )
        assert response.status_code == 200
        assert response.text == "<h1>parity</h1>"
    finally:
        await provider.terminate_process(
            instance, process_id=process_id, grace_seconds=1, deadline_at=deadline()
        )


JOURNEYS = (files, exec_and_stream, stdin_on_a_terminal, kill)
