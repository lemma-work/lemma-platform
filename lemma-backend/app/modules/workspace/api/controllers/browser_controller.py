from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import CurrentUser
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

router = APIRouter(prefix="/workspace/apps", tags=["Workspace Apps"])


def get_workspace_service() -> WorkspaceSandboxService:
    return WorkspaceSandboxService()


WorkspaceServiceDep = Annotated[WorkspaceSandboxService, Depends(get_workspace_service)]


async def touch_browser(service: WorkspaceSandboxService, user_id) -> None:
    """Reset both clocks that would otherwise close the browser under a watcher.

    `agent-browser` shuts Chrome down after two minutes with no command, and the
    sandbox releases after fifteen minutes idle. Watching is not a command, so a
    live view with nobody typing goes dark in about two minutes — which is a
    thing people reported as the view "turning off by itself".

    One trivial command answers both: it resets the daemon's idle timer, and
    running it through the workspace session refreshes the sandbox's own
    activity clock.
    """
    session = await service.get_session(
        user_id, pod_id=None, initial_cwd="/workspace", close_on_exit=False
    )
    async with session:
        await session.exec_command(cmd="agent-browser get url", timeout=20)


class WorkspaceAppAccessRequest(BaseModel):
    ttl_seconds: int = Field(default=1800, ge=60, le=3600)


class WorkspaceAppAccessResponse(BaseModel):
    app: str
    url: str
    expires_at: datetime


@router.post(
    "/browser/access",
    response_model=WorkspaceAppAccessResponse,
    status_code=status.HTTP_200_OK,
    operation_id="workspace.browser.access",
    summary="Create workspace browser access URL",
)
async def create_workspace_browser_access(
    request: WorkspaceAppAccessRequest,
    user: CurrentUser,
) -> WorkspaceAppAccessResponse:
    # The URL handed back is a signed grant, so without the signing key there
    # is nothing to hand back.
    if not workspace_settings.runtime_credential_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace port access signing key is not configured",
        )

    access = await WorkspaceSandboxService().create_browser_access(
        user.id, ttl_seconds=request.ttl_seconds
    )

    return WorkspaceAppAccessResponse(
        app="browser",
        url=access.url,
        expires_at=access.expires_at,
    )


@router.post(
    "/browser/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="workspace.browser.heartbeat",
    summary="Keep the workspace browser awake while somebody is watching",
)
async def heartbeat_workspace_browser(
    user: CurrentUser,
    service: WorkspaceServiceDep,
) -> None:
    try:
        await touch_browser(service, user.id)
    finally:
        await service.close()
