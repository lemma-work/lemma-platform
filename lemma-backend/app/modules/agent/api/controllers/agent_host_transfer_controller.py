"""The Agent Host's bulk bytes, over HTTP beside the link.

See ``domain/agent_host_transfer``: the link names a transfer or a blob, and
the bytes come and go here. A paired machine is the only caller. It
authenticates exactly as its link does -- the host secret as a bearer token --
and the global session gate already exempts ``/agent-host/`` for that reason.

Not in the OpenAPI schema, like the link itself: no client but the Agent Host
calls these, and the SDKs have nothing to offer for them.

Answers, which the host maps to an op failure:

* 401 -- the credential is missing, unknown or revoked (one answer for all, as
  the link closes with one code for all).
* 404 -- no transfer this host may use: unknown, expired, another host's, or
  for a run this host does not hold. Indistinguishable on purpose.
* 409 -- already used. A transfer moves once.
* 413 -- more bytes than the transfer or blob takes.
* 422 -- the body does not match the digest it was sent with.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host_transfer import (
    BLOB_MAX_BYTES,
    BLOB_PATH,
    DIGEST_HEADER,
    TRANSFER_PATH,
    is_sha256_hex,
)
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.agent_host.transfers import (
    AgentHostTransferStore,
    TransferRefusal,
    TransferRefused,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel
from app.modules.agent.services.agent_host_auth import host_secret_hash
from app.modules.agent.services.agent_host_link_wire import bearer_secret


logger = get_logger(__name__)

router = APIRouter(tags=["agent_host"], include_in_schema=False)

_store = AgentHostTransferStore()
_uow_factory = SessionUnitOfWorkFactory(async_session_maker)

_STATUS = {
    TransferRefusal.NOT_FOUND: 404,
    TransferRefusal.ALREADY_USED: 409,
    TransferRefusal.TOO_LARGE: 413,
    TransferRefusal.DIGEST_MISMATCH: 422,
}

_UNAUTHORIZED_CODE = "AGENT_HOST_REVOKED_OR_MISSING"


def _refusal(
    refused: TransferRefused, *, host_id: UUID | None, route: str
) -> JSONResponse:
    logger.info(
        "agent.agent_host_transfer.refused",
        host_id=str(host_id) if host_id else None,
        route=route,
        refusal=refused.refusal.value,
    )
    return JSONResponse(
        status_code=_STATUS[refused.refusal],
        content={
            "detail": {
                "code": refused.refusal.value.upper(),
                "message": refused.message,
            }
        },
    )


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "detail": {
                "code": _UNAUTHORIZED_CODE,
                "message": "this computer is not paired with Lemma",
            }
        },
    )


async def _authenticated_host(
    authorization: str | None, *, holding_run: UUID | None = None
) -> UUID | None:
    """The host the bearer secret belongs to, while it is still paired.

    One short transaction, closed before any body is read: a slow upload must
    not hold a database connection for as long as it takes. With
    ``holding_run``, also ``TransferRefused`` unless this host holds that run.
    """
    secret = bearer_secret({"authorization": authorization or ""})
    if secret is None:
        return None
    async with _uow_factory() as uow:
        host = await AgentHostRepository(uow).get_by_secret_hash(
            host_secret_hash(secret)
        )
        if host is None or host.revoked_at is not None:
            return None
        if holding_run is not None:
            lease = await uow.session.get(AgentHostRunLeaseModel, holding_run)
            if lease is None or lease.host_id != host.id:
                raise TransferRefused(
                    TransferRefusal.NOT_FOUND, "no such run on this host"
                )
        return host.id


async def _body(request: Request, limit: int) -> bytes:
    """The request body, refused as soon as it passes ``limit``."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise TransferRefused(TransferRefusal.TOO_LARGE, f"at most {limit} bytes")
    body = bytearray()
    async for piece in request.stream():
        body.extend(piece)
        if len(body) > limit:
            raise TransferRefused(TransferRefusal.TOO_LARGE, f"at most {limit} bytes")
    return bytes(body)


def _digest(value: str | None) -> str:
    digest = (value or "").strip().lower()
    if not is_sha256_hex(digest):
        raise TransferRefused(
            TransferRefusal.DIGEST_MISMATCH,
            f"{DIGEST_HEADER} must be the body's SHA-256, as lowercase hex",
        )
    return digest


@router.get(TRANSFER_PATH)
async def fetch_transfer(
    transfer_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Bytes Lemma staged for this host's ``op``, once."""
    host_id = await _authenticated_host(authorization)
    if host_id is None:
        return _unauthorized()
    try:
        data, sha256 = await _store.fetch_by_host(transfer_id, host_id=host_id)
    except TransferRefused as refused:
        return _refusal(refused, host_id=host_id, route="fetch")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={DIGEST_HEADER: sha256},
    )


@router.put(TRANSFER_PATH)
async def deliver_transfer(
    transfer_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Bytes this host read for an ``op``, into the slot the op named, once."""
    host_id = await _authenticated_host(authorization)
    if host_id is None:
        return _unauthorized()
    try:
        sha256 = _digest(request.headers.get(DIGEST_HEADER))
        limit = await _store.max_bytes_for(transfer_id, host_id=host_id)
        data = await _body(request, limit)
        await _store.deliver_by_host(
            transfer_id, host_id=host_id, data=data, sha256=sha256
        )
    except TransferRefused as refused:
        return _refusal(refused, host_id=host_id, route="deliver")
    return Response(status_code=204)


@router.put(BLOB_PATH)
async def deliver_blob(
    run_id: UUID,
    sha256: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """A run event's attachment, before the event that names it."""
    try:
        host_id = await _authenticated_host(authorization, holding_run=run_id)
    except TransferRefused as refused:
        return _refusal(refused, host_id=None, route="blob")
    if host_id is None:
        return _unauthorized()
    try:
        digest = _digest(sha256)
        data = await _body(request, BLOB_MAX_BYTES)
        await _store.put_blob(run_id=run_id, sha256=digest, data=data)
    except TransferRefused as refused:
        return _refusal(refused, host_id=host_id, route="blob")
    return Response(status_code=204)
