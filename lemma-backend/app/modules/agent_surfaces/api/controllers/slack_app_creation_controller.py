"""Letting Lemma create a pod's own Slack app: the token, and the app itself.

Pod-scoped because the reach sheet that asks is, though the token belongs to
the organization: one saved token serves every pod in the org that wants a bot
in that workspace. Saving it, forgetting it and creating an app each also need
the right to add the org's connector installs, which the service checks.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.api.surface_config_resolver import (
    require_surface_agent_action,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.pod.contracts.agent_access import pod_organization_id
from app.modules.agent_surfaces.services.slack_app_provisioning import (
    SlackAppProvisioner,
    SlackAppWorkspace,
)

router = APIRouter(prefix="/pods/{pod_id}/surface-setup/slack", tags=["Agent Surfaces"])


class SlackAppWorkspaceResponse(BaseModel):
    """A Slack workspace Lemma can create apps in. Carries no token."""

    team_id: str
    team_name: str | None = None
    saved_at: datetime


class SlackAppWorkspaceListResponse(BaseModel):
    items: list[SlackAppWorkspaceResponse]


class SlackConfigurationTokenRequest(BaseModel):
    refresh_token: str = Field(
        min_length=1,
        max_length=1024,
        description=(
            "The refresh token of an app configuration token, from "
            "api.slack.com/apps. Lemma rotates it at once, which proves it works "
            "and names the workspace it is for."
        ),
    )


class SlackAppCreateRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=32)
    app_name: str = Field(
        min_length=1,
        max_length=255,
        description="What the app and its bot are called: the teammate's name.",
    )


class SlackAppCreateResponse(BaseModel):
    """The app Slack made, and the install to authorise it against."""

    app_id: str
    auth_config_id: UUID


async def _organization(uow, pod_id: UUID) -> UUID:
    """The pod's organization: the token and the apps belong to it, not the pod."""
    organization_id = await pod_organization_id(uow, pod_id)
    if organization_id is None:
        raise AgentSurfaceValidationError(f"Pod {pod_id} not found")
    return organization_id


def _workspace(item: SlackAppWorkspace) -> SlackAppWorkspaceResponse:
    return SlackAppWorkspaceResponse(
        team_id=item.team_id, team_name=item.team_name, saved_at=item.saved_at
    )


@router.get(
    "/app-creation",
    operation_id="agent.surface.slack_app_creation.list",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_slack_app_workspaces(
    pod_id: UUID, uow: UoWDep, ctx: PodContextDep
) -> SlackAppWorkspaceListResponse:
    """The Slack workspaces this pod's organization lets Lemma create apps in."""
    items = await SlackAppProvisioner(uow).workspaces(await _organization(uow, pod_id))
    return SlackAppWorkspaceListResponse(items=[_workspace(item) for item in items])


@router.put(
    "/app-creation",
    operation_id="agent.surface.slack_app_creation.save",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def save_slack_configuration_token(
    pod_id: UUID,
    request: SlackConfigurationTokenRequest,
    user: CurrentUser,
    uow: UoWDep,
    ctx: PodContextDep,
) -> SlackAppWorkspaceResponse:
    """Let Lemma create Slack apps in the workspace this token is for."""
    saved = await SlackAppProvisioner(uow).save_token(
        organization_id=await _organization(uow, pod_id),
        user_id=user.id,
        refresh_token=request.refresh_token,
    )
    return _workspace(saved)


@router.delete(
    "/app-creation/{team_id}",
    operation_id="agent.surface.slack_app_creation.forget",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
    status_code=204,
)
async def forget_slack_configuration_token(
    pod_id: UUID, team_id: str, user: CurrentUser, uow: UoWDep, ctx: PodContextDep
) -> None:
    """Stop Lemma creating apps there. Apps it already made are not touched."""
    await SlackAppProvisioner(uow).forget(
        organization_id=await _organization(uow, pod_id),
        user_id=user.id,
        team_id=team_id,
    )


@router.post(
    "/apps",
    operation_id="agent.surface.slack_app.create",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def create_slack_app(
    pod_id: UUID,
    request: SlackAppCreateRequest,
    user: CurrentUser,
    uow: UoWDep,
    ctx: PodContextDep,
) -> SlackAppCreateResponse:
    """Create this pod's own Slack app. What is left is authorising it."""
    # The app is for the pod's own assistant, whose row id is the pod's -- the
    # agent a surface made on it will belong to, as on the plain create.
    await require_surface_agent_action(
        ctx=ctx, pod_id=pod_id, agent_id=pod_id, action=Permissions.AGENT_UPDATE
    )
    created = await SlackAppProvisioner(uow).create_pod_app(
        organization_id=await _organization(uow, pod_id),
        user_id=user.id,
        team_id=request.team_id,
        app_name=request.app_name,
    )
    return SlackAppCreateResponse(
        app_id=created.app_id, auth_config_id=created.auth_config_id
    )
