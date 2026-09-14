"""Read access to a person's own workspace sandbox files.

Authorisation is free here and worth saying why: a workspace is keyed
``(WORKSPACE, user_id)``, so ``CurrentUser`` is the whole check and there is no
``sandbox_id`` parameter for a caller to point somewhere else. What still needs
guarding is the *path*.

**These routes see ``/workspace`` and nothing else.** The sandbox runtime also
allows ``/tmp``, and deliberately so — it is where ``github_credential_bridge``
stages a credential precisely because that directory dies with the sandbox. An
HTTP read route over ``/tmp`` would publish those files to any request carrying
the caller's session, which is a far wider surface than a shell inside the
sandbox — these endpoints are reachable from page script through the published
JS client, which a shell is not.

Two clamps, because one was not enough. :func:`_workspace_path` rejects a path
that *spells* its way out. :func:`_inside_workspace` rejects one that
**resolves** its way out: the textual check is `posixpath.normpath`, which knows
nothing about symlinks, and the runtime follows them. A symlink planted under
``/workspace`` by the agent therefore served ``/tmp`` — including the staged
credential the paragraph above exists to keep out.

Reads are **ambient by default**: listing does not wake a paused sandbox, because
a file pane that boots a sandbox on every render is a cost bug against a 900s
idle release. Reading a file's *content* is the interactive act that wakes it.
"""

from __future__ import annotations

import posixpath
from datetime import datetime
from typing import AsyncIterator, Literal

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser
from app.core.log.log import get_logger
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from app.modules.workspace.session_support import sandbox_failure_types
from sandbox_runtime.protocol import FileKind

logger = get_logger(__name__)

router = APIRouter(prefix="/workspace", tags=["Workspace"])


def get_workspace_service() -> WorkspaceSandboxService:
    """The service these routes read through.

    A dependency rather than a direct construction so a test supplies its own
    without reaching inside the module under test.
    """
    return WorkspaceSandboxService()


WorkspaceServiceDep = Annotated[WorkspaceSandboxService, Depends(get_workspace_service)]

_ROOT = "/workspace"

# One page of a directory. A workspace holding a `node_modules` is the ordinary
# case, not the pathological one, and a pane that asks for all of it stalls on
# the transfer rather than on the listing.
_MAX_ENTRIES = 1000

# The ceiling on a single content read. The runtime's own transfer bound is
# 256 MB; this is what a *viewer* should ever pull in one request, and a caller
# that wants more asks for the next range.
_MAX_CONTENT_BYTES = 8 * 1024 * 1024

# What a workspace read can fail with. Narrow on purpose: these become a status
# the caller can act on, and anything outside the set is a defect that should
# surface as a 500 rather than be dressed up as "the workspace is busy".
_READ_FAILURES: tuple[type[BaseException], ...] = (
    *sandbox_failure_types(),
    WorkspaceRuntimeError,
)


class WorkspaceFileEntry(BaseModel):
    path: str = Field(description="Absolute path inside the workspace.")
    name: str = Field(description="Final path segment.")
    kind: Literal["file", "directory", "symlink"] = Field(
        description="What this entry is."
    )
    size_bytes: int = Field(description="Size in bytes; 0 for a directory.")
    modified_at: datetime = Field(description="Last modification time.")


class WorkspaceFileListResponse(BaseModel):
    path: str = Field(description="The directory that was listed.")
    sleeping: bool = Field(
        default=False,
        description=(
            "True when the workspace is paused and was not woken to answer. "
            "Entries are empty; ask again with `wake=true` to start it."
        ),
    )
    truncated: bool = Field(
        default=False,
        description="True when the directory holds more entries than were returned.",
    )
    next_after: str | None = Field(
        default=None,
        description=(
            "Pass as `after` to get the next page. Null when this is the last "
            "one. A directory with more entries than fit was previously a dead "
            "end: the rest could be counted and never reached."
        ),
    )
    entries: list[WorkspaceFileEntry] = Field(default_factory=list)


