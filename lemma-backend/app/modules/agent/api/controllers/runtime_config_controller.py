"""Agent runtime discovery routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import OrgContextDep
from app.core.authorization.permissions import Permissions
from app.core.log.log import get_logger
from app.modules.agent.api.schemas import (
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
    CreateAgentHostRuntimeProfileRequest,
    CreateAnthropicCompatibleRuntimeProfileRequest,
    CreateAgentRuntimeProfileRequest,
    CreateOpenAICompatibleRuntimeProfileRequest,
)
from app.modules.agent.agent_runtime_defaults import AgentRuntimeDefaultService
from app.modules.agent.infrastructure.agent_host_repository import AgentHostRepository
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)
from app.core.crypto import get_secret_cipher

logger = get_logger(__name__)

router = APIRouter(tags=["agent_runtime"])


async def _ensure_org_member(
    *,
    org_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
) -> None:
    from app.composition.identity_notifications import user_is_organization_member

    if not await user_is_organization_member(
        uow,
        user_id=user.id,
        organization_id=org_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of this organization",
        )


def _runtime_profile_service(uow: UoWDep) -> AgentRuntimeProfileService:
    return AgentRuntimeProfileService(
        repository=AgentRuntimeProfileRepository(
            uow,
            encryption=get_secret_cipher(),
        ),
        host_repository=AgentHostRepository(uow),
    )


@router.get(
    "/organizations/{org_id}/agent-runtime/profiles",
    response_model=AgentRuntimeProfileListResponse,
    operation_id="agent.runtime.profiles.list",
    summary="List Available Agent Runtime Profiles",
)
async def list_available_runtime_profiles(
    org_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentRuntimeProfileListResponse:
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    service = _runtime_profile_service(uow)
    profiles = await service.list_profiles(
        organization_id=org_id,
        user_id=user.id,
    )
    defaults = AgentRuntimeDefaultService()
    return AgentRuntimeProfileListResponse(
        items=[
            AgentRuntimeProfileResponse.model_validate(profile.public_dict())
            for profile in profiles
        ],
        default_runtime=defaults.get_default(),
    )


@router.post(
    "/organizations/{org_id}/agent-runtime/profiles",
    response_model=AgentRuntimeProfileResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="agent.runtime.profiles.create",
    summary="Create Agent Runtime Profile",
)
async def create_runtime_profile(
    org_id: UUID,
    data: CreateAgentRuntimeProfileRequest,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeProfileResponse:
    # Creating an ORGANIZATION-scoped runtime profile registers an org-wide model
    # provider (a caller-controlled base_url/api_key) usable by every member's
    # agent runs, so it must require org editor/owner — not mere membership.
    await ctx.require(Permissions.ORG_UPDATE, ResourceRef.organization(org_id))
    service = _runtime_profile_service(uow)
    try:
        if isinstance(data, CreateAgentHostRuntimeProfileRequest):
            profile = await service.create_agent_host_profile(
                organization_id=org_id,
                user_id=user.id,
                harness_id=data.harness_id,
                name=data.name,
                scope=data.scope,
                description=data.description,
                default_model_name=data.default_model_name,
                config_selections=data.config_selections,
            )
        elif isinstance(data, CreateOpenAICompatibleRuntimeProfileRequest):
            profile = await service.create_openai_compatible_profile(
                organization_id=org_id,
                name=data.name,
                base_url=data.base_url,
                api_key=data.api_key,
                description=data.description,
                default_model_name=data.default_model_name,
                model_names=data.model_names,
                headers=data.headers,
                model_settings=data.model_settings,
            )
        elif isinstance(data, CreateAnthropicCompatibleRuntimeProfileRequest):
            profile = await service.create_anthropic_compatible_profile(
                organization_id=org_id,
                name=data.name,
                api_key=data.api_key,
                base_url=data.base_url,
                description=data.description,
                default_model_name=data.default_model_name,
                model_names=data.model_names,
                headers=data.headers,
                model_settings=data.model_settings,
            )
        else:
            raise ValueError("Unsupported runtime profile source")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return AgentRuntimeProfileResponse.model_validate(profile.public_dict())
