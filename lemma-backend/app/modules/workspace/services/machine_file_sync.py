"""Copying files between a machine and a pod's files, without a round trip.

Authorized twice, on purpose: the caller must be able to use the machine (the
route resolved it) *and* the pod files are read or written as the caller, with
their own context in that pod -- a machine is not a way into a pod's files
somebody could not otherwise reach.

Capped both ways and per file, not only per tree: a single file is measured
before it is read, and read as a stream that stops at the cap, so one large
file cannot be pulled whole into the API's memory.
"""

from __future__ import annotations

import posixpath
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.providers.runtime_errors import (
    WorkspaceRuntimeFileNotFound,
)
from sandbox_runtime.errors import SandboxPathNotFound
from sandbox_runtime.protocol import FileKind


#: The most one file copied onto a machine from a pod may be.
MAX_FROM_POD_BYTES = 256 * 1024 * 1024


def _too_large(max_bytes: int) -> MachineConflict:
    return MachineConflict(
        f"That is more than {max_bytes // (1024 * 1024)} MB; sync a smaller file.",
        code="MACHINE_SYNC_TOO_LARGE",
    )


def _deadline(seconds: int = 300) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _client():
    from app.modules.workspace.services.sandbox_composition import build_local_client

    return build_local_client()


async def _pod_context(uow, user_id: UUID, pod_id: UUID):
    from app.core.authorization.service import AuthorizationDataService

    return await AuthorizationDataService(uow.session).build_user_context(
        user_id=user_id, pod_id=pod_id
    )


async def sync_from_pod(
    uow_factory: UnitOfWorkFactory,
    *,
    machine_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    pod_path: str,
    machine_path: str,
    max_bytes: int = MAX_FROM_POD_BYTES,
) -> tuple[list[str], int]:
    from app.modules.datastore.contracts.pod_files import (
        read_pod_file,
        readable_pod_file,
    )

    async with uow_factory() as uow:
        ctx = await _pod_context(uow, user_id, pod_id)
        facts = await readable_pod_file(uow, pod_id=pod_id, path=pod_path, ctx=ctx)
        if facts is not None and facts.size_bytes > max_bytes:
            raise _too_large(max_bytes)
        found = await read_pod_file(uow, pod_id=pod_id, path=pod_path, ctx=ctx)
    client = _client()
    target = await _landing(client, machine_id, machine_path, found.name)
    await client.write_file(machine_id, target, found.content, deadline_at=_deadline())
    return [target], len(found.content)


async def _landing(client, machine_id: UUID, machine_path: str, name: str) -> str:
    """Where a file copied to ``machine_path`` lands: inside it when it is a
    directory, at it otherwise -- absent being the ordinary case. Any other
    failure, the machine itself not there among them, is not an answer."""
    try:
        stat = await client.stat_file(
            machine_id, machine_path, deadline_at=_deadline(30)
        )
    except SandboxPathNotFound, WorkspaceRuntimeFileNotFound:
        return machine_path
    if getattr(stat, "kind", None) == FileKind.DIRECTORY:
        return posixpath.join(machine_path, name)
    return machine_path


async def sync_to_pod(
    uow_factory: UnitOfWorkFactory,
    *,
    machine_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    pod_folder: str,
    machine_path: str,
    max_files: int,
    max_bytes: int,
    client=None,
) -> tuple[list[str], int]:
    """``client`` is the machine's runtime client; built here when not given."""
    from app.modules.datastore.contracts.pod_files import write_pod_file

    client = client or _client()
    files = await _collect(
        client, machine_id, machine_path, max_files=max_files, max_bytes=max_bytes
    )
    base = (
        machine_path
        if len(files) != 1 or files[0][0] != machine_path
        else posixpath.dirname(machine_path)
    )
    written: list[str] = []
    total = 0
    for path, _size in files:
        content = await _read_capped(
            client, machine_id, path, room=max_bytes - total, cap=max_bytes
        )
        relative_dir = posixpath.dirname(posixpath.relpath(path, base))
        directory = posixpath.normpath(posixpath.join(pod_folder, relative_dir))
        async with uow_factory() as uow:
            ctx = await _pod_context(uow, user_id, pod_id)
            stored = await write_pod_file(
                uow,
                pod_id=pod_id,
                directory=directory,
                name=posixpath.basename(path),
                content=content,
                ctx=ctx,
            )
            await uow.commit()
        written.append(stored.pod_path)
        total += len(content)
    return written, total


async def _read_capped(
    client, machine_id: UUID, path: str, *, room: int, cap: int
) -> bytes:
    """One file, streamed and stopped at ``room`` bytes: a file that grew past
    the cap after it was measured is refused, not buffered whole."""
    chunks: list[bytes] = []
    size = 0
    async with client.stream_file(machine_id, path, deadline_at=_deadline()) as stream:
        async for chunk in stream:
            size += len(chunk)
            if size > room:
                raise _too_large(cap)
            chunks.append(chunk)
    return b"".join(chunks)


async def _collect(
    client, machine_id: UUID, root: str, *, max_files: int, max_bytes: int
) -> list[tuple[str, int]]:
    """Every file under ``root`` (or ``root`` itself), refusing past the caps
    rather than copying a silently partial tree."""
    stat = await client.stat_file(machine_id, root, deadline_at=_deadline(30))
    if getattr(stat, "kind", None) != FileKind.DIRECTORY:
        size = int(getattr(stat, "size_bytes", 0) or 0)
        if size > max_bytes:
            raise _too_large(max_bytes)
        return [(root, size)]
    found: list[tuple[str, int]] = []
    pending = [root]
    size = 0
    while pending:
        directory = pending.pop()
        for entry in await client.list_files(
            machine_id, directory, deadline_at=_deadline(30)
        ):
            kind = getattr(entry, "kind", None)
            path = str(getattr(entry, "path", ""))
            if kind == FileKind.DIRECTORY:
                pending.append(path)
            elif kind == FileKind.FILE:
                size += int(getattr(entry, "size_bytes", 0) or 0)
                found.append((path, int(getattr(entry, "size_bytes", 0) or 0)))
            if len(found) > max_files or size > max_bytes:
                raise MachineConflict(
                    f"That is more than {max_files} files or {max_bytes // (1024 * 1024)} MB; "
                    "sync a smaller folder.",
                    code="MACHINE_SYNC_TOO_LARGE",
                )
    return sorted(found)