def _workspace_path(path: str | None) -> str:
    """Resolve a caller path to an absolute one under ``/workspace``.

    Rejects rather than clamps, so a caller asking for ``/tmp`` or ``/etc`` is
    told no instead of quietly being handed the workspace root and believing the
    answer describes what it asked for.
    """
    candidate = (path or "").strip() or _ROOT
    if "\x00" in candidate:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Path must not contain a null byte",
        )
    absolute = (
        candidate if candidate.startswith("/") else posixpath.join(_ROOT, candidate)
    )
    normalized = posixpath.normpath(absolute)
    if normalized != _ROOT and not normalized.startswith(f"{_ROOT}/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Path must stay inside /workspace",
        )
    return normalized


def _inside_workspace(stat) -> None:
    """Refuse anything that resolves outside ``/workspace``.

    The runtime stats without following the final component and returns the
    path with its *parent* already resolved. Those two facts together are the
    whole check:

    * ``kind is SYMLINK`` -- the thing asked for is itself a link. Refused
      rather than followed, because where it points is not this endpoint's to
      decide.
    * the reported path is outside ``/workspace`` -- some ancestor was a link,
      and resolving the parent is what made that visible. ``/workspace/x/token``
      where ``x -> /tmp/lemma-relay`` comes back as ``/tmp/lemma-relay/token``
      and is refused here.

    Checked against what the sandbox reports, not against what was asked for.
    A check on the request string is the one that was already there, and it is
    the one a symlink walks straight past.

    Honest limit: on E2B the file API goes through the provider SDK rather than
    this runtime, and that SDK reports neither symlinks nor resolved paths, so
    this cannot see them. The Docker and desktop fabrics are covered.
    """
    reported = str(getattr(stat, "path", "") or "")
    if getattr(stat, "kind", None) == FileKind.SYMLINK:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Path must stay inside /workspace",
        )
    if reported and reported != _ROOT and not reported.startswith(f"{_ROOT}/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Path must stay inside /workspace",
        )


#: What the runtime can report an entry as. Narrowed here rather than trusted,
#: because the response says it is one of three things and a fourth arriving
#: from a future runtime should be reported as a plain file rather than
#: rejected by the response model after the read has already happened.
_KINDS: tuple[str, ...] = ("file", "directory", "symlink")


def _kind_of(stat: object) -> Literal["file", "directory", "symlink"]:
    kind = str(getattr(stat, "kind", "file"))
    return kind if kind in _KINDS else "file"  # type: ignore[return-value]


def _entry(stat: object) -> WorkspaceFileEntry:
    path = str(getattr(stat, "path", ""))
    return WorkspaceFileEntry(
        path=path,
        name=posixpath.basename(path) or path,
        kind=_kind_of(stat),
        size_bytes=int(getattr(stat, "size_bytes", 0) or 0),
        modified_at=getattr(stat, "modified_at"),
    )


async def _is_awake(service: WorkspaceSandboxService, user_id) -> bool:
    """Whether the workspace is already running, without starting one."""
    info = await service.sandbox.get_sandbox(user_id)
    return info is not None and info.status == "RUNNING"


@router.get(
    "/files",
    response_model=WorkspaceFileListResponse,
    operation_id="workspace.files.list",
    summary="List workspace files",
)
async def list_workspace_files(
    user: CurrentUser,
    service: WorkspaceServiceDep,
    path: str | None = Query(default=None, max_length=4096),
    wake: bool = Query(
        default=False,
        description="Start the workspace if it is paused. Off by default.",
    ),
    after: str | None = Query(
        default=None,
        max_length=4096,
        description=(
            "Continue after this entry's path, from a previous response's `next_after`."
        ),
    ),
) -> WorkspaceFileListResponse:
    target = _workspace_path(path)
    try:
        if not wake and not await _is_awake(service, user.id):
            return WorkspaceFileListResponse(path=target, sleeping=True)
        session = await service.get_session(
            user.id, pod_id=None, initial_cwd=_ROOT, close_on_exit=False
        )
        async with session:
            _inside_workspace(await session.stat_file(target))
            stats = await session.list_files(target)
    except HTTPException:
        raise
    except _READ_FAILURES as exc:
        # A conversation's directory does not exist until the agent writes
        # something into it, so "not there" is the ordinary first state of every
        # new conversation rather than a failure. Answering 404 made an empty
        # workspace look broken. A missing *file* is still a 404 — that is
        # `:stat` and `:content`, below.
        if "NotFound" in type(exc).__name__:
            return WorkspaceFileListResponse(path=target)
        raise _as_http_error(exc, target)
    finally:
        await service.close()

    # Ordered by name so a page boundary means the same thing on the next
    # request. The runtime returns a directory in whatever order it read it,
    # and paging through an unstable order shows some entries twice and others
    # never.
    ordered = sorted(stats, key=lambda stat: str(getattr(stat, "path", "")))
    if after:
        ordered = [stat for stat in ordered if str(getattr(stat, "path", "")) > after]
    page = ordered[:_MAX_ENTRIES]
    more = len(ordered) > _MAX_ENTRIES
    return WorkspaceFileListResponse(
        path=target,
        truncated=more,
        next_after=str(getattr(page[-1], "path", "")) if more and page else None,
        entries=[_entry(stat) for stat in page],
    )


@router.get(
    "/files:stat",
    response_model=WorkspaceFileEntry,
    operation_id="workspace.files.stat",
    summary="Stat one workspace file",
)
async def stat_workspace_file(
    user: CurrentUser,
    service: WorkspaceServiceDep,
    path: str = Query(min_length=1, max_length=4096),
) -> WorkspaceFileEntry:
    target = _workspace_path(path)
    try:
        session = await service.get_session(
            user.id, pod_id=None, initial_cwd=_ROOT, close_on_exit=False
        )
        async with session:
            stat = await session.stat_file(target)
            _inside_workspace(stat)
    except HTTPException:
        raise
    except _READ_FAILURES as exc:
        raise _as_http_error(exc, target)
    finally:
        await service.close()
    return _entry(stat)


@router.get(
    "/files:content",
    operation_id="workspace.files.content",
    summary="Read workspace file content",
    response_class=StreamingResponse,
)
async def read_workspace_file(
    user: CurrentUser,
    service: WorkspaceServiceDep,
    path: str = Query(min_length=1, max_length=4096),
    offset: int = Query(default=0, ge=0),
    length: int | None = Query(default=None, ge=1, le=_MAX_CONTENT_BYTES),
) -> StreamingResponse:
    target = _workspace_path(path)
    session = None
    try:
        session = await service.get_session(
            user.id, pod_id=None, initial_cwd=_ROOT, close_on_exit=False
        )
        await session.__aenter__()
        # Statted before it is read. The extra round trip is what makes the
        # boundary hold: reading straight from the path asked for is what
        # followed a symlink out of /workspace.
        _inside_workspace(await session.stat_file(target))
        content = await session.read_file(
            target, offset=offset, length=length or _MAX_CONTENT_BYTES
        )
    except HTTPException:
        await _release(service, session)
        raise
    except _READ_FAILURES as exc:
        await _release(service, session)
        raise _as_http_error(exc, target)

    async def body() -> AsyncIterator[bytes]:
        try:
            yield content
        finally:
            await _release(service, session)

    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(content)),
            # A workspace file is the person's own content and is never markup
            # this app should render: served inline it would run as script on
            # the API origin.
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _release(service: WorkspaceSandboxService, session) -> None:
    if session is not None:
        try:
            await session.__aexit__(None, None, None)
        except _READ_FAILURES:
            logger.warning(
                "workspace.files.session_close.degraded",
                exc_info=True,
            )
    await service.close()


def _as_http_error(exc: BaseException, path: str) -> HTTPException:
    """Map a workspace read failure onto a status the caller can act on.

    Matched on the error's name rather than its type because the two failure
    families are parallel rather than shared — the runtime raises
    ``SandboxPathNotFound`` and the HTTP client raises
    ``WorkspaceRuntimeFileNotFound`` for the same event — and a viewer asking
    for a file that is not there should get a 404 from either.
    """
    name = type(exc).__name__
    if "NotFound" in name:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No such path: {path}"
        )
    if "TooLarge" in name or "Rejected" in name:
        return HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File is larger than this endpoint will serve; read a range.",
        )
    logger.warning("workspace.files.read_failed.degraded", exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Workspace is not reachable right now.",
    )
