"""Letting Lemma create a teammate's own Slack app: the token, and the app itself.

Pod-scoped because the reach sheet that asks is, though the token belongs to
the organization: one saved token serves every pod in the org that wants a bot
in that workspace. The organization is the pod's, derived here, so a caller can
never name another one. Reading the list, saving, forgetting and creating each
also need the right to manage the org's connector installs; a reader without
it is told so (``can_manage``) and shown no workspace.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field, SecretStr

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.api.surface_config_resolver import (
    require_surface_agent_action,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.pod.contracts.agent_access import pod_organization_id

if TYPE_CHECKING:
    from app.modules.agent_surfaces.services.slack_app_provisioning import (
        SlackAppProvisioner,
        SlackAppWorkspace,
    )

router = APIRouter(prefix="/pods/{pod_id}/surface-setup/slack", tags=["Agent Surfaces"])


def get_slack_app_calls() -> object:
    """Slack's side, as a dependency so a test can stand in for Slack.

    ``object`` and imported here rather than at module scope: serving the API
    must not load what only these four routes use.
    """
    from app.modules.agent_surfaces.services.slack_app_provisioning import (
        SlackAppCalls,
    )

    return SlackAppCalls()


def _provisioner(uow, calls: object = None) -> SlackAppProvisioner:
    from app.modules.agent_surfaces.services.slack_app_provisioning import (
        SlackAppCalls,
        SlackAppProvisioner,
    )

    return SlackAppProvisioner(
        uow, calls=calls if isinstance(calls, SlackAppCalls) else None
    )


class SlackAppWorkspaceResponse(BaseModel):
    """A Slack workspace Lemma can create apps in. Carries no token."""

    team_id: str
    team_name: str | None = None
    slack_user_id: str | None = Field(
        default=None,
        description=(
            "The Slack user whose configuration token this is. Slack creates "
            "every app as them, and the token can change every app they "
            "collaborate on."
        ),
    )
    saved_at: datetime
    needs_new_token: bool = Field(
        default=False,
        description=(
            "Slack no longer accepts the saved token, or Lemma can no longer "
            "read it: paste a new one before Lemma can create apps here."
        ),
    )


class SlackAppWorkspaceListResponse(BaseModel):
    can_manage: bool = Field(
        description=(
            "Whether the caller may save tokens and create apps: an owner or "
            "editor of the organization. Others are shown no workspace."
        )
    )
    items: list[SlackAppWorkspaceResponse]


class SlackConfigurationTokenRequest(BaseModel):
    refresh_token: SecretStr = Field(
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
    agent_id: UUID | None = Field(
        default=None,
        description=(
            "The agent the app answers as; the pod's own agent when omitted. "
            "The app is named after it."
        ),
    )


class SlackAppCreateResponse(BaseModel):
    """The app Slack made, and the install to authorise it against."""

    app_id: str
    auth_config_id: UUID
    reused: bool = Field(
        default=False,
        description=(
            "An app made earlier for the same agent and workspace (or the same "
            "Idempotency-Key), not a new one."
        ),
    )


async def _organization(uow, pod_id: UUID) -> UUID:
    """The pod's organization: the token and the apps belong to it, not the pod."""
    organization_id = await pod_organization_id(uow, pod_id)
    if organization_id is None:
        raise AgentSurfaceValidationError(f"Pod {pod_id} not found")
    return organization_id


def _workspace(item: SlackAppWorkspace) -> SlackAppWorkspaceResponse:
    return SlackAppWorkspaceResponse(
        team_id=item.team_id,
        team_name=item.team_name,
        slack_user_id=item.slack_user_id,
        saved_at=item.saved_at,
        needs_new_token=item.needs_new_token,
    )


@router.get(
    "/app-creation",
    operation_id="agent.surface.slack_app_creation.list",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_slack_app_workspaces(
    pod_id: UUID, user: CurrentUser, uow: UoWDep, ctx: PodContextDep
) -> SlackAppWorkspaceListResponse:
    """The Slack workspaces this pod's organization lets Lemma create apps in."""
    listed = await _provisioner(uow).workspaces(
        organization_id=await _organization(uow, pod_id), user_id=user.id
    )
    return SlackAppWorkspaceListResponse(
        can_manage=listed.can_manage,
        items=[_workspace(item) for item in listed.items],
    )


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
    calls: object = Depends(get_slack_app_calls),
) -> SlackAppWorkspaceResponse:
    """Let Lemma create Slack apps in the workspace this token is for."""
    saved = await _provisioner(uow, calls).save_token(
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
    """Stop Lemma creating apps there. Apps it already made are not touched.

    Slack offers no way to revoke a configuration token, so it stays valid at
    Slack: revoke it on api.slack.com/apps as well.
    """
    await _provisioner(uow).forget(
        organization_id=await _organization(uow, pod_id),
        user_id=user.id,
        team_id=team_id,
    )


@router.post(
    "/apps",
    operation_id="agent.surface.slack_app.create",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
    responses={
        409: {"description": "Not set up, the token needs replacing, or busy."},
        429: {"description": "Slack is rate-limiting app creation."},
    },
)
async def create_slack_app(
    pod_id: UUID,
    request: SlackAppCreateRequest,
    user: CurrentUser,
    uow: UoWDep,
    ctx: PodContextDep,
    calls: object = Depends(get_slack_app_calls),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=255
    ),
) -> SlackAppCreateResponse:
    """Create an agent's own Slack app, named after it. What is left is
    authorising it.

    One app per agent per workspace: until it is authorised, asking again
    answers with the same app, and so does the same `Idempotency-Key` from the
    same member for a day.
    """
    # Naming no agent means the pod's own, whose row id is the pod's.
    agent_id = request.agent_id or pod_id
    await require_surface_agent_action(
        ctx=ctx, pod_id=pod_id, agent_id=agent_id, action=Permissions.AGENT_UPDATE
    )
    created = await _provisioner(uow, calls).create_app(
        organization_id=await _organization(uow, pod_id),
        pod_id=pod_id,
        agent_id=agent_id,
        user_id=user.id,
        team_id=request.team_id,
        idempotency_key=idempotency_key,
    )
    return SlackAppCreateResponse(
        app_id=created.app_id,
        auth_config_id=created.auth_config_id,
        reused=created.reused,
    )
