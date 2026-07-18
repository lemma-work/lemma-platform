from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from agentbox_client import AgentBoxClient
from app.core.api.dependencies import CurrentUser
from app.core.config import settings
from app.core.request_context import correlation_headers
from app.modules.workspace.services.agentbox_manager import agentbox_sandbox_id
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

    client = AgentBoxClient(
        base_url=settings.agentbox_api_url,
        api_key=api_key,
        timeout_seconds=300.0,
        context_headers_provider=correlation_headers,
    )
    try:
        await client.ensure_sandbox(
            agentbox_sandbox_id(user.id),
            env={
                "LEMMA_BASE_URL": (
                    WorkspaceSandboxService.resolve_workspace_api_url_for_runtime(
                        WorkspaceSandboxService._resolve_runtime()
                    )
                )
            },
        )
        access = await client.get_app_access_url(
            agentbox_sandbox_id(user.id),
            "browser",
            ttl_seconds=request.ttl_seconds,
        )
    finally:
        await client.close()

    return WorkspaceAppAccessResponse(
        app=access.app,
        url=access.url,
        expires_at=datetime.fromtimestamp(access.expires_at, tz=timezone.utc),
    )
