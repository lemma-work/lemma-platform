"""Agent Host pairing, identity, dispatch, and harness publication APIs.

Two audiences share this router. ``/me/runtime/*`` routes are called by a
signed-in user managing their machines. ``/agent-host/*`` routes are called by
the Agent Host itself, authenticated by the opaque per-installation secret
issued at pairing time.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated
from uuid import UUID, uuid7

from fastapi import APIRouter, Header, HTTPException, Request, status

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
    AgentHostCommand,
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
from app.modules.agent.infrastructure.agent_host_channels import host_poke_channel
from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_repository import (
    AgentHostRepository,
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
# Bounds how long a missed poke can delay delivery while idle. The poke is the
# fast path; this is the floor on correctness.
_IDLE_REPOLL_SECONDS = 5.0
_MAX_COMMANDS_PER_POLL = 16
# After a control update that actually changed something, ask the host back
# promptly rather than holding the connection open for a full long poll: it has
# more to tell us, or something to clear from its outbox. A repeated heartbeat
# is not such an update, or a busy host would never long-poll at all.
_CONTROL_UPDATE_BACKOFF_MS = 1_000


def _repository_error(exc: AgentHostRepositoryError) -> HTTPException:
    if isinstance(exc, AgentHostNotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, AgentHostPairingRejected):
        code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, AgentHostProtocolViolation):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(
        status_code=code,
        detail={"code": exc.code, "message": str(exc)},
    )


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
    host = await AgentHostRepository(uow).get_by_secret_hash(host_secret_hash(secret))
    if host is None or host.revoked_at is not None:
        # Deliberately identical for unknown and revoked secrets.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AGENT_HOST_REVOKED_OR_MISSING",
                "message": "Agent Host is unavailable",
            },
        )
    return host


def _host_response(host: AgentHostModel) -> AgentHostResponse:
    return AgentHostResponse(
        id=host.id,
        user_id=host.user_id,
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


def _harness_response(harness: AgentHostHarnessModel) -> AgentHostHarnessResponse:
    return AgentHostHarnessResponse.model_validate(harness)


@router.post(
    "/me/runtime/agent-host-pairings",
    response_model=AgentHostPairingCreated,
    operation_id="agent.host.pairing.create",
)
async def create_agent_host_pairing(
    request: AgentHostPairingCreate,
    user: CurrentUser,
    uow: UoWDep,
) -> AgentHostPairingCreated:
    """Mint a short-lived pairing code for a machine this user controls.

    A paired computer is the user's, not a workspace's: nothing here needs an
    organization. Sharing it happens later, by giving a runtime profile
    ORGANIZATION scope.
    """
    code = generate_pairing_code()
    pairing = await AgentHostRepository(uow).create_pairing(
        pairing_id=uuid7(),
        user_id=user.id,
        code_hash=pairing_code_hash(code),
        display_name=request.display_name,
    )
    await uow.commit()
    return AgentHostPairingCreated(
        pairing_id=pairing.id,
        pairing_code=code,
        expires_at=pairing.expires_at,
    )


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
    """Revoke a host, invalidating its secret immediately."""
    try:
        host = await AgentHostRepository(uow).revoke(host_id=host_id, user_id=user.id)
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc
    await uow.commit()
    return _host_response(host)


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
    repository = AgentHostRepository(uow)
    host = await repository.get_for_user(host_id=host_id, user_id=user.id)
    if host is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "AGENT_HOST_NOT_FOUND",
                "message": "Agent Host was not found",
            },
        )
    harnesses = await repository.list_harnesses(host_id=host.id)
    return AgentHostHarnessListResponse(
        items=[_harness_response(harness) for harness in harnesses]
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
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc
    await uow.commit()
    return AgentHostPairingCompleted(
        host_id=host.id,
        user_id=host.user_id,
        host_secret=secret,
    )


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
    """Replace this host's harness snapshots with the reported set."""
    host = await _authenticated_host(authorization=authorization, uow=uow)
    repository = AgentHostRepository(uow)
    try:
        published = [
            await repository.publish_harness(host_id=host.id, snapshot=snapshot)
            for snapshot in request.harnesses
        ]
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc
    await uow.commit()
    return AgentHostHarnessPublishResponse(
        items=[_harness_response(harness) for harness in published]
    )


