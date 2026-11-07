"""`/machines/{ref}/files`: read, write, move, delete and sync a machine's files.

**These routes see the machine's home and nothing else.** The runtime also
allows ``/tmp``, deliberately -- it is where a credential is staged so that it
dies with the machine -- and an HTTP route over ``/tmp`` would publish those
files to anything holding the caller's session.

Two clamps, because one was not enough: ``_machine_path`` rejects a path that
*spells* its way out, and ``_inside_home`` one that *resolves* its way out
through a symlink the runtime would follow.

The browser's profile is the one place under the home these routes never
serve, and never change: reading it would publish every live session, and
deleting or overwriting it would sign a person out of everything. A confirmed
`home` reset is the only thing that replaces it.

Listing does not wake a paused machine: a file pane that boots a machine on
every render is a cost bug. Reading content, and every write, does.
"""

from __future__ import annotations

import posixpath
from datetime import datetime, timedelta, timezone
from typing import Annotated, AsyncIterator, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.core.log.log import get_logger
from app.modules.workspace.api.controllers.machine_dependencies import (
    SeeMachine,
    UowFactoryDep,
    UseMachine,
    acting_user,
)
from app.modules.workspace.api.controllers.machines_controller import machines_enabled
from app.modules.workspace.api.controllers.workspace_file_ranges import (
    Unsatisfiable,
    matches_etag,
    requested_range,
)
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
from app.modules.workspace.session_support import sandbox_failure_types
from sandbox_runtime.paths import (
    HOME_ROOT,
    WORKSPACE_ROOT,
    is_browser_private,
    is_inside_home,
    is_protected,
)
from sandbox_runtime.protocol import FileKind

logger = get_logger(__name__)

router = APIRouter(tags=["Machine Files"], dependencies=[Depends(machines_enabled)])

_MAX_ENTRIES = 1000
_MAX_CONTENT_BYTES = 8 * 1024 * 1024
_MAX_WRITE_BYTES = 64 * 1024 * 1024
_MAX_SYNC_FILES = 500
_MAX_SYNC_BYTES = 256 * 1024 * 1024

_FAILURES: tuple[type[BaseException], ...] = (
    *sandbox_failure_types(),
    WorkspaceRuntimeError,
)


class MachineFileEntry(BaseModel):
    path: str
    name: str
    kind: Literal["file", "directory", "symlink"]
    size_bytes: int
    modified_at: datetime
    mode: int | None = None
    sha256: str | None = None


class MachineFileListResponse(BaseModel):
    path: str
    home_root: str = HOME_ROOT
    workspace_root: str = WORKSPACE_ROOT
    sleeping: bool = Field(
        default=False,
        description="The machine is paused and was not woken; ask with `wake=true`.",
    )
    truncated: bool = False
    next_after: str | None = None
    exists: bool = True
    entries: list[MachineFileEntry] = Field(default_factory=list)


class MoveRequest(BaseModel):
    source: str = Field(min_length=1, max_length=4096)
    destination: str = Field(min_length=1, max_length=4096)


class SyncRequest(BaseModel):
    direction: Literal["to_pod", "from_pod"]
    machine_path: str = Field(min_length=1, max_length=4096)
    pod_id: UUID
    pod_path: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "`to_pod`: the pod folder to put the files in. `from_pod`: the pod "
            "file to copy onto the machine."
        ),
    )


class SyncResponse(BaseModel):
    files: list[str] = Field(description="What was written, where it landed.")
    bytes: int


def _machine_path(path: str | None) -> str:
    candidate = (path or "").strip() or WORKSPACE_ROOT
    if "\x00" in candidate:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Path must not contain a null byte"
        )
    absolute = (
        candidate
        if candidate.startswith("/")
        else posixpath.join(WORKSPACE_ROOT, candidate)
    )
    normalized = posixpath.normpath(absolute)
    if not is_inside_home(normalized):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Path must stay inside {HOME_ROOT}"
        )
    _refuse_browser_profile(normalized)
    return normalized


def _refuse_browser_profile(path: str) -> None:
    if is_browser_private(path):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "The browser's profile holds live sessions and is not served over "
            "the file API. Manage sign-ins under the machine's browser logins.",
        )


