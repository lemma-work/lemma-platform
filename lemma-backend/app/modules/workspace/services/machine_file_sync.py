"""Copying files between a machine and a pod's files, without a round trip.

Authorized twice, on purpose: the caller must be able to use the machine (the
route resolved it) *and* the pod files are read or written as the caller, with
their own context in that pod -- a machine is not a way into a pod's files
somebody could not otherwise reach.
"""

from __future__ import annotations

import posixpath
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
from sandbox_runtime.errors import SandboxError
from sandbox_runtime.protocol import FileKind


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
) -> tuple[list[str], int]:
    from app.modules.datastore.contracts.pod_files import read_pod_file

    async with uow_factory() as uow:
        ctx = await _pod_context(uow, user_id, pod_id)
        found = await read_pod_file(uow, pod_id=pod_id, path=pod_path, ctx=ctx)
    target = machine_path
    client = _client()
    try:
        stat = await client.stat_file(
            machine_id, machine_path, deadline_at=_deadline(30)
        )
        if getattr(stat, "kind", None) == FileKind.DIRECTORY:
            target = posixpath.join(machine_path, found.name)
    except (SandboxError, WorkspaceRuntimeError) as exc:
        # Absent is the ordinary case: the copy lands at the path given.
        if "NotFound" not in type(exc).__name__:
            raise
    await client.write_file(machine_id, target, found.content, deadline_at=_deadline())
    return [target], len(found.content)


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
) -> tuple[list[str], int]:
    from app.modules.datastore.contracts.pod_files import write_pod_file

    files = await _collect(
        machine_id, machine_path, max_files=max_files, max_bytes=max_bytes
    )
    base = (
        machine_path
        if len(files) != 1 or files[0][0] != machine_path
        else posixpath.dirname(machine_path)
    )
    client = _client()
    written: list[str] = []
    total = 0
    for path, _size in files:
        content = await client.read_file(machine_id, path, deadline_at=_deadline())
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


async def _collect(
    machine_id: UUID, root: str, *, max_files: int, max_bytes: int
) -> list[tuple[str, int]]:
    """Every file under ``root`` (or ``root`` itself), refusing past the caps
    rather than copying a silently partial tree."""
    client = _client()
    stat = await client.stat_file(machine_id, root, deadline_at=_deadline(30))
    if getattr(stat, "kind", None) != FileKind.DIRECTORY:
        return [(root, int(getattr(stat, "size_bytes", 0) or 0))]
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
