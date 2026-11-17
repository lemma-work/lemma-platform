"""Large files: a truncated read says so, and a sync caps every file."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.workspace.api.controllers import (
    machine_files_controller as controller,
)
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services import machine_file_sync
from sandbox_runtime.paths import WORKSPACE_ROOT

CEILING = controller._MAX_CONTENT_BYTES


class _Files:
    def __init__(self, size: int) -> None:
        self.size = size

    async def stat_file(self, machine_id, path, *, deadline_at):
        return SimpleNamespace(
            path=path,
            kind="file",
            size_bytes=self.size,
            sha256="whole-file-digest",
            modified_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )

    async def read_file(self, machine_id, path, *, deadline_at, offset=0, length=None):
        return b"x" * min(length or self.size, self.size - offset)


def _machine():
    return SimpleNamespace(machine=SimpleNamespace(id=uuid4()))


async def test_a_file_past_the_ceiling_is_a_206_without_the_whole_files_etag() -> None:
    total = CEILING + 100
    response = await controller.read_file(
        _machine(),
        _Files(total),
        path="big.bin",
        offset=0,
        length=None,
        range_header=None,
        if_none_match=None,
    )

    assert response.status_code == status.HTTP_206_PARTIAL_CONTENT
    assert response.headers["content-range"] == f"bytes 0-{CEILING - 1}/{total}"
    assert "etag" not in response.headers


async def test_a_whole_file_is_a_200_with_its_etag() -> None:
    response = await controller.read_file(
        _machine(),
        _Files(12),
        path="small.txt",
        offset=0,
        length=None,
        range_header=None,
        if_none_match=None,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["etag"] == '"whole-file-digest"'
    assert "content-range" not in response.headers


async def test_a_slice_asked_for_by_offset_is_a_206_naming_it() -> None:
    response = await controller.read_file(
        _machine(),
        _Files(100),
        path="a.txt",
        offset=10,
        length=20,
        range_header=None,
        if_none_match=None,
    )

    assert response.status_code == status.HTTP_206_PARTIAL_CONTENT
    assert response.headers["content-range"] == "bytes 10-29/100"


class _Machine:
    """A machine's files as the sync reaches them: one big file."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.read_bytes = 0

    async def stat_file(self, machine_id, path, *, deadline_at):
        return SimpleNamespace(kind="file", size_bytes=self.size)

    @asynccontextmanager
    async def stream_file(self, machine_id, path, *, deadline_at):
        async def chunks():
            for _ in range(self.size // 1024):
                self.read_bytes += 1024
                yield b"y" * 1024

        yield chunks()


async def test_a_single_file_past_the_cap_is_refused_before_it_is_read() -> None:
    machine = _Machine(10 * 1024 * 1024)

    with pytest.raises(MachineConflict) as refused:
        await machine_file_sync.sync_to_pod(
            None,  # type: ignore[arg-type]
            machine_id=uuid4(),
            user_id=uuid4(),
            pod_id=uuid4(),
            pod_folder="/out",
            machine_path=f"{WORKSPACE_ROOT}/big.bin",
            max_files=10,
            max_bytes=1024 * 1024,
            client=machine,
        )

    assert refused.value.code == "MACHINE_SYNC_TOO_LARGE"
    assert machine.read_bytes == 0


async def test_a_file_that_grows_past_the_cap_stops_streaming_at_it() -> None:
    machine = _Machine(10 * 1024 * 1024)

    with pytest.raises(MachineConflict):
        await machine_file_sync._read_capped(
            machine, uuid4(), "/x", room=64 * 1024, cap=64 * 1024
        )

    assert machine.read_bytes <= 65 * 1024