def _refuse_protected(path: str) -> None:
    """Writes are refused on anything holding a protected path, not just in it:
    deleting ``~/.lemma`` deletes the profile inside it."""
    if is_protected(path):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "That would change the browser's profile, which holds your logins. "
            "Forget a login explicitly instead.",
        )


def _inside_home(stat) -> None:
    reported = str(getattr(stat, "path", "") or "")
    if getattr(stat, "kind", None) == FileKind.SYMLINK or (
        reported and not is_inside_home(reported)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Path must stay inside {HOME_ROOT}"
        )
    if reported:
        _refuse_browser_profile(reported)


_KINDS = ("file", "directory", "symlink")


def _entry(stat) -> MachineFileEntry:
    path = str(getattr(stat, "path", ""))
    kind = str(getattr(stat, "kind", "file"))
    mode = getattr(stat, "mode", None)
    digest = getattr(stat, "sha256", None)
    return MachineFileEntry(
        path=path,
        name=posixpath.basename(path) or path,
        kind=kind if kind in _KINDS else "file",  # type: ignore[arg-type]
        size_bytes=int(getattr(stat, "size_bytes", 0) or 0),
        modified_at=getattr(stat, "modified_at"),
        mode=int(mode) if isinstance(mode, int) else None,
        sha256=str(digest) if digest else None,
    )


class MachineFiles:
    """What these routes ask of a machine. A dependency, so a test supplies its
    own rather than reaching into this module."""

    def __init__(self) -> None:
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
            get_sandbox_service,
        )

        self._client = build_local_client()
        self._service = get_sandbox_service()

    async def awake(self, machine_id: UUID) -> bool:
        """Running now, asked without starting anything."""
        info = await self._service.describe(machine_id)
        return info is not None and info.status == "RUNNING"

    async def stat_file(self, machine_id: UUID, path: str, *, deadline_at: datetime):
        return await self._client.stat_file(machine_id, path, deadline_at=deadline_at)

    async def list_files(self, machine_id: UUID, path: str, *, deadline_at: datetime):
        return await self._client.list_files(machine_id, path, deadline_at=deadline_at)

    async def read_file(
        self,
        machine_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        return await self._client.read_file(
            machine_id, path, deadline_at=deadline_at, offset=offset, length=length
        )

    async def write_file_stream(
        self,
        machine_id: UUID,
        path: str,
        data: AsyncIterator[bytes],
        *,
        deadline_at: datetime,
    ):
        return await self._client.write_file_stream(
            machine_id, path, data, deadline_at=deadline_at
        )

    async def delete_file(
        self, machine_id: UUID, path: str, *, deadline_at: datetime, recursive: bool
    ) -> None:
        await self._client.delete_file(
            machine_id, path, deadline_at=deadline_at, recursive=recursive
        )

    async def move_file(
        self, machine_id: UUID, source: str, destination: str, *, deadline_at: datetime
    ) -> None:
        await self._client.move_file(
            machine_id, source, destination, deadline_at=deadline_at
        )


async def machine_files() -> MachineFiles:
    # Async on purpose: a plain function runs in a worker thread, where the
    # sandbox service it builds finds no running loop.
    return MachineFiles()


FilesDep = Annotated[MachineFiles, Depends(machine_files)]


def _deadline(seconds: int = 120) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _as_http_error(exc: BaseException, path: str) -> HTTPException:
    name = type(exc).__name__
    if "Unauthorized" in name:
        logger.warning(
            "workspace.machine_files.runtime_unauthorized.failed", exc_info=exc
        )
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Lemma could not authenticate to the machine.",
        )
    if "NotFound" in name:
        return HTTPException(status.HTTP_404_NOT_FOUND, f"No such path: {path}")
    if "TooLarge" in name or "Rejected" in name:
        return HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "File is larger than this endpoint will serve; read a range.",
        )
    if "Unavailable" in name:
        logger.warning("workspace.machine_files.not_ready.degraded", exc_info=exc)
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The machine is still starting. Try again in a moment.",
        )
    logger.warning("workspace.machine_files.failed.degraded", exc_info=exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "The machine is not reachable right now."
    )