@router.post(
    "/agent-host/revoke",
    response_model=AgentHostResponse,
    operation_id="agent.host.self_revoke",
)
async def self_revoke_agent_host(
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostResponse:
    """Let a host retire its own credential, e.g. on uninstall."""
    host = await _authenticated_host(authorization=authorization, uow=uow)
    try:
        revoked = await AgentHostRepository(uow).revoke(
            host_id=host.id, user_id=host.user_id
        )
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc
    await uow.commit()
    return _host_response(revoked)


@router.post(
    "/agent-host/poll",
    response_model=AgentHostPollResponse,
    operation_id="agent.host.poll",
)
async def poll_agent_host_commands(
    request: AgentHostPollRequest,
    http_request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentHostPollResponse:
    """Long-poll for commands, carrying the host's control updates up.

    This owns its own units of work rather than the request-scoped one: the
    idle wait below can hold the connection open for 25 seconds, and a
    transaction must not stay open across it.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _LONG_POLL_SECONDS

    # First pass: authenticate, record the heartbeat, and apply the host's
    # acknowledgements, checkpoints, and rejections.
    async with _uow_factory() as uow:
        host = await _authenticated_host(authorization=authorization, uow=uow)
        host = await AgentHostRepository(uow).mark_seen(
            host_id=host.id,
            hello=request.hello,
            capacity=request.capacity.model_dump(mode="json"),
        )
        negotiated_protocol = host.protocol_version or AGENT_HOST_PROTOCOL_VERSION
        host_status = AgentHostStatus(host.status)
        host_id = host.id
        try:
            commands = await AgentHostDispatchRepository(uow).poll_commands(
                host_id=host_id,
                limit=_MAX_COMMANDS_PER_POLL,
                acknowledged_command_ids=request.acknowledged_command_ids,
                checkpoints=request.checkpoints,
                rejections=request.rejections,
                available_run_slots=request.capacity.available_runs,
            )
        except AgentHostRepositoryError as exc:
            raise _repository_error(exc) from exc
        await uow.commit()

    # Only a control update that *changed* something is a reason to cut the
    # long poll short. A non-terminal checkpoint is the run's lease heartbeat,
    # which the host resends on every poll for as long as the run lives, so
    # treating "the host sent a checkpoint" as news meant a host was never once
    # allowed to long-poll while it had work — it round-tripped at 1Hz for the
    # whole run and rewrote a lease and a conversation-metadata row each time.
    if (
        commands
        or commands.progressed
        or host_status is AgentHostStatus.UPGRADE_REQUIRED
    ):
        return AgentHostPollResponse(
            protocol_version=negotiated_protocol,
            host_status=host_status,
            commands=commands,
            poll_after_ms=(_CONTROL_UPDATE_BACKOFF_MS if commands.progressed else 0),
        )

    return AgentHostPollResponse(
        protocol_version=negotiated_protocol,
        host_status=host_status,
        commands=await _await_commands(
            host_id=host_id,
            deadline=deadline,
            loop=loop,
            available_run_slots=request.capacity.available_runs,
            http_request=http_request,
        ),
    )


async def _await_commands(
    *,
    host_id: UUID,
    deadline: float,
    loop: asyncio.AbstractEventLoop,
    available_run_slots: int,
    http_request: Request,
) -> list[AgentHostCommand]:
    """Hold the poll until there is something to say, or the hold expires.

    Waits for a poke, falling back to a slow re-query so a missed poke only
    delays delivery by a few seconds rather than a whole hold.

    It also watches for the host going away. A host abandons a poll whenever it
    has something else to do, and nothing here used to notice — so each
    abandoned poll went on holding a task and a Redis subscription for the rest
    of its 25 seconds. One host streaming a single answer was measured stacking
    26 of them, against exactly one while idle. Noticing keeps that cost
    proportional to how many hosts there are rather than to how talkative their
    agents are.
    """
    channel_service = await get_channel_service()
    async with channel_service.subscribe([host_poke_channel(host_id)]) as pokes:
        poke = aiter(pokes)
        # The wait for a poke is a task that outlives one loop iteration, and is
        # deliberately never cancelled mid-flight. `asyncio.wait_for` cancels the
        # inner awaitable on timeout, and cancelling `anext()` closes the async
        # generator - so the *second* idle round would raise StopAsyncIteration
        # out of the handler and 500 the poll. Every host went OFFLINE five
        # seconds after connecting because of it.
        waiting: asyncio.Task[str | bytes] | None = None
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return []
                if waiting is None:
                    waiting = asyncio.ensure_future(anext(poke))
                done, _ = await asyncio.wait(
                    {waiting}, timeout=min(_IDLE_REPOLL_SECONDS, remaining)
                )
                if done:
                    # Consume the result so an exception is not swallowed, then
                    # re-arm on the next round. A closed subscription ends the
                    # idle wait: the host re-polls and subscribes afresh.
                    finished, waiting = waiting, None
                    try:
                        finished.result()
                    except StopAsyncIteration:
                        return []
                elif await http_request.is_disconnected():
                    # Nobody is left to answer. Checked only on a quiet round,
                    # so it costs nothing on the path that matters and cannot
                    # discard a poke already in hand: a command handed out here
                    # would be marked DELIVERED to a host that will never read
                    # it, and wait out its lease before anyone tried again.
                    return []
                if loop.time() >= deadline:
                    continue
                async with _uow_factory() as uow:
                    commands = await AgentHostDispatchRepository(uow).poll_commands(
                        host_id=host_id,
                        limit=_MAX_COMMANDS_PER_POLL,
                        acknowledged_command_ids=[],
                        checkpoints=[],
                        rejections=[],
                        available_run_slots=available_run_slots,
                    )
                    await uow.commit()
                if commands:
                    return list(commands)
        finally:
            # The response is on its way out; nothing will read the poke now.
            if waiting is not None:
                waiting.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await waiting


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
    """Append one ordered batch to the run's stream.

    There is no second lane to publish on: every event type travels the one
    ordered stream, and the ack watermark is the stream's last entry.
    """
    host = await _authenticated_host(authorization=authorization, uow=uow)
    try:
        ack = await AgentHostDispatchRepository(uow).append_events(
            host_id=host.id,
            batch=request,
        )
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc
    await uow.commit()
    return ack
