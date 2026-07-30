"""Durable external Agent Host management and control APIs."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated
from uuid import UUID, uuid7

from fastapi import APIRouter, Header, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.api.agent_host_schemas import (
    AgentHostHarnessListResponse,
    AgentHostHarnessPublishRequest,
    AgentHostHarnessPublishResponse,
    AgentHostHarnessResponse,
    AgentHostListResponse,
    AgentHostResponse,
)
from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostPairingComplete,
    AgentHostPairingCompleted,
    AgentHostPairingCreate,
    AgentHostPairingCreated,
    AgentHostPollRequest,
    AgentHostPollResponse,
    AgentHostStatus,
    effective_agent_host_status,
)
from app.modules.agent.infrastructure.agent_host_channels import (
    host_poke_channel,
    publish_run_stream_event,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.agent_host_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    AgentHostNotFound,
    AgentHostPairingRejected,
    AgentHostProtocolViolation,
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostHarnessModel,
    AgentHostModel,
)
from app.modules.agent.services.agent_host_auth import (
    InvalidAgentHostCredential,
    generate_host_secret,
    generate_pairing_code,
    host_secret_hash,
    pairing_code_hash,
)


router = APIRouter(tags=["agent_host"])
_uow_factory = SessionUnitOfWorkFactory(async_session_maker)
_LONG_POLL_SECONDS = 25.0
# Safety re-query interval while long-polling idle; a poke on the host's
# realtime channel is the fast path, this bounds a missed poke.
_IDLE_REPOLL_SECONDS = 5.0
_MAX_COMMANDS_PER_POLL = 16
_CONTROL_UPDATE_BACKOFF_MS = 1_000


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise InvalidAgentHostCredential("missing Agent Host authorization")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise InvalidAgentHostCredential("invalid Agent Host authorization")
    return token.strip()


async def _authenticated_host(
    *,
    authorization: str | None,
    uow: SqlAlchemyUnitOfWork,
) -> AgentHostModel:
    """Resolve the calling host from its bearer secret in one lookup."""
    try:
        secret = _bearer_token(authorization)
    except InvalidAgentHostCredential as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_AGENT_HOST_CREDENTIAL", "message": str(exc)},
        ) from exc
    host = await AgentHostRepository(uow).get_by_secret_hash(
        host_secret_hash(secret)
    )
    if host is None or host.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AGENT_HOST_REVOKED_OR_MISSING",
                "message": "Agent Host is unavailable",
            },
        )
    return host


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
        display_name=host.display_name,
        status=effective_agent_host_status(host.status, host.last_seen_at),
        protocol_version=host.protocol_version,
        host_release=host.host_release,
        capacity=host.capacity or {},
        last_seen_at=host.last_seen_at,
        revoked_at=host.revoked_at,
        created_at=host.created_at,
        updated_at=host.updated_at,
    )


def _harness_response(
    harness: AgentHostHarnessModel,
) -> AgentHostHarnessResponse:
    return AgentHostHarnessResponse(
        id=harness.id,
        host_id=harness.host_id,
        harness_key=harness.harness_key,
        display_name=harness.display_name,
        adapter_version=harness.adapter_version,
        upstream_version=harness.upstream_version,
        health=harness.health,
        capabilities=harness.capabilities or {},
        config_revision=harness.config_revision,
        config_options=harness.config_options or [],
        stale_after=harness.stale_after,
        stale_reason=harness.stale_reason,
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
        detail={"code": exc.code, "message": str(exc)},
    )


@router.post(
    "/me/runtime/agent-host-pairings",
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
    "/agent-host/pairings:complete",
    response_model=AgentHostPairingCompleted,
    operation_id="agent.host.pairing.complete",
)
async def complete_agent_host_pairing(
    request: AgentHostPairingComplete,
    uow: UoWDep,
) -> AgentHostPairingCompleted:
    """Consume a pairing code and issue the host secret, shown exactly once."""
    secret = generate_host_secret()
    try:
        host = await AgentHostRepository(uow).consume_pairing(
            code_hash=pairing_code_hash(request.pairing_code),
            host_secret_hash=host_secret_hash(secret),
            display_name=request.display_name,
            hello=request.hello,
        )
        await uow.commit()
        return AgentHostPairingCompleted(
            host_id=host.id,
            user_id=host.user_id,
            organization_id=host.organization_id,
            host_secret=secret,
        )
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc


@router.post(
    "/agent-host/poll",
    response_model=AgentHostPollResponse,
    operation_id="agent.host.poll",
)
async def poll_agent_host_commands(
    request: AgentHostPollRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostPollResponse:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _LONG_POLL_SECONDS

    # First pass: authenticate, record the heartbeat (write-on-change), and
    # apply the host's control updates.
    async with _uow_factory() as uow:
        host = await _authenticated_host(authorization=authorization, uow=uow)
        host = await AgentHostRepository(uow).mark_seen(
            host_id=host.id,
            hello=request.hello,
            capacity=request.capacity.model_dump(mode="json"),
        )
        negotiated_protocol = host.protocol_version or AGENT_HOST_PROTOCOL_VERSION
        host_status = AgentHostStatus(host.status)
        commands = await AgentHostDispatchRepository(uow).poll_commands(
            host_id=host.id,
            limit=_MAX_COMMANDS_PER_POLL,
            acknowledged_command_ids=request.acknowledged_command_ids,
            checkpoints=request.checkpoints,
            rejections=request.rejections,
            available_run_slots=request.capacity.available_runs,
        )
        await uow.commit()
    control_update_applied = bool(
        request.acknowledged_command_ids or request.checkpoints or request.rejections
    )
    if (
        commands
        or control_update_applied
        or request.capacity.available_runs == 0
        or host_status is AgentHostStatus.UPGRADE_REQUIRED
    ):
        return AgentHostPollResponse(
            protocol_version=negotiated_protocol,
            host_status=host_status,
            commands=commands,
            poll_after_ms=(
                _CONTROL_UPDATE_BACKOFF_MS if control_update_applied else 0
            ),
        )

    # Idle path: wait for a poke on the host's channel, falling back to a
    # slow re-query so a missed poke only delays delivery by a few seconds.
    channel_service = await get_channel_service()
    async with channel_service.subscribe([host_poke_channel(host.id)]) as pokes:
        poke = aiter(pokes)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return AgentHostPollResponse(
                    protocol_version=negotiated_protocol,
                    host_status=host_status,
                    commands=[],
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    anext(poke), timeout=min(_IDLE_REPOLL_SECONDS, remaining)
                )
            if loop.time() >= deadline:
                continue
            async with _uow_factory() as uow:
                commands = await AgentHostDispatchRepository(uow).poll_commands(
                    host_id=host.id,
                    limit=_MAX_COMMANDS_PER_POLL,
                    acknowledged_command_ids=[],
                    checkpoints=[],
                    rejections=[],
                    available_run_slots=request.capacity.available_runs,
                )
                await uow.commit()
            if commands:
                return AgentHostPollResponse(
                    protocol_version=negotiated_protocol,
                    host_status=host_status,
                    commands=commands,
                )


@router.post(
    "/agent-host/events:append",
    response_model=AgentHostEventAck,
    operation_id="agent.host.events.append",
)
async def append_agent_host_events(
    request: AgentHostEventBatch,
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostEventAck:
    host = await _authenticated_host(authorization=authorization, uow=uow)
    try:
        ack, stream_events = await AgentHostDispatchRepository(uow).append_events(
            host_id=host.id,
            batch=request,
        )
        await uow.commit()
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc
    for event in stream_events:
        await publish_run_stream_event(
            event.run_id,
            {
                "sequence": event.sequence,
                "type": event.type.value,
                "object_id": event.object_id,
                "payload": event.payload,
            },
        )
    return ack


@router.put(
    "/agent-host/harnesses",
    response_model=AgentHostHarnessPublishResponse,
    operation_id="agent.host.harnesses.publish",
)
async def publish_agent_host_harnesses(
    request: AgentHostHarnessPublishRequest,
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostHarnessPublishResponse:
    host = await _authenticated_host(authorization=authorization, uow=uow)
    repo = AgentHostRepository(uow)
    try:
        harnesses = [
            await repo.publish_harness(
                host_id=host.id,
                snapshot=snapshot,
            )
            for snapshot in request.harnesses
        ]
        await uow.commit()
        return AgentHostHarnessPublishResponse(
            items=[_harness_response(item) for item in harnesses]
        )
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc


@router.post(
    "/agent-host/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.host.self_revoke",
)
async def self_revoke_agent_host(
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Revoke the calling device before its local identity is removed."""

    host = await _authenticated_host(authorization=authorization, uow=uow)
    try:
        await AgentHostRepository(uow).revoke(
            host_id=host.id,
            user_id=host.user_id,
        )
        await uow.commit()
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc


@router.get(
    "/me/runtime/agent-hosts",
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
    "/me/runtime/agent-hosts/{host_id}/harnesses",
    response_model=AgentHostHarnessListResponse,
    operation_id="agent.host.harnesses.list",
)
async def list_agent_host_harnesses(
    host_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentHostHarnessListResponse:
    repo = AgentHostRepository(uow)
    if await repo.get_for_user(host_id=host_id, user_id=user.id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    harnesses = await repo.list_harnesses(host_id=host_id)
    return AgentHostHarnessListResponse(
        items=[_harness_response(item) for item in harnesses]
    )


@router.delete(
    "/me/runtime/agent-hosts/{host_id}",
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