@router.get(
    "/machines/{ref}/files",
    response_model=MachineFileListResponse,
    operation_id="machine.files.list",
    summary="List a directory on a machine",
)
async def list_files(
    resolved: UseMachine,
    files: FilesDep,
    path: str | None = Query(default=None, max_length=4096),
    wake: bool = Query(default=False, description="Start the machine if it is paused."),
    after: str | None = Query(default=None, max_length=4096),
) -> MachineFileListResponse:
    target = _machine_path(path)
    machine_id = resolved.machine.id
    try:
        if not wake and not await files.awake(machine_id):
            return MachineFileListResponse(path=target, sleeping=True)
        client = files
        _inside_home(
            await client.stat_file(machine_id, target, deadline_at=_deadline())
        )
        stats = await client.list_files(machine_id, target, deadline_at=_deadline())
    except HTTPException:
        raise
    except _FAILURES as exc:
        if "NotFound" in type(exc).__name__:
            return MachineFileListResponse(path=target, exists=False)
        raise _as_http_error(exc, target)
    ordered = sorted(stats, key=lambda stat: str(getattr(stat, "path", "")))
    if after:
        ordered = [stat for stat in ordered if str(getattr(stat, "path", "")) > after]
    page = ordered[:_MAX_ENTRIES]
    more = len(ordered) > _MAX_ENTRIES
    return MachineFileListResponse(
        path=target,
        truncated=more,
        next_after=str(getattr(page[-1], "path", "")) if more and page else None,
        entries=[_entry(stat) for stat in page],
    )


@router.get(
    "/machines/{ref}/files:stat",
    response_model=MachineFileEntry,
    operation_id="machine.files.stat",
    summary="Stat one path on a machine",
)
async def stat_file(
    resolved: UseMachine,
    files: FilesDep,
    path: str = Query(min_length=1, max_length=4096),
) -> MachineFileEntry:
    target = _machine_path(path)
    try:
        stat = await files.stat_file(
            resolved.machine.id, target, deadline_at=_deadline()
        )
    except _FAILURES as exc:
        raise _as_http_error(exc, target)
    _inside_home(stat)
    return _entry(stat)


@router.get(
    "/machines/{ref}/files:content",
    operation_id="machine.files.content",
    summary="Read a file on a machine",
    response_class=StreamingResponse,
)
async def read_file(
    resolved: UseMachine,
    files: FilesDep,
    path: str = Query(min_length=1, max_length=4096),
    offset: int = Query(default=0, ge=0),
    length: int | None = Query(default=None, ge=1, le=_MAX_CONTENT_BYTES),
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    target = _machine_path(path)
    machine_id = resolved.machine.id
    client = files
    try:
        stat = await client.stat_file(machine_id, target, deadline_at=_deadline())
        _inside_home(stat)
        total = int(getattr(stat, "size_bytes", 0) or 0)
        etag = f'"{digest}"' if (digest := getattr(stat, "sha256", None)) else None
        if etag and if_none_match and matches_etag(if_none_match, etag):
            return Response(
                status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag}
            )
        wanted = requested_range(range_header, total)
        if isinstance(wanted, Unsatisfiable):
            raise HTTPException(
                status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                f"That range is outside a {total}-byte file",
                headers={"Content-Range": f"bytes */{total}"},
            )
        if wanted is not None:
            offset, length = wanted
        content = await client.read_file(
            machine_id,
            target,
            offset=offset,
            length=length or _MAX_CONTENT_BYTES,
            deadline_at=_deadline(),
        )
    except HTTPException:
        raise
    except _FAILURES as exc:
        raise _as_http_error(exc, target)

    async def body() -> AsyncIterator[bytes]:
        yield content

    headers = {
        "Content-Length": str(len(content)),
        # The person's own content, never markup this origin should render.
        "Content-Disposition": "attachment",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
    }
    if etag:
        headers["ETag"] = etag
    if wanted is not None:
        headers["Content-Range"] = f"bytes {offset}-{offset + len(content) - 1}/{total}"
    return StreamingResponse(
        body(),
        status_code=status.HTTP_206_PARTIAL_CONTENT
        if wanted is not None
        else status.HTTP_200_OK,
        media_type="application/octet-stream",
        headers=headers,
    )


