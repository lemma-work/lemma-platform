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
    AgentRuntimeProfileDetailResponse,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
    CreateAgentHostRuntimeProfileRequest,
    CreateAnthropicCompatibleRuntimeProfileRequest,
    CreateAgentRuntimeProfileRequest,
    CreateOpenAICompatibleRuntimeProfileRequest,
    UpdateAgentHostRuntimeProfileRequest,
    UpdateAnthropicCompatibleRuntimeProfileRequest,
    UpdateAgentRuntimeProfileRequest,
    UpdateOpenAICompatibleRuntimeProfileRequest,
)
from app.modules.agent.agent_runtime_defaults import AgentRuntimeDefaultService
from app.modules.agent.infrastructure.agent_host_repository import AgentHostRepository
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.api.agent_host_schemas import AgentHostHarnessResponse
from app.modules.agent.domain.agent_host import effective_agent_host_status
from app.modules.agent.domain.runtime_profiles import RuntimeProfileScope
from app.modules.agent.services.runtime_profile_editor import (
    AgentRuntimeProfileEditor,
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


def _profile_response(profile, availability=None) -> AgentRuntimeProfileResponse:
    """Serialize one profile, merging in its derived availability.

    availability is not on the domain entity - it is read from the harness and
    its host at request time - so it is attached here rather than in public_dict.
    """
    data = profile.public_dict()
    if availability is not None:
        data["availability_status"] = availability.value
    return AgentRuntimeProfileResponse.model_validate(data)


async def _require_profile_editor(
    *,
    profile,
    org_id: UUID,
    user: CurrentUser,
    ctx: OrgContextDep,
) -> None:
    """Who may change or read the full detail of one profile.

    A workspace profile is shared configuration, so it takes the same
    ORG_UPDATE the create route requires. A personal one is usable only by its
    owner, so the owner qualifies on their own - an org editor still does too,
    because they can already see it in the listing.
    """
    if profile.scope is RuntimeProfileScope.PERSONAL and profile.user_id == user.id:
        return
    await ctx.require(Permissions.ORG_UPDATE, ResourceRef.organization(org_id))


async def _load_profile_or_404(
    service: AgentRuntimeProfileService,
    *,
    profile_id: str,
    org_id: UUID,
    user: CurrentUser,
):
    if service.repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runtime profile not found",
        )
    profile = await service.repository.get_visible_by_id(
        profile_id=profile_id,
        organization_id=org_id,
        user_id=user.id,
        # An archived profile must still be addressable, or it could never be
        # restored or renamed out of a name collision.
        include_disabled=True,
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runtime profile not found",
        )
    return profile


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
    include_disabled: bool = False,
) -> AgentRuntimeProfileListResponse:
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    service = _runtime_profile_service(uow)
    # Archived profiles are excluded by default: this listing is also the chat
    # model catalog, and a retired model must not stay pickable.
    entries = await service.list_profiles_with_availability(
        organization_id=org_id,
        user_id=user.id,
        include_disabled=include_disabled,
    )
    defaults = AgentRuntimeDefaultService()
    return AgentRuntimeProfileListResponse(
        items=[
            _profile_response(profile, availability)
            for profile, availability in entries
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


@router.get(
    "/organizations/{org_id}/agent-runtime/profiles/{profile_id}",
    response_model=AgentRuntimeProfileDetailResponse,
    operation_id="agent.runtime.profiles.get",
    summary="Get Agent Runtime Profile",
)
async def get_runtime_profile(
    org_id: UUID,
    profile_id: str,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeProfileDetailResponse:
    # Behind the edit gate rather than plain membership: for a workspace-scoped
    # harness profile this returns another member's machine's configuration.
    service = _runtime_profile_service(uow)
    profile = await _load_profile_or_404(
        service, profile_id=profile_id, org_id=org_id, user=user
    )
    await _require_profile_editor(profile=profile, org_id=org_id, user=user, ctx=ctx)

    harness = None
    host_status = None
    if profile.harness_id is not None and service.host_repository is not None:
        harness_row = await service.host_repository.get_harness(
            harness_id=profile.harness_id
        )
        if harness_row is not None:
            harness = AgentHostHarnessResponse.model_validate(harness_row)
            host = await service.host_repository.get(host_id=harness_row.host_id)
            if host is not None:
                host_status = effective_agent_host_status(
                    host.status, host.last_seen_at
                )
    return AgentRuntimeProfileDetailResponse.model_validate(
        {
            **profile.public_dict(),
            "harness": harness,
            "host_status": host_status,
        }
    )


@router.patch(
    "/organizations/{org_id}/agent-runtime/profiles/{profile_id}",
    response_model=AgentRuntimeProfileResponse,
    operation_id="agent.runtime.profiles.update",
    summary="Update Agent Runtime Profile",
)
async def update_runtime_profile(
    org_id: UUID,
    profile_id: str,
    data: UpdateAgentRuntimeProfileRequest,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeProfileResponse:
    service = _runtime_profile_service(uow)
    profile = await _load_profile_or_404(
        service, profile_id=profile_id, org_id=org_id, user=user
    )
    await _require_profile_editor(profile=profile, org_id=org_id, user=user, ctx=ctx)
    editor = AgentRuntimeProfileEditor(service)

    # Only fields the caller actually sent are forwarded. Everything else stays
    # UNSET, which is what tells the editor "leave this alone" as opposed to
    # "set this to null" - the distinction that keeps a rename from wiping a
    # stored API key.
    supplied = data.model_fields_set - {"source", "refresh_models"}
    changes = {field: getattr(data, field) for field in supplied}
    common = dict(
        profile_id=profile_id,
        organization_id=org_id,
        user_id=user.id,
    )
    try:
        if isinstance(data, UpdateAgentHostRuntimeProfileRequest):
            updated = await editor.update_agent_host_profile(**common, **changes)
        elif isinstance(data, UpdateOpenAICompatibleRuntimeProfileRequest):
            updated = await editor.update_openai_compatible_profile(
                **common, refresh_models=data.refresh_models, **changes
            )
        elif isinstance(data, UpdateAnthropicCompatibleRuntimeProfileRequest):
            updated = await editor.update_anthropic_compatible_profile(
                **common, refresh_models=data.refresh_models, **changes
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
    return _profile_response(updated)


@router.delete(
    "/organizations/{org_id}/agent-runtime/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.runtime.profiles.archive",
    summary="Archive Agent Runtime Profile",
)
async def archive_runtime_profile(
    org_id: UUID,
    profile_id: str,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> None:
    # Archive, not delete. Five places store a bare profile id with no foreign
    # key, and a run lease's profile pointer is ON DELETE SET NULL, which would
    # break dispatch idempotency for an in-flight run.
    service = _runtime_profile_service(uow)
    profile = await _load_profile_or_404(
        service, profile_id=profile_id, org_id=org_id, user=user
    )
    await _require_profile_editor(profile=profile, org_id=org_id, user=user, ctx=ctx)
    try:
        await AgentRuntimeProfileEditor(service).archive_profile(
            profile_id=profile_id,
            organization_id=org_id,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return None


@router.post(
    "/organizations/{org_id}/agent-runtime/profiles/{profile_id}:restore",
    response_model=AgentRuntimeProfileResponse,
    operation_id="agent.runtime.profiles.restore",
    summary="Restore Agent Runtime Profile",
)
async def restore_runtime_profile(
    org_id: UUID,
    profile_id: str,
    user: CurrentUser,
    uow: UoWDep,
    ctx: OrgContextDep,
) -> AgentRuntimeProfileResponse:
    service = _runtime_profile_service(uow)
    profile = await _load_profile_or_404(
        service, profile_id=profile_id, org_id=org_id, user=user
    )
    await _require_profile_editor(profile=profile, org_id=org_id, user=user, ctx=ctx)
    try:
        restored = await AgentRuntimeProfileEditor(service).restore_profile(
            profile_id=profile_id,
            organization_id=org_id,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        # Another profile took this name while it sat archived.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return _profile_response(restored)
