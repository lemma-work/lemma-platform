"""Agent runtime discovery routes."""

from __future__ import annotations

import asyncio
import contextlib
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from supertokens_python.exceptions import SuperTokensError
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import OrgContextDep
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.agent.api.runtime_profile_presenter import (
    profile_responses_with_runtime_status,
)
from app.modules.agent.api.schemas import (
    AgentHarnessInfo,
    AgentHarnessListResponse,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
    CreateAnthropicCompatibleRuntimeProfileRequest,
    CreateAgentHostRuntimeProfileRequest,
    CreateAgentRuntimeProfileRequest,
    CreateOpenAICompatibleRuntimeProfileRequest,
    CreateUserDaemonRuntimeProfileRequest,
)
from app.modules.agent.agent_runtime_defaults import AgentRuntimeDefaultService
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileScope,
)
from app.modules.agent.infrastructure.daemon_hub import (
    agent_runtime_daemon_hub,
)
from app.modules.agent.infrastructure.agent_runtime_redis import (
    clear_daemon_capacity,
    set_daemon_capacity,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeDaemonRepository,
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
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
        daemon_repository=AgentRuntimeDaemonRepository(uow),
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
        items=await profile_responses_with_runtime_status(
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
    # Personal daemon/Agent Host profiles are private to the current user and
    # require membership only. Organization profiles and provider credentials
    # are shared with every member, so they require org editor/owner.
    await _ensure_org_member(org_id=org_id, user=user, uow=uow)
    requested_scope = getattr(data, "scope", RuntimeProfileScope.ORGANIZATION)
    if requested_scope is RuntimeProfileScope.ORGANIZATION:
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
        elif isinstance(data, CreateAgentHostRuntimeProfileRequest):
            profile = await service.create_agent_host_profile(
                organization_id=org_id,
                user_id=user.id,
                host_integration_id=data.host_integration_id,
                scope=data.scope,
                name=data.name,
                description=data.description,
                integration_snapshot_revision=data.integration_snapshot_revision,
                config_selections=data.config_selections,
                host_wait_timeout_seconds=data.host_wait_timeout_seconds,
                fallback_profile_id=data.fallback_profile_id,
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
        await profile_responses_with_runtime_status(
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
    except PermissionError, SuperTokensError:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Unauthorized daemon websocket.",
        )
        return
    user_id = UUID(session.get_user_id())
    await websocket.accept()
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    daemon_id: UUID | None = None
    stale_reaper_task: asyncio.Task[None] | None = None
    try:
        daemon_id, payload = await _receive_daemon_ready(
            websocket=websocket,
                user_id=user_id,
            uow_factory=uow_factory,
            )
        await agent_runtime_daemon_hub.register(
            daemon_id=daemon_id,
            user_id=user_id,
            websocket=websocket,
        )
        await _store_capacity_if_present(daemon_id, payload.get("capacity"))
        # Reattach any runs the daemon says it's still holding from a prior
        # connection BEFORE acking readiness, so a run.start/run.stop that
        # arrives right after can find the reattached queue rather than racing
        # ahead of this.
        reattach_run_ids = _reattach_agent_run_ids(payload.get("reattach_runs"))
        if reattach_run_ids:
            await agent_runtime_daemon_hub.reattach_runs(
                daemon_id=daemon_id,
                user_id=user_id,
                agent_run_ids=reattach_run_ids,
            )
        await websocket.send_json(
            {
                "type": "daemon.ready_ack",
                "daemon_id": str(daemon_id),
            }
        )
        last_ping_monotonic = _MutableMonotonic()
        stale_reaper_task = create_inherited_task(
            _close_if_ping_stale(websocket, last_ping_monotonic, daemon_id=daemon_id)
        )
        await _serve_daemon_messages(
            websocket=websocket,
                        daemon_id=daemon_id,
                        user_id=user_id,
            uow_factory=uow_factory,
            last_ping_monotonic=last_ping_monotonic,
                    )
    except WebSocketDisconnect:
        pass
    finally:
        if stale_reaper_task is not None:
            stale_reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stale_reaper_task
        if daemon_id is not None:
            await agent_runtime_daemon_hub.unregister(
                daemon_id=daemon_id,
                user_id=user_id,
                websocket=websocket,
            )
            # A disconnected daemon's last-known capacity has no actionable
            # meaning -- clear it immediately rather than relying solely on
            # the Redis TTL, which would let start_run() see stale
            # not-at-capacity data for up to _DAEMON_CAPACITY_TTL_SECONDS
            # after a crash.
            await clear_daemon_capacity(daemon_id=daemon_id)
            async with uow_factory() as uow:
                await AgentRuntimeDaemonRepository(uow).mark_offline(
                    daemon_id=daemon_id,
                    user_id=user_id,
                )


async def _receive_daemon_ready(
    *,
    websocket: WebSocket,
    user_id: UUID,
    uow_factory: SessionUnitOfWorkFactory,
) -> tuple[UUID, dict[str, object]]:
    ready_message = await websocket.receive_json()
    if ready_message.get("type") != "daemon.ready":
        await websocket.close(code=1008, reason="daemon.ready required")
        raise WebSocketDisconnect(code=1008)
    payload = ready_message.get("payload") or {}
    if not isinstance(payload, dict):
        await websocket.close(code=1008, reason="Invalid daemon.ready payload")
        raise WebSocketDisconnect(code=1008)
    device_key = str(payload.get("device_key") or "").strip()
    if not device_key:
        await websocket.close(code=1008, reason="device_key required")
        raise WebSocketDisconnect(code=1008)
    async with uow_factory() as uow:
        daemon = await AgentRuntimeDaemonRepository(uow).upsert_ready(
            user_id=user_id,
            device_key=device_key,
            display_name=str(payload.get("display_name") or "Lemma daemon"),
            device_info=_json_object(payload.get("device_info")),
            harness_catalog=_json_object(payload.get("harness_catalog")),
        )
    return daemon.id, payload


async def _serve_daemon_messages(
    *,
    websocket: WebSocket,
    daemon_id: UUID,
    user_id: UUID,
    uow_factory: SessionUnitOfWorkFactory,
    last_ping_monotonic: _MutableMonotonic,
) -> None:
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
            await _store_capacity_if_present(daemon_id, message.get("capacity"))
        elif message_type == "run.event":
            await agent_runtime_daemon_hub.handle_run_event(
                daemon_id=daemon_id,
                user_id=user_id,
                message=message,
            )
        elif message_type == "daemon.ping":
            last_ping_monotonic.value = time.monotonic()
            async with uow_factory() as uow:
                await AgentRuntimeDaemonRepository(uow).mark_seen(
                    daemon_id=daemon_id,
                    user_id=user_id,
                )
            await _store_capacity_if_present(
                daemon_id, _json_object(message.get("payload")).get("capacity")
            )
            await websocket.send_json({"type": "daemon.pong"})


class _MutableMonotonic:
    """Tiny mutable holder so the reaper task can observe ping updates.

    Plain closures over a local variable can't be reassigned from another
    task without ``nonlocal`` plumbing across two separate coroutines; a
    one-field object is simpler than threading an ``asyncio.Event`` for this.
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = time.monotonic()


async def _close_if_ping_stale(
    websocket: WebSocket,
    last_ping_monotonic: _MutableMonotonic,
    *,
    daemon_id: UUID,
) -> None:
    """Proactively close a daemon websocket that's gone quiet on ``daemon.ping``.

    Backstop for a half-open connection the daemon's own client-side heartbeat
    doesn't notice (e.g. one direction of the socket is silently dropped by a
    middlebox). Normal disconnects are already detected instantly via
    ``WebSocketDisconnect`` elsewhere in this route -- this only fires when the
    transport looks alive but has stopped carrying heartbeats.
    """
    threshold = agent_settings.daemon_ws_ping_stale_after_seconds
    poll_interval = max(1.0, threshold / 3)
    while True:
        await asyncio.sleep(poll_interval)
        if time.monotonic() - last_ping_monotonic.value > threshold:
            logger.debug(
                "agent.runtime_config_controller.daemon_websocket_stale_no_daemon.diagnostic",
                daemon_id=str(daemon_id),
                threshold_seconds=threshold,
            )
            with contextlib.suppress(Exception):
                await websocket.close(
                    code=status.WS_1001_GOING_AWAY, reason="Heartbeat timeout"
                )
            return


def _json_object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _reattach_agent_run_ids(raw: object) -> list[UUID]:
    if not isinstance(raw, list):
        return []
    agent_run_ids: list[UUID] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            agent_run_ids.append(UUID(str(item.get("agent_run_id"))))
        except ValueError:
            continue
    return agent_run_ids


async def _store_capacity_if_present(daemon_id: UUID, raw_capacity: object) -> None:
    if not isinstance(raw_capacity, dict):
        return
    active = raw_capacity.get("active_run_count")
    cap = raw_capacity.get("max_concurrent_runs")
    if not isinstance(active, int) or not isinstance(cap, int):
        return
    await set_daemon_capacity(
        daemon_id=daemon_id, active_run_count=active, max_concurrent_runs=cap
    )


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
                str(item) for item in raw_models if available and str(item).strip()
            ]
            model_catalog = _harness_model_catalog(raw_info) if available else []
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
            provider_model_name = (
                str(item.get("provider_model_name") or name).strip() or name
            )
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
