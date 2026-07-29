"""Durable external Agent Host v2 management and control APIs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID, uuid7

from fastapi import APIRouter, Header, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.api.agent_host_schemas import (
    AgentHostIntegrationListResponse,
    AgentHostIntegrationPublishRequest,
    AgentHostIntegrationPublishResponse,
    AgentHostIntegrationResponse,
    AgentHostListResponse,
    AgentHostResponse,
)
from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostMcpRouteResponse,
    AgentHostPairingComplete,
    AgentHostPairingCompleted,
    AgentHostPairingCreate,
    AgentHostPairingCreated,
    AgentHostPollRequest,
    AgentHostPollResponse,
    AgentHostStatus,
    AgentHostTokenClaims,
    AgentHostTokenExchange,
    AgentHostTokenResponse,
    effective_agent_host_status,
)
from app.modules.agent.infrastructure.agent_host_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    AgentHostNotFound,
    AgentHostPairingRejected,
    AgentHostProtocolViolation,
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostIntegrationModel,
    AgentHostModel,
)
from app.modules.agent.services.agent_host_auth import (
    InvalidAgentHostCredential,
    generate_pairing_code,
    mint_agent_host_token,
    nonce_hash,
    pairing_code_hash,
    public_key_fingerprint,
    verify_agent_host_token,
    verify_host_signature,
    verify_pairing_signature,
)


router = APIRouter(tags=["agent_host"])
_uow_factory = SessionUnitOfWorkFactory(async_session_maker)
_LONG_POLL_SECONDS = 25.0
_LONG_POLL_INTERVAL_SECONDS = 1.0
_MAX_COMMANDS_PER_POLL = 16
_CONTROL_UPDATE_BACKOFF_MS = 1_000


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise InvalidAgentHostCredential("missing Agent Host authorization")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise InvalidAgentHostCredential("invalid Agent Host authorization")
    return token.strip()


async def _device_claims(
    *,
    authorization: str | None,
    capability: str,
) -> AgentHostTokenClaims:
    try:
        claims = verify_agent_host_token(
            _bearer_token(authorization),
            required_capability=capability,
        )
    except InvalidAgentHostCredential as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_AGENT_HOST_TOKEN", "message": str(exc)},
        ) from exc
    async with _uow_factory() as uow:
        host = await AgentHostRepository(uow).get(claims.host_id)
        if (
            host is None
            or host.user_id != claims.user_id
            or host.organization_id != claims.organization_id
            or host.revoked_at is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "AGENT_HOST_REVOKED_OR_MISSING",
                    "message": "Agent Host is unavailable",
                },
            )
    return claims


async def _require_org_membership(
    *,
    user_id: UUID,
    organization_id: UUID | None,
    uow: UoWDep,
) -> None:
    if organization_id is None:
        return
    from app.composition.identity_notifications import user_is_organization_member

    if not await user_is_organization_member(
        uow,
        user_id=user_id,
        organization_id=organization_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of this organization",
        )


def _host_response(host: AgentHostModel) -> AgentHostResponse:
    return AgentHostResponse(
        id=host.id,
        user_id=host.user_id,
        organization_id=host.organization_id,
        installation_id=host.installation_id,
        public_key_fingerprint=host.public_key_fingerprint,
        display_name=host.display_name,
        status=effective_agent_host_status(host.status, host.last_seen_at),
        protocol_min=host.protocol_min,
        protocol_max=host.protocol_max,
        protocol_version=host.protocol_version,
        host_release=host.host_release,
        adapter_manifest_id=host.adapter_manifest_id,
        instance_id=host.instance_id,
        capacity=host.capacity or {},
        last_seen_at=host.last_seen_at,
        revoked_at=host.revoked_at,
        created_at=host.created_at,
        updated_at=host.updated_at,
    )


def _integration_response(
    integration: AgentHostIntegrationModel,
) -> AgentHostIntegrationResponse:
    return AgentHostIntegrationResponse(
        id=integration.id,
        host_id=integration.host_id,
        integration_key=integration.integration_key,
        display_name=integration.display_name,
        adapter_protocol=integration.adapter_protocol,
        adapter_version=integration.adapter_version,
        upstream_version=integration.upstream_version,
        auth_state=integration.auth_state,
        health=integration.health,
        capabilities=integration.capabilities or {},
        config_revision=integration.config_revision,
        config_options=integration.config_options or [],
        fetched_at=integration.fetched_at,
        stale_after=integration.stale_after,
        stale_reason=integration.stale_reason,
        metadata=integration.integration_metadata or {},
    )


def _repository_error(exc: AgentHostRepositoryError) -> HTTPException:
    if isinstance(exc, AgentHostNotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, (AgentHostProtocolViolation, AgentHostPairingRejected)):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(
        status_code=code,
        detail={"code": type(exc).__name__.upper(), "message": str(exc)},
    )


@router.post(
    "/me/agent-hosts/pairings",
    response_model=AgentHostPairingCreated,
    status_code=status.HTTP_201_CREATED,
    operation_id="agent.host.pairing.create",
)
async def create_agent_host_pairing(
    request: AgentHostPairingCreate,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentHostPairingCreated:
    await _require_org_membership(
        user_id=user.id,
        organization_id=request.organization_id,
        uow=uow,
    )
    code = generate_pairing_code()
    pairing = await AgentHostRepository(uow).create_pairing(
        pairing_id=uuid7(),
        user_id=user.id,
        organization_id=request.organization_id,
        code_hash=pairing_code_hash(code),
        display_name=request.display_name,
    )
    await uow.commit()
    return AgentHostPairingCreated(
        pairing_id=pairing.id,
        pairing_code=code,
        expires_at=pairing.expires_at,
    )


@router.post(
    "/agent-host/v2/pairings:complete",
    response_model=AgentHostPairingCompleted,
    operation_id="agent.host.pairing.complete",
)
async def complete_agent_host_pairing(
    request: AgentHostPairingComplete,
    uow: UoWDep,
) -> AgentHostPairingCompleted:
    try:
        verify_pairing_signature(
            public_key=request.public_key,
            pairing_code=request.pairing_code,
            installation_id=request.hello.installation_id,
            nonce=request.nonce,
            timestamp=request.timestamp,
            signature=request.signature,
        )
        fingerprint = public_key_fingerprint(request.public_key)
        host = await AgentHostRepository(uow).consume_pairing(
            code_hash=pairing_code_hash(request.pairing_code),
            public_key=request.public_key,
            public_key_fingerprint=fingerprint,
            display_name=request.display_name,
            hello=request.hello,
        )
        await uow.commit()
        return AgentHostPairingCompleted(
            host_id=host.id,
            user_id=host.user_id,
            organization_id=host.organization_id,
            public_key_fingerprint=fingerprint,
        )
    except (AgentHostRepositoryError, InvalidAgentHostCredential) as exc:
        if isinstance(exc, AgentHostRepositoryError):
            raise _repository_error(exc) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PUBLIC_KEY", "message": str(exc)},
        ) from exc


@router.post(
    "/agent-host/v2/token:exchange",
    response_model=AgentHostTokenResponse,
    operation_id="agent.host.token.exchange",
)
async def exchange_agent_host_token(
    request: AgentHostTokenExchange,
    uow: UoWDep,
) -> AgentHostTokenResponse:
    repo = AgentHostRepository(uow)
    host = await repo.get(request.host_id, for_update=True)
    if host is None or host.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent Host is unavailable",
        )
    try:
        verify_host_signature(
            public_key=host.public_key,
            host_id=host.id,
            nonce=request.nonce,
            timestamp=request.timestamp,
            signature=request.signature,
        )
        await repo.record_nonce(
            host_id=host.id,
            nonce_hash=nonce_hash(request.nonce),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        token, expires_at = mint_agent_host_token(
            host_id=host.id,
            user_id=host.user_id,
            organization_id=host.organization_id,
        )
        await uow.commit()
        return AgentHostTokenResponse(access_token=token, expires_at=expires_at)
    except (InvalidAgentHostCredential, AgentHostRepositoryError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_AGENT_HOST_PROOF", "message": str(exc)},
        ) from exc


@router.post(
    "/agent-host/v2/poll",
    response_model=AgentHostPollResponse,
    operation_id="agent.host.poll",
)
async def poll_agent_host_commands(
    request: AgentHostPollRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostPollResponse:
    claims = await _device_claims(
        authorization=authorization,
        capability="control",
    )
    deadline = asyncio.get_running_loop().time() + _LONG_POLL_SECONDS
    first = True
    negotiated_protocol = AGENT_HOST_PROTOCOL_VERSION
    host_status = AgentHostStatus.ONLINE
    while True:
        async with _uow_factory() as uow:
            if first:
                host = await AgentHostRepository(uow).mark_seen(
                host_id=claims.host_id,
                hello=request.hello,
                capacity=request.capacity.model_dump(mode="json"),
            )
                negotiated_protocol = (
                    host.protocol_version or AGENT_HOST_PROTOCOL_VERSION
                )
                host_status = AgentHostStatus(host.status)
            commands = await AgentHostDispatchRepository(uow).poll_commands(
                host_id=claims.host_id,
                limit=_MAX_COMMANDS_PER_POLL,
                acknowledged_command_ids=(
                    request.acknowledged_command_ids if first else []
                ),
                checkpoints=request.checkpoints if first else [],
                available_run_slots=request.capacity.available_runs,
            )
            await uow.commit()
            control_update_applied = first and bool(
                request.acknowledged_command_ids or request.checkpoints
            )
            if (
                commands
                or control_update_applied
                or host_status is AgentHostStatus.UPGRADE_REQUIRED
            ):
                return AgentHostPollResponse(
                    protocol_version=negotiated_protocol,
                    policy_revision="agent-host-policy-v1",
                    host_status=host_status,
                    commands=commands,
                    poll_after_ms=(
                        _CONTROL_UPDATE_BACKOFF_MS if control_update_applied else 0
                    ),
                )
        first = False
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return AgentHostPollResponse(
                protocol_version=negotiated_protocol,
                policy_revision="agent-host-policy-v1",
                host_status=host_status,
                commands=[],
            )
        await asyncio.sleep(min(_LONG_POLL_INTERVAL_SECONDS, remaining))


@router.post(
    "/agent-host/v2/events:append",
    response_model=AgentHostEventAck,
    operation_id="agent.host.events.append",
)
async def append_agent_host_events(
    request: AgentHostEventBatch,
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostEventAck:
    claims = await _device_claims(
        authorization=authorization,
        capability="events",
    )
    try:
        ack = await AgentHostDispatchRepository(uow).append_events(
            host_id=claims.host_id,
            batch=request,
        )
        await uow.commit()
        return ack
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc


@router.get(
    "/agent-host/v2/mcp-routes/{route_id}",
    response_model=AgentHostMcpRouteResponse,
    operation_id="agent.host.mcp_route.resolve",
)
async def resolve_agent_host_mcp_route(
    route_id: UUID,
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostMcpRouteResponse:
    claims = await _device_claims(
        authorization=authorization,
        capability="mcp",
    )
    try:
        route = await AgentHostDispatchRepository(uow).resolve_mcp_route(
            route_id=route_id,
            host_id=claims.host_id,
        )
        payload = await get_secret_cipher().decrypt_json_async(route.encrypted_payload)
        if payload is None:
            raise AgentHostProtocolViolation("MCP route payload is unavailable")
        await uow.commit()
        return AgentHostMcpRouteResponse(
            route_id=route.id,
            run_id=route.run_id,
            lease_epoch=route.lease_epoch,
            expires_at=route.expires_at,
            mcp=payload,
        )
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc


@router.put(
    "/agent-host/v2/integrations",
    response_model=AgentHostIntegrationPublishResponse,
    operation_id="agent.host.integrations.publish",
)
async def publish_agent_host_integrations(
    request: AgentHostIntegrationPublishRequest,
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostIntegrationPublishResponse:
    claims = await _device_claims(
        authorization=authorization,
        capability="integrations",
    )
    repo = AgentHostRepository(uow)
    try:
        integrations = [
            await repo.publish_integration(
                host_id=claims.host_id,
                snapshot=snapshot,
            )
            for snapshot in request.integrations
        ]
        await uow.commit()
        return AgentHostIntegrationPublishResponse(
            items=[_integration_response(item) for item in integrations]
        )
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc


@router.post(
    "/agent-host/v2/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.host.self_revoke",
)
async def self_revoke_agent_host(
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Revoke the calling device before its local identity is removed."""

    claims = await _device_claims(
        authorization=authorization,
        capability="control",
    )
    try:
        await AgentHostRepository(uow).revoke(
            host_id=claims.host_id,
            user_id=claims.user_id,
        )
        await uow.commit()
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc


@router.get(
    "/me/agent-hosts",
    response_model=AgentHostListResponse,
    operation_id="agent.host.list",
)
async def list_agent_hosts(
    user: CurrentUser,
    uow: UoWDep,
) -> AgentHostListResponse:
    hosts = await AgentHostRepository(uow).list_for_user(user_id=user.id)
    return AgentHostListResponse(items=[_host_response(host) for host in hosts])


@router.get(
    "/me/agent-hosts/{host_id}/integrations",
    response_model=AgentHostIntegrationListResponse,
    operation_id="agent.host.integrations.list",
)
async def list_agent_host_integrations(
    host_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentHostIntegrationListResponse:
    repo = AgentHostRepository(uow)
    if await repo.get_for_user(host_id=host_id, user_id=user.id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    integrations = await repo.list_integrations(host_id=host_id)
    return AgentHostIntegrationListResponse(
        items=[_integration_response(item) for item in integrations]
    )


@router.delete(
    "/me/agent-hosts/{host_id}",
    response_model=AgentHostResponse,
    operation_id="agent.host.revoke",
)
async def revoke_agent_host(
    host_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentHostResponse:
    try:
        host = await AgentHostRepository(uow).revoke(
            host_id=host_id,
            user_id=user.id,
        )
        await uow.commit()
        return _host_response(host)
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc
