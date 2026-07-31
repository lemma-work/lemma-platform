"""Agent Host pairing, identity, and harness publication APIs.

Two audiences share this router. ``/me/runtime/*`` routes are called by a
signed-in user managing their machines. ``/agent-host/*`` routes are called by
the Agent Host itself, authenticated by the opaque per-installation secret
issued at pairing time.

Run dispatch routes (poll, events) are added alongside the dispatch tables.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid7

from fastapi import APIRouter, Header, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.api.agent_host_schemas import (
    AgentHostHarnessListResponse,
    AgentHostHarnessPublishRequest,
    AgentHostHarnessPublishResponse,
    AgentHostHarnessResponse,
    AgentHostListResponse,
    AgentHostResponse,
)
from app.modules.agent.domain.agent_host import (
    AgentHostPairingComplete,
    AgentHostPairingCompleted,
    AgentHostPairingCreate,
    AgentHostPairingCreated,
    effective_agent_host_status,
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
    """Mint a short-lived pairing code for a machine this user controls."""
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
        organization_id=host.organization_id,
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
