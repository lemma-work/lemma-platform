"""Authenticated entry to centralized personal workspace provisioning."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.api.dependencies import UoWDep
from app.modules.identity.api.dependencies import get_organization_service
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.first_workspace import (
    WorkspaceEntry,
    ensure_first_workspace,
)

router = APIRouter(
    prefix="/users/me",
    tags=["Users"],
    redirect_slashes=False,
)


class FirstWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    with_pod: bool = Field(
        default=True,
        description="Ensure a personal pod and assistant. Importers may request only an organization.",
    )


class FirstWorkspaceResponse(BaseModel):
    organization_id: UUID
    pod_id: UUID | None = None
    assistant_id: UUID | None = None
    entry: WorkspaceEntry
    organization_created: bool
    pod_created: bool


@router.post(
    "/first-workspace",
    status_code=status.HTTP_200_OK,
    operation_id="users.ensure_first_workspace",
    summary="Ensure The Current User Has A Workspace",
    description="Select an eligible organization and idempotently ensure the current user has a private pod and assistant.",
    response_model=FirstWorkspaceResponse,
)
async def ensure_workspace(
    request: Request, uow: UoWDep, data: FirstWorkspaceRequest | None = None
) -> FirstWorkspaceResponse:
    # `request.state.user` is the principal the token carries -- an id and
    # nothing else, deliberately, so that most requests need no user read. The
    # naming needs an address and a name, so this is one of the few that does.
    principal = request.state.user
    user = await UserRepository(uow).get(principal.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    workspace = await ensure_first_workspace(
        uow,
        organization_service=get_organization_service(uow),
        user_id=user.id,
        email=str(user.email),
        full_name=" ".join(
            part for part in (user.first_name, user.last_name) if part
        ).strip()
        or None,
        with_pod=(data or FirstWorkspaceRequest()).with_pod,
    )
    return FirstWorkspaceResponse(
        organization_id=workspace.organization_id,
        pod_id=workspace.pod_id,
        entry=workspace.entry,
        assistant_id=workspace.assistant_id,
        organization_created=workspace.organization_created,
        pod_created=workspace.pod_created,
    )
