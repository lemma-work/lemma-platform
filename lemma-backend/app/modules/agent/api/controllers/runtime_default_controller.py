"""The organization's default model, and testing a saved provider.

Kept out of ``runtime_config_controller`` for size, and because neither route
creates or edits a profile's own configuration: one moves a mark between
profiles, the other only reads one and talks to its provider.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import OrgContextDep
from app.core.authorization.permissions import Permissions
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent.api.controllers.runtime_config_controller import (
    _load_profile_or_404,
    _require_profile_editor,
    _runtime_profile_service,
)
from app.modules.agent.api.schemas import (
    AgentRuntimeProfileTestResponse,
    SetOrganizationDefaultRuntimeRequest,
)
from app.modules.agent.domain.runtime_profiles import RuntimeProfileKind
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.services.organization_default_service import (
    OrganizationDefaultNotFoundError,
    OrganizationDefaultService,
)
from app.modules.agent.services.runtime_provider_check import (
    check_saved_provider_connection,
)

router = APIRouter(tags=["agent_runtime"])

_DEFAULT_PATH = "/organizations/{organization_id}/agent-runtime/default"


def _default_service(uow: UoWDep) -> OrganizationDefaultService:
    return OrganizationDefaultService(
        AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher())
    )


@router.put(
    _DEFAULT_PATH,
    response_model=AgentRuntimeConfig,
    operation_id="agent.runtime.default.set",
    summary="Set the Organization's Default Model",
)
async def set_organization_default_runtime(
    organization_id: UUID,
    data: SetOrganizationDefaultRuntimeRequest,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeConfig:
    # Every teammate in every pod that has not pinned a model runs on this, so
    # it takes the same gate as adding an organization-wide provider.
    await ctx.require(Permissions.ORG_UPDATE, ResourceRef.organization(organization_id))
    try:
        return await _default_service(uow).set_default(
            organization_id=organization_id,
            profile_id=data.profile_id,
            model_name=data.model_name,
        )
    except OrganizationDefaultNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runtime profile not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete(
    _DEFAULT_PATH,
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.runtime.default.clear",
    summary="Clear the Organization's Default Model",
)
async def clear_organization_default_runtime(
    organization_id: UUID,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> None:
    await ctx.require(Permissions.ORG_UPDATE, ResourceRef.organization(organization_id))
    await _default_service(uow).clear_default(organization_id=organization_id)


@router.post(
    "/organizations/{organization_id}/agent-runtime/profiles/{profile_id}/test",
    response_model=AgentRuntimeProfileTestResponse,
    operation_id="agent.runtime.profiles.test",
    summary="Test a Saved Model Provider",
)
async def check_runtime_profile_connection(
    organization_id: UUID,
    profile_id: str,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeProfileTestResponse:
    # The editor gate doubles as the only brake on this route: each call is a
    # provider request on the organization's key, so it is limited to the
    # people who could change that key anyway.
    service = _runtime_profile_service(uow)
    profile = await _load_profile_or_404(
        service, profile_id=profile_id, organization_id=organization_id, user=user
    )
    await _require_profile_editor(
        profile=profile, organization_id=organization_id, user=user, ctx=ctx
    )
    if profile.kind is not RuntimeProfileKind.MODEL_PROVIDER:
        # A coding agent is "working" when its computer is online, which the
        # listing already says; there is no key or endpoint here to test.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a model provider can be tested",
        )
    resolved = await service.resolve(
        runtime=AgentRuntimeConfig(profile_id=profile.id),
        organization_id=organization_id,
        user_id=user.id,
    )
    try:
        # Two provider round trips, one of them a model call that can take
        # seconds on a cold local server; nothing is written afterwards.
        async with connection_released(uow.session):
            result = await check_saved_provider_connection(resolved)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return AgentRuntimeProfileTestResponse(
        ok=result.ok, message=result.message, models=result.models
    )
