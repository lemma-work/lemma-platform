"""Agent Host management APIs for a signed-in user: pair, list, revoke.

These are the ``/me/runtime/*`` routes a person calls to manage their machines.
The machine itself has no HTTP API: everything it says and hears travels on its
link WebSocket, in ``agent_host_link_controller``.
"""

from __future__ import annotations

from uuid import UUID, uuid7

from fastapi import APIRouter, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent.api.agent_host_schemas import (
    AgentHostHarnessListResponse,
    AgentHostHarnessResponse,
    AgentHostListResponse,
    AgentHostResponse,
)
from app.modules.agent.domain.agent_host import (
    AgentHostPairingCreate,
    AgentHostPairingCreated,
    effective_agent_host_status,
)
from app.modules.agent.infrastructure.agent_host.channels import (
    notify_host_revoked,
)
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.agent_host.repository_common import (
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
    generate_pairing_code,
    pairing_code_hash,
)


router = APIRouter(tags=["agent_host"])


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
    """Revoke a host, invalidating its secret immediately.

    The secret stops authenticating the moment this commits, but a link opened
    with it before then is already past authentication. The notice closes it,
    on whichever replica holds it, instead of leaving it working until the host
    happens to reconnect.
    """
    try:
        host = await AgentHostRepository(uow).revoke(host_id=host_id, user_id=user.id)
    except AgentHostRepositoryError as exc:
        raise _repository_error(exc) from exc
    await uow.commit()
    async with connection_released(uow.session):
        await notify_host_revoked(host.id)
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
