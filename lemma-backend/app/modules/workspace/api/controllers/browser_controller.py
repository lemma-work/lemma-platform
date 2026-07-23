from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser
from app.core.config import settings
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

router = APIRouter(prefix="/workspace/apps", tags=["Workspace Apps"])


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
    api_key = settings.agentbox_api_key
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace sandbox manager API key is not configured",
        )

    access = await WorkspaceSandboxService().create_browser_access(
        user.id, ttl_seconds=request.ttl_seconds
    )

    return WorkspaceAppAccessResponse(
        app="browser",
        url=access.url,
        expires_at=access.expires_at,
    )
