"""Canonical runtime-profile management routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import OrgContextDep
from app.core.authorization.permissions import Permissions
from app.core.crypto import get_secret_cipher
from app.modules.agent.agent_runtime_defaults import AgentRuntimeDefaultService
from app.modules.agent.api.runtime_profile_presenter import (
    profile_responses_with_runtime_status,
)
from app.modules.agent.api.schemas import (
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
    CreateAgentRuntimeProfileRequest,
    CreateAnthropicCompatibleRuntimeProfileRequest,
    CreateAzureOpenAIRuntimeProfileRequest,
    CreateGoogleVertexRuntimeProfileRequest,
    CreateHarnessRuntimeProfileRequest,
    CreateOpenAICompatibleRuntimeProfileRequest,
    UpdateRuntimeProfileRequest,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileScope,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.repositories import AgentRuntimeProfileRepository
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)

router = APIRouter(tags=["runtime"])


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


async def _profile_response(
    profile: AgentRuntimeProfile,
    *,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentRuntimeProfileResponse:
    return (
        await profile_responses_with_runtime_status(
            [profile],
            user_id=user.id,
            uow=uow,
        )
    )[0]


async def _require_profile(
    *,
    service: AgentRuntimeProfileService,
    profile_id: str,
    org_id: UUID,
    user: CurrentUser,
) -> AgentRuntimeProfile:
    profile = await service.get_profile(
        profile_id=profile_id,
        organization_id=org_id,
        user_id=user.id,
        include_disabled=True,
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runtime profile was not found",
        )
    return profile


async def _authorize_profile_mutation(
    profile: AgentRuntimeProfile,
    *,
    org_id: UUID,
    ctx: OrgContextDep,
) -> None:
    if profile.scope is RuntimeProfileScope.SYSTEM:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System runtime profiles cannot be changed",
        )
    if profile.scope is RuntimeProfileScope.ORGANIZATION:
        await ctx.require(Permissions.ORG_UPDATE, ResourceRef.organization(org_id))


@router.get(
    "/organizations/{org_id}/runtime/profiles",
    response_model=AgentRuntimeProfileListResponse,
    operation_id="runtime.profiles.list",
    summary="List runtime profiles",
)
async def list_runtime_profiles(
    org_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentRuntimeProfileListResponse:
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    profiles = await _runtime_profile_service(uow).list_profiles(
        organization_id=org_id,
        user_id=user.id,
    )
    return AgentRuntimeProfileListResponse(
        items=await profile_responses_with_runtime_status(
            profiles,
            user_id=user.id,
            uow=uow,
        ),
        default_runtime=AgentRuntimeDefaultService().get_default(
            available_profile_ids={profile.id for profile in profiles},
        ),
    )


@router.post(
    "/organizations/{org_id}/runtime/profiles",
    response_model=AgentRuntimeProfileResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="runtime.profiles.create",
    summary="Create a runtime profile",
)
async def create_runtime_profile(
    org_id: UUID,
    data: CreateAgentRuntimeProfileRequest,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeProfileResponse:
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    if data.scope is RuntimeProfileScope.ORGANIZATION:
        await ctx.require(Permissions.ORG_UPDATE, ResourceRef.organization(org_id))
    service = _runtime_profile_service(uow)
    try:
        if isinstance(data, CreateHarnessRuntimeProfileRequest):
            profile = await service.create_harness_profile(
                organization_id=org_id,
                user_id=user.id,
                harness_id=data.harness_id,
                scope=data.scope,
                name=data.name,
                description=data.description,
                default_model_name=data.default_model_name,
                harness_snapshot_revision=data.harness_snapshot_revision,
                config_selections=data.config_selections,
                host_wait_timeout_seconds=data.host_wait_timeout_seconds,
                fallback_profile_id=data.fallback_profile_id,
            )
        elif isinstance(data, CreateOpenAICompatibleRuntimeProfileRequest):
            profile = await service.create_openai_compatible_profile(
                organization_id=org_id,
                user_id=user.id,
                scope=data.scope,
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
                user_id=user.id,
                scope=data.scope,
                name=data.name,
                api_key=data.api_key,
                base_url=data.base_url,
                description=data.description,
                default_model_name=data.default_model_name,
                model_names=data.model_names,
                headers=data.headers,
                model_settings=data.model_settings,
            )
        elif isinstance(data, CreateAzureOpenAIRuntimeProfileRequest):
            profile = await service.create_azure_openai_profile(
                organization_id=org_id,
                user_id=user.id,
                scope=data.scope,
                name=data.name,
                azure_endpoint=data.azure_endpoint,
                api_version=data.api_version,
                api_key=data.api_key,
                description=data.description,
                default_model_name=data.default_model_name,
                model_names=data.model_names,
                model_settings=data.model_settings,
            )
        elif isinstance(data, CreateGoogleVertexRuntimeProfileRequest):
            profile = await service.create_google_vertex_profile(
                organization_id=org_id,
                user_id=user.id,
                scope=data.scope,
                name=data.name,
                project_id=data.project_id,
                location=data.location,
                service_account_json=data.service_account_json,
                description=data.description,
                default_model_name=data.default_model_name,
                model_names=data.model_names,
                model_settings=data.model_settings,
            )
        else:
            raise ValueError("Unsupported runtime profile type")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _profile_response(profile, user=user, uow=uow)


@router.get(
    "/organizations/{org_id}/runtime/profiles/{profile_id}",
    response_model=AgentRuntimeProfileResponse,
    operation_id="runtime.profiles.get",
    summary="Get a runtime profile",
)
async def get_runtime_profile(
    org_id: UUID,
    profile_id: str,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentRuntimeProfileResponse:
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    profile = await _require_profile(
        service=_runtime_profile_service(uow),
        profile_id=profile_id,
        org_id=org_id,
        user=user,
    )
    return await _profile_response(profile, user=user, uow=uow)


@router.patch(
    "/organizations/{org_id}/runtime/profiles/{profile_id}",
    response_model=AgentRuntimeProfileResponse,
    operation_id="runtime.profiles.update",
    summary="Update a runtime profile",
)
async def update_runtime_profile(
    org_id: UUID,
    profile_id: str,
    data: UpdateRuntimeProfileRequest,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeProfileResponse:
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    service = _runtime_profile_service(uow)
    profile = await _require_profile(
        service=service,
        profile_id=profile_id,
        org_id=org_id,
        user=user,
    )
    await _authorize_profile_mutation(profile, org_id=org_id, ctx=ctx)
    try:
        profile = await service.update_profile(
            profile_id=profile_id,
            organization_id=org_id,
            user_id=user.id,
            changes=data.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _profile_response(profile, user=user, uow=uow)


@router.delete(
    "/organizations/{org_id}/runtime/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="runtime.profiles.delete",
    summary="Disable a runtime profile",
)
async def delete_runtime_profile(
    org_id: UUID,
    profile_id: str,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> Response:
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    service = _runtime_profile_service(uow)
    profile = await _require_profile(
        service=service,
        profile_id=profile_id,
        org_id=org_id,
        user=user,
    )
    await _authorize_profile_mutation(profile, org_id=org_id, ctx=ctx)
    await service.disable_profile(
        profile_id=profile_id,
        organization_id=org_id,
        user_id=user.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/organizations/{org_id}/runtime/profiles/{profile_id}/refresh",
    response_model=AgentRuntimeProfileResponse,
    operation_id="runtime.profiles.refresh",
    summary="Refresh a runtime profile",
)
async def refresh_runtime_profile(
    org_id: UUID,
    profile_id: str,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeProfileResponse:
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    service = _runtime_profile_service(uow)
    profile = await _require_profile(
        service=service,
        profile_id=profile_id,
        org_id=org_id,
        user=user,
    )
    await _authorize_profile_mutation(profile, org_id=org_id, ctx=ctx)
    try:
        profile = await service.refresh_profile(
            profile_id=profile_id,
            organization_id=org_id,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _profile_response(profile, user=user, uow=uow)
