"""`/workspace/*`: the routes before machines, still served, and saying so.

Pods, apps and scripts written against these keep working: each route here is
its `/machines/me/...` replacement under the old path and the old shape, and
every response carries ``X-Lemma-Deprecation`` naming what to call instead
(`app.core.api.deprecation`). They go in a later release, once nothing calls
them.

Who may reach them is unchanged from before machines: the person's own
machine, through the same resolver as every machine route. An app's page is
refused here as it always was -- these are not in `APP_ROUTES` -- and reaches
its viewer's machine only through `/machines/me`, with their consent.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.api.deprecation import deprecated_route
from app.modules.workspace.api.controllers import (
    machine_browser_controller,
    machine_files_controller,
    machines_controller,
)
from app.modules.workspace.api.controllers.machine_dependencies import (
    UowFactoryDep,
    resolve_for_request,
)
from app.modules.workspace.api.controllers.machine_files_controller import (
    FilesDep,
    MachineFileEntry,
    MachineFileListResponse,
)
from app.modules.workspace.api.machine_schemas import MachineStartupResponse
from app.modules.workspace.domain.machine_access import MachineAccess
from app.modules.workspace.services.machine_resolver import ResolvedMachine

#: When the old routes stop answering. Long enough for a published app or a
#: pod bundle written against them to move.
SUNSET = date(2027, 1, 31)

router = APIRouter(prefix="/workspace", tags=["Workspace"])
apps_router = APIRouter(prefix="/workspace/apps", tags=["Workspace Apps"])


async def own_machine(request: Request, uow_factory: UowFactoryDep) -> ResolvedMachine:
    """The caller's own machine, as `/machines/me` resolves it."""
    resolved, caller = await resolve_for_request(
        request, uow_factory, "me", need=MachineAccess.USE
    )
    request.state.machine_caller = caller
    return resolved


async def own_machine_status(
    request: Request, uow_factory: UowFactoryDep
) -> ResolvedMachine:
    resolved, caller = await resolve_for_request(
        request, uow_factory, "me", need=MachineAccess.STATUS
    )
    request.state.machine_caller = caller
    return resolved


OwnMachine = Annotated[ResolvedMachine, Depends(own_machine)]


@router.get(
    "/files",
    response_model=MachineFileListResponse,
    summary="List workspace files",
    **deprecated_route(
        operation_id="workspace.files.list",
        replacement="GET /machines/me/files",
        sunset=SUNSET,
    ),
)
async def list_workspace_files(
    resolved: OwnMachine,
    files: FilesDep,
    path: str | None = Query(default=None, max_length=4096),
    wake: bool = Query(default=False),
    after: str | None = Query(default=None, max_length=4096),
) -> MachineFileListResponse:
    return await machine_files_controller.list_files(
        resolved, files, path=path, wake=wake, after=after
    )


@router.get(
    "/files:stat",
    response_model=MachineFileEntry,
    summary="Stat one workspace file",
    **deprecated_route(
        operation_id="workspace.files.stat",
        replacement="GET /machines/me/files:stat",
        sunset=SUNSET,
    ),
)
async def stat_workspace_file(
    resolved: OwnMachine,
    files: FilesDep,
    path: str = Query(min_length=1, max_length=4096),
) -> MachineFileEntry:
    return await machine_files_controller.stat_file(resolved, files, path=path)


@router.get(
    "/files:content",
    summary="Read workspace file content",
    response_class=Response,
    **deprecated_route(
        operation_id="workspace.files.content",
        replacement="GET /machines/me/files:content",
        sunset=SUNSET,
    ),
)
async def read_workspace_file(
    resolved: OwnMachine,
    files: FilesDep,
    path: str = Query(min_length=1, max_length=4096),
    offset: int = Query(default=0, ge=0),
    length: int | None = Query(default=None, ge=1, le=8 * 1024 * 1024),
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    return await machine_files_controller.read_file(
        resolved,
        files,
        path=path,
        offset=offset,
        length=length,
        range_header=range_header,
        if_none_match=if_none_match,
    )


@router.get(
    "/status",
    response_model=MachineStartupResponse,
    response_model_exclude_none=True,
    summary="Whether your computer is ready",
    **deprecated_route(
        operation_id="workspace.status",
        replacement="GET /machines/me/startup",
        sunset=SUNSET,
    ),
)
async def workspace_status(
    resolved: Annotated[ResolvedMachine, Depends(own_machine_status)],
) -> MachineStartupResponse:
    return await machines_controller.machine_startup(resolved)


class WorkspaceAppAccessRequest(BaseModel):
    ttl_seconds: int = Field(default=1800, ge=60, le=3600)


class WorkspaceAppAccessResponse(BaseModel):
    app: str
    url: str
    expires_at: datetime


@apps_router.post(
    "/browser/access",
    response_model=WorkspaceAppAccessResponse,
    summary="Create workspace browser access URL",
    **deprecated_route(
        operation_id="workspace.browser.access",
        replacement="POST /machines/me/browser:access",
        sunset=SUNSET,
    ),
)
async def create_workspace_browser_access(
    body: WorkspaceAppAccessRequest,
    resolved: OwnMachine,
) -> WorkspaceAppAccessResponse:
    access = await machine_browser_controller.browser_access(
        machine_browser_controller.BrowserAccessRequest(ttl_seconds=body.ttl_seconds),
        resolved,
    )
    return WorkspaceAppAccessResponse(
        app="browser", url=access.url, expires_at=access.expires_at
    )


__all__ = ["SUNSET", "apps_router", "router"]
