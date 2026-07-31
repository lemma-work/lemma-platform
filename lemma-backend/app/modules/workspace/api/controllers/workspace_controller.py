from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.api.dependencies import CurrentUser
from app.core.config import settings
from app.modules.workspace.services.agentbox_manager import agentbox_sandbox_id
from app.modules.workspace.services.workspace_activity_store import WorkspaceActivity
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
    get_workspace_activity_store,
)

router = APIRouter(prefix="/workspace", tags=["Workspace"])

_WORKSPACE_ME_APP_TOKEN_TTL_SECONDS = 600


class WorkspaceMeSandbox(BaseModel):
    id: str
    status: str
    ready: bool
    runtime: str
    updated_at: datetime | None = None


class WorkspaceMeSession(BaseModel):
    session_id: str
    runtime: str
    last_used_at: datetime
    pod_id: UUID | None = None


class WorkspaceMeApp(BaseModel):
    app: str
    url: str
    expires_at: datetime


class WorkspaceMeResponse(BaseModel):
    user_id: UUID
    sandbox: WorkspaceMeSandbox
    active_session: WorkspaceMeSession | None = None
    apps: dict[str, WorkspaceMeApp]


def _active_session_from_activity(
    activity: WorkspaceActivity | None,
) -> WorkspaceMeSession | None:
    if activity is None or not activity.session_id:
        return None
    return WorkspaceMeSession(
        session_id=activity.session_id,
        runtime=activity.runtime,
        last_used_at=activity.last_used_at,
        pod_id=activity.pod_id,
    )


@router.get(
    "/me",
    response_model=WorkspaceMeResponse,
    status_code=status.HTTP_200_OK,
    operation_id="workspace.me",
    summary="Get current workspace state",
)
async def get_workspace_me(user: CurrentUser) -> WorkspaceMeResponse:
    api_key = settings.agentbox_api_key
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace sandbox manager API key is not configured",
        )

    runtime = WorkspaceSandboxService._resolve_runtime()
    sandbox_id = agentbox_sandbox_id(user.id)
    activity = await get_workspace_activity_store().get_activity(
        runtime=runtime,
        user_id=user.id,
    )

    service = WorkspaceSandboxService()
    sandbox = await service.get_or_create_sandbox(user.id)
    browser_access = await service.create_browser_access(
        user.id,
        ttl_seconds=_WORKSPACE_ME_APP_TOKEN_TTL_SECONDS,
        ensure_sandbox=False,
    )

    return WorkspaceMeResponse(
        user_id=user.id,
        sandbox=WorkspaceMeSandbox(
            id=str(sandbox_id),
            status=sandbox.status,
            ready=sandbox.status == "RUNNING",
            runtime=runtime,
            updated_at=datetime.now(timezone.utc),
        ),
        active_session=_active_session_from_activity(activity),
        apps={
            "browser": WorkspaceMeApp(
                app="browser",
                url=browser_access.url,
                expires_at=browser_access.expires_at,
            )
        },
    )