@router.put(
    "/machines/{ref}/files/content",
    response_model=MachineFileEntry,
    operation_id="machine.files.write",
    summary="Write a file on a machine",
)
async def write_file(
    request: Request,
    resolved: UseMachine,
    files: FilesDep,
    path: str = Query(min_length=1, max_length=4096),
) -> MachineFileEntry:
    target = _machine_path(path)
    _refuse_protected(target)
    declared = request.headers.get("content-length")
    if declared and int(declared) > _MAX_WRITE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, "Write at most 64 MB per request."
        )

    async def chunks() -> AsyncIterator[bytes]:
        written = 0
        async for chunk in request.stream():
            written += len(chunk)
            if written > _MAX_WRITE_BYTES:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "Write at most 64 MB per request.",
                )
            yield chunk

    try:
        stat = await files.write_file_stream(
            resolved.machine.id, target, chunks(), deadline_at=_deadline(600)
        )
    except _FAILURES as exc:
        raise _as_http_error(exc, target)
    return _entry(stat)


@router.delete(
    "/machines/{ref}/files",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="machine.files.delete",
    summary="Delete a file or directory on a machine",
)
async def delete_file(
    resolved: UseMachine,
    files: FilesDep,
    path: str = Query(min_length=1, max_length=4096),
    recursive: bool = Query(default=False),
) -> None:
    target = _machine_path(path)
    _refuse_protected(target)
    if target in (HOME_ROOT, WORKSPACE_ROOT):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Use a home reset to start over."
        )
    try:
        await files.delete_file(
            resolved.machine.id, target, recursive=recursive, deadline_at=_deadline()
        )
    except _FAILURES as exc:
        raise _as_http_error(exc, target)


@router.post(
    "/machines/{ref}/files:move",
    response_model=MachineFileEntry,
    operation_id="machine.files.move",
    summary="Move or rename a path on a machine",
)
async def move_file(
    resolved: UseMachine, files: FilesDep, body: MoveRequest
) -> MachineFileEntry:
    source = _machine_path(body.source)
    destination = _machine_path(body.destination)
    _refuse_protected(source)
    _refuse_protected(destination)
    client = files
    try:
        await client.move_file(
            resolved.machine.id, source, destination, deadline_at=_deadline()
        )
        stat = await client.stat_file(
            resolved.machine.id, destination, deadline_at=_deadline()
        )
    except _FAILURES as exc:
        raise _as_http_error(exc, source)
    return _entry(stat)


@router.post(
    "/machines/{ref}/files:sync",
    response_model=SyncResponse,
    operation_id="machine.files.sync",
    summary="Copy files between a machine and pod files",
)
async def sync_files(
    request: Request,
    resolved: UseMachine,
    body: SyncRequest,
    uow_factory: UowFactoryDep,
) -> SyncResponse:
    """Server-side, so a render's output or a pod's input does not make a round
    trip through whoever asked. ``to_pod`` copies a file, or a directory's files
    (at most 500, 256 MB), into a pod folder; ``from_pod`` copies one pod file
    onto the machine."""
    from app.modules.workspace.services.machine_file_sync import (
        sync_from_pod,
        sync_to_pod,
    )

    from app.core.app_pin import app_pod_for

    machine_path = _machine_path(body.machine_path)
    user_id = acting_user(request, resolved)
    # An app's page reaches only its own pod's files, whatever pod it names.
    pod_id = app_pod_for(request, body.pod_id) or body.pod_id
    if body.direction == "from_pod":
        _refuse_protected(machine_path)
        written, size = await sync_from_pod(
            uow_factory,
            machine_id=resolved.machine.id,
            user_id=user_id,
            pod_id=pod_id,
            pod_path=body.pod_path,
            machine_path=machine_path,
        )
    else:
        written, size = await sync_to_pod(
            uow_factory,
            machine_id=resolved.machine.id,
            user_id=user_id,
            pod_id=pod_id,
            pod_folder=body.pod_path,
            machine_path=machine_path,
            max_files=_MAX_SYNC_FILES,
            max_bytes=_MAX_SYNC_BYTES,
        )
    return SyncResponse(files=written, bytes=size)


__all__ = ["SeeMachine", "router"]
