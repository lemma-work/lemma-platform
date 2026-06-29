"""Agent runtime discovery routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from supertokens_python.recipe.session.asyncio import get_session_without_request_response
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import OrgContextDep
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.api.schemas import (
    AgentHarnessInfo,
    AgentHarnessListResponse,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
    CreateAnthropicCompatibleRuntimeProfileRequest,
    CreateAgentRuntimeProfileRequest,
    CreateOpenAICompatibleRuntimeProfileRequest,
    CreateUserDaemonRuntimeProfileRequest,
)
from app.modules.agent.agent_runtime_defaults import AgentRuntimeDefaultService
from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)
from app.modules.agent.infrastructure.daemon_hub import agent_runtime_daemon_hub
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeDaemonRepository,
    AgentRuntimeProfileRepository,
)
from app.modules.agent.services.runtime_profile_service import AgentRuntimeProfileService
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.core.crypto import get_secret_cipher

router = APIRouter(tags=["agent_runtime"])


async def _ensure_org_member(
    *,
    org_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
) -> None:
    member = await OrganizationRepository(uow).get_member(user.id, org_id)
    if member is None:
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
        daemon_repository=AgentRuntimeDaemonRepository(uow),
    )


async def _profile_responses_with_daemon_status(
    profiles: list[AgentRuntimeProfile],
    *,
    user_id: UUID,
    uow: UoWDep,
) -> list[AgentRuntimeProfileResponse]:
    daemon_repo = AgentRuntimeDaemonRepository(uow)
    responses: list[AgentRuntimeProfileResponse] = []
    for profile in profiles:
        payload = profile.public_dict()
        if profile.daemon_id is not None:
            payload.update(
                await _daemon_status_payload(
                    profile,
                    daemon_repo=daemon_repo,
                    user_id=user_id,
                )
            )
        responses.append(AgentRuntimeProfileResponse.model_validate(payload))
    return responses


async def _daemon_status_payload(
    profile: AgentRuntimeProfile,
    *,
    daemon_repo: AgentRuntimeDaemonRepository,
    user_id: UUID,
) -> dict[str, object]:
    if profile.user_id is None or profile.daemon_id is None:
        return {
            "daemon_harness_available": False,
            "availability_status": "UNAVAILABLE",
        }
    daemon = await daemon_repo.get_for_user(
        daemon_id=profile.daemon_id,
        user_id=profile.user_id,
    )
    if daemon is None:
        return {
            "daemon_harness_available": False,
            "availability_status": "UNAVAILABLE",
        }
    catalog = _json_object(getattr(daemon, "harness_catalog", None))
    raw_info = catalog.get(profile.derived_harness_kind().value)
    harness_available = isinstance(raw_info, dict) and raw_info.get("available") is not False
    if profile.scope.value == "PERSONAL" and profile.user_id != user_id:
        availability_status = "UNAVAILABLE_FOR_YOU"
    elif daemon.status != "ONLINE":
        availability_status = "OFFLINE"
    elif not harness_available:
        availability_status = "NOT_INSTALLED"
    else:
        availability_status = "READY"
    return {
        "daemon_display_name": daemon.display_name,
        "daemon_status": daemon.status,
        "daemon_harness_available": harness_available,
        "availability_status": availability_status,
    }


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
        items=await _profile_responses_with_daemon_status(
            profiles,
            user_id=user.id,
            uow=uow,
        ),
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
        if isinstance(data, CreateUserDaemonRuntimeProfileRequest):
            profile = await service.create_user_daemon_profile(
                organization_id=org_id,
                user_id=user.id,
                daemon_id=data.daemon_id,
                harness_kind=data.harness_kind,
                name=data.name,
                scope=data.scope,
                description=data.description,
                default_model_name=data.default_model_name,
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
    return (
        await _profile_responses_with_daemon_status(
            [profile],
            user_id=user.id,
            uow=uow,
        )
    )[0]


@router.get(
    "/agent-runtime/harnesses",
    response_model=AgentHarnessListResponse,
    operation_id="agent.runtime.harnesses.list",
    summary="List Available Agent Harnesses",
)
async def list_available_harnesses(
    user: CurrentUser,
    uow: UoWDep,
) -> AgentHarnessListResponse:
    daemons = await AgentRuntimeDaemonRepository(uow).list_for_user(user_id=user.id)
    return AgentHarnessListResponse(
        items=_harness_infos_from_daemons(daemons),
    )


@router.websocket("/me/agent-runtime/daemon/ws")
async def daemon_websocket(websocket: WebSocket) -> None:
    try:
        session = await _daemon_websocket_session(websocket)
    except TryRefreshTokenError:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Access token expired. Run `lemma auth login`.",
        )
        return
    except Exception:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Unauthorized daemon websocket.",
        )
        return
    user_id = UUID(session.get_user_id())
    await websocket.accept()
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    daemon_id: UUID | None = None
    try:
        ready_message = await websocket.receive_json()
        if ready_message.get("type") != "daemon.ready":
            await websocket.close(code=1008, reason="daemon.ready required")
            return
        payload = ready_message.get("payload") or {}
        if not isinstance(payload, dict):
            await websocket.close(code=1008, reason="Invalid daemon.ready payload")
            return
        device_key = str(payload.get("device_key") or "").strip()
        if not device_key:
            await websocket.close(code=1008, reason="device_key required")
            return
        async with uow_factory() as uow:
            daemon = await AgentRuntimeDaemonRepository(uow).upsert_ready(
                user_id=user_id,
                device_key=device_key,
                display_name=str(payload.get("display_name") or "Lemma daemon"),
                device_info=_json_object(payload.get("device_info")),
                harness_catalog=_json_object(payload.get("harness_catalog")),
            )
            daemon_id = daemon.id
        await agent_runtime_daemon_hub.register(
            daemon_id=daemon_id,
            user_id=user_id,
            websocket=websocket,
        )
        await websocket.send_json(
            {
                "type": "daemon.ready_ack",
                "daemon_id": str(daemon_id),
            }
        )
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "daemon.catalog":
                catalog = _json_object(message.get("payload") or message.get("catalog"))
                async with uow_factory() as uow:
                    await AgentRuntimeDaemonRepository(uow).update_catalog(
                        daemon_id=daemon_id,
                        user_id=user_id,
                        harness_catalog=catalog,
                    )
                continue
            if message_type == "run.event":
                await agent_runtime_daemon_hub.handle_run_event(
                    daemon_id=daemon_id,
                    user_id=user_id,
                    message=message,
                )
                continue
            if message_type == "daemon.ping":
                async with uow_factory() as uow:
                    await AgentRuntimeDaemonRepository(uow).mark_seen(
                        daemon_id=daemon_id,
                        user_id=user_id,
                    )
                await websocket.send_json({"type": "daemon.pong"})
    except WebSocketDisconnect:
        pass
    finally:
        if daemon_id is not None:
            await agent_runtime_daemon_hub.unregister(
                daemon_id=daemon_id,
                user_id=user_id,
            )
            async with uow_factory() as uow:
                await AgentRuntimeDaemonRepository(uow).mark_offline(
                    daemon_id=daemon_id,
                    user_id=user_id,
                )


def _json_object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


async def _daemon_websocket_session(websocket: WebSocket):
    authorization = websocket.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise PermissionError("Daemon websocket requires bearer authorization.")
    return await get_session_without_request_response(
        token.strip(),
        anti_csrf_check=False,
        session_required=True,
    )




def _harness_infos_from_daemons(daemons: list[object]) -> list[AgentHarnessInfo]:
    items: list[AgentHarnessInfo] = []
    for daemon in daemons:
        if getattr(daemon, "status", None) != "ONLINE":
            continue
        catalog = _json_object(getattr(daemon, "harness_catalog", None))
        for raw_kind, raw_info in catalog.items():
            try:
                harness_kind = HarnessKind(raw_kind)
            except ValueError:
                continue
            if not isinstance(raw_info, dict):
                continue
            available = raw_info.get("available") is not False
            raw_models = raw_info.get("models") or []
            models = [
                str(item)
                for item in raw_models
                if available and str(item).strip()
            ]
            model_catalog = (
                _harness_model_catalog(raw_info) if available else []
            )
            items.append(
                AgentHarnessInfo(
                    harness_kind=harness_kind,
                    display_name=str(
                        raw_info.get("display_name")
                        or f"{harness_kind.value} on {daemon.display_name}"
                    ),
                    models=models,
                    model_catalog=model_catalog,
                    available=available,
                    availability_status="READY" if available else "NOT_INSTALLED",
                    daemon_id=daemon.id,
                    daemon_display_name=daemon.display_name,
                    daemon_status=daemon.status,
                )
            )
    return items


def _harness_model_catalog(raw_info: dict) -> list[RuntimeModelCatalogEntry]:
    """Structured model entries for a detected harness.

    Uses the daemon's ``model_catalog`` (display name + provider model id +
    metadata) when present, falling back to the flat ``models`` aliases for
    daemons that predate the structured catalog.
    """
    capabilities = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
    raw_catalog = raw_info.get("model_catalog")
    entries: list[RuntimeModelCatalogEntry] = []
    if isinstance(raw_catalog, list):
        for item in raw_catalog:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            provider_model_name = str(item.get("provider_model_name") or name).strip() or name
            display_name = str(item.get("display_name") or "").strip() or name
            metadata = item.get("metadata")
            entries.append(
                RuntimeModelCatalogEntry(
                    name=name,
                    display_name=display_name,
                    provider_model_name=provider_model_name,
                    capabilities=capabilities,
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
            )
    if entries:
        return entries
    # Back-compat: build plain entries from the flat models list.
    return [
        RuntimeModelCatalogEntry(
            name=str(model).strip(),
            display_name=str(model).strip(),
            provider_model_name=str(model).strip(),
            capabilities=capabilities,
        )
        for model in (raw_info.get("models") or [])
        if str(model).strip()
    ]
