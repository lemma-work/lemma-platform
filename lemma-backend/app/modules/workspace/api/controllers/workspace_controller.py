from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from agentbox_client import AgentBoxClient
from app.core.api.dependencies import CurrentUser
from app.core.config import settings
from app.core.request_context import correlation_headers
from app.modules.workspace.services.agentbox_manager import agentbox_sandbox_id
from app.modules.workspace.services.workspace_activity_store import WorkspaceActivity
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
    get_workspace_activity_store,
    get_workspace_state_store,
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

    client = AgentBoxClient(
        base_url=settings.agentbox_api_url,
        api_key=api_key,
        timeout_seconds=300.0,
        context_headers_provider=correlation_headers,
    )
    try:
        sandbox = await client.ensure_sandbox(
            sandbox_id,
            env={
                "LEMMA_BASE_URL": (
                    WorkspaceSandboxService.resolve_workspace_api_url_for_runtime(
                        runtime
                    )
                )
            },
        )
        browser_access = await client.get_app_access_url(
            sandbox_id,
            "browser",
            ttl_seconds=_WORKSPACE_ME_APP_TOKEN_TTL_SECONDS,
        )
    finally:
        await client.close()

    await get_workspace_state_store().mark_running(
        runtime=runtime,
        user_id=user.id,
        pod_name=None,
        container_name=sandbox.id,
        namespace=None,
        workspace_url=f"agentbox://{sandbox_id}",
    )

    return WorkspaceMeResponse(
        user_id=user.id,
        sandbox=WorkspaceMeSandbox(
            id=sandbox_id,
            status=sandbox.status,
            ready=sandbox.ready,
            runtime=runtime,
            updated_at=datetime.now(timezone.utc),
        ),
        active_session=_active_session_from_activity(activity),
        apps={
            "browser": WorkspaceMeApp(
                app=browser_access.app,
                url=browser_access.url,
                expires_at=datetime.fromtimestamp(
                    browser_access.expires_at,
                    tz=timezone.utc,
                ),
            )
        },
    )
