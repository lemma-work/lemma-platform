"""A connected machine's routes beside its link, and the replicas' stream proxy.

See docs/architecture/connected-machines.md. Under ``/agent-host/``, which the
session gate exempts, and outside the OpenAPI schema like the link itself: no
client but ``lemma machine connect`` and its agent calls them.

* ``POST /agent-host/compute/pairings`` -- a machine with no credential asks for
  a code to show its person. It sends the hash of a claim key it keeps.
* ``GET /agent-host/compute/runtime[/archive|/installer]`` -- under the
  machine's secret: the runtime bundle's description and its hashed
  requirements, the archive, and the installer. A host-mode machine builds its
  environment and installs its first runtime from these; later bundles are
  pushed down its link.
* ``WS /agent-host/compute/streams/{connection_id}`` -- the replica holding a
  machine's link splices a stream for another replica. Signed with the
  runtime credential key (``machine_link.routes``); no person or machine
  calls it.
"""

from __future__ import annotations

import asyncio
import contextlib
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent.contracts.compute_link import MachineMode
from app.modules.agent.contracts.compute_link import bearer_secret
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine_connection import MachineConnection
from app.modules.workspace.infrastructure.runtime_bundle import (
    RuntimeBundle,
    runtime_bundle,
)
from app.modules.workspace.services.machine_link.routes import (
    INTERNAL_STREAM_PATH,
    PROXY_NOT_HELD,
    PROXY_REFUSED,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    read_small_json,
    small_json,
    verify_signature,
)
from sandbox_runtime import runtime_install
from sandbox_runtime.machine_link.mux import MAX_DATA_BYTES, StreamReset

logger = get_logger(__name__)

router = APIRouter(tags=["agent_host"], include_in_schema=False)


@lru_cache(maxsize=1)
def _service():
    """Built on first use, so serving the API does not load the link's code."""
    from app.modules.workspace.services.machine_connection_service import (
        MachineConnectionService,
    )

    return MachineConnectionService()


@lru_cache(maxsize=1)
def _store():
    from app.modules.workspace.services.machine_link.store import MachineLinkStore

    return MachineLinkStore()


#: The installer is uploaded on its own (it ships in neither the overlay nor
#: an image's floor); a machine fetches it from here. Found by its module, so
#: the path is right in a checkout and in the image alike.
_INSTALLER = Path(runtime_install.__file__)


def _enabled() -> None:
    if not workspace_settings.machine_connections_enabled:
        raise HTTPException(404, detail="Connected machines are not enabled here.")


class PairingRequest(BaseModel):
    claim_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_name: str = Field(min_length=1, max_length=255)
    mode: MachineMode
    platform: str = Field(min_length=1, max_length=32)


class PairingCode(BaseModel):
    code: str
    expires_at: str
    #: Where the person approves it, when this server knows its frontend.
    approve_url: str | None = None


def _approve_url(code: str) -> str | None:
    if not settings.frontend_url:
        return None
    return f"{settings.frontend_url.rstrip('/')}/machines/connect?code={code}"


@router.post("/agent-host/compute/pairings", status_code=201)
async def request_pairing_code(body: PairingRequest) -> PairingCode:
    _enabled()
    code, pending = await _service().request_code(
        claim_key_sha256=body.claim_key_sha256,
        display_name=body.display_name,
        mode=body.mode.value,
        platform=body.platform,
    )
    return PairingCode(
        code=code,
        expires_at=pending.expires_at.isoformat(),
        approve_url=_approve_url(code),
    )


async def _machine(authorization: str | None) -> MachineConnection:
    _enabled()
    secret = bearer_secret({"authorization": authorization or ""})
    machine = await _store().machine_by_secret(secret) if secret else None
    if machine is None:
        raise HTTPException(401, detail="AGENT_HOST_REVOKED_OR_MISSING")
    return machine


def _bundle() -> RuntimeBundle:
    bundle = runtime_bundle()
    if bundle is None:
        raise HTTPException(
            503, detail="This server has no runtime bundle to give a machine."
        )
    return bundle


class RuntimeDescription(BaseModel):
    version: str
    archive_sha256: str
    size: int
    requires: list[str]
    #: The hashed, locked third-party closure; None from an older build.
    requirements: str | None
    #: The image this server runs its own machines from, which image mode runs.
    workspace_image: str | None


@router.get("/agent-host/compute/runtime")
async def describe_runtime(
    authorization: Annotated[str | None, Header()] = None,
) -> RuntimeDescription:
    await _machine(authorization)
    bundle = _bundle()
    return RuntimeDescription(
        version=bundle.version,
        archive_sha256=bundle.archive_sha256,
        size=bundle.size_bytes,
        requires=list(bundle.requires),
        requirements=bundle.requirements,
        workspace_image=workspace_settings.workspace_image or None,
    )


@router.get("/agent-host/compute/runtime/archive")
async def runtime_archive(
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    await _machine(authorization)
    bundle = _bundle()
    return Response(
        bundle.archive,
        media_type="application/zip",
        headers={"x-lemma-runtime-version": bundle.version},
    )


@router.get("/agent-host/compute/runtime/installer")
async def runtime_installer(
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    await _machine(authorization)
    return Response(_INSTALLER.read_bytes(), media_type="text/x-python")


# ------------------------------------------------------------ replica proxy


@router.websocket(INTERNAL_STREAM_PATH)
async def proxy_machine_stream(websocket: WebSocket, connection_id: UUID) -> None:
    """Splice a stream on a link this replica holds to another replica."""
    if not verify_signature(
        connection_id,
        websocket.headers.get(SIGNATURE_HEADER),
        websocket.headers.get(TIMESTAMP_HEADER),
    ):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    from app.modules.workspace.services.machine_link.dialer import (
        machine_link_registry,
    )

    session = machine_link_registry.get(connection_id)
    if session is None:
        await websocket.close(code=PROXY_NOT_HELD, reason="not held here")
        return
    try:
        target = read_small_json(websocket.query_params.get("target") or "{}")
        if not isinstance(target, dict):
            raise ValueError("target")
        stream = await session.open_stream(target)
    except ValueError, ValidationError:
        await websocket.close(code=4400, reason="bad target")
        return
    except StreamReset as reset:
        await websocket.close(
            code=PROXY_REFUSED,
            reason=small_json({"code": reset.code, "message": reset.message[:80]}),
        )
        return
    await websocket.send_text('{"accept":true}')
    await _splice(websocket, stream)


async def _splice(websocket: WebSocket, stream) -> None:
    async def inbound() -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            data = message.get("bytes")
            if isinstance(data, bytes):
                await stream.write(data)
            elif message.get("text") == '{"fin":true}':
                await stream.write_eof()
                return

    async def outbound() -> None:
        while chunk := await stream.read(MAX_DATA_BYTES):
            await websocket.send_bytes(chunk)
        await websocket.send_text('{"fin":true}')

    tasks = [asyncio.ensure_future(inbound()), asyncio.ensure_future(outbound())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        failed = next(
            (
                task.exception()
                for task in done
                if not task.cancelled() and task.exception() is not None
            ),
            None,
        )
        if failed is None:
            await asyncio.gather(*tasks)
        elif isinstance(failed, StreamReset):
            await websocket.send_text(
                small_json({"reset": {"code": failed.code, "message": failed.message}})
            )
    except (WebSocketDisconnect, StreamReset, RuntimeError) as ended:
        # Either end going away is how a proxied stream ends; nothing to retry.
        logger.info(
            "workspace.machine_link.proxy_stream_ended", reason=type(ended).__name__
        )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stream.aclose()
        with contextlib.suppress(RuntimeError):
            # Already closed by the other replica.
            await websocket.close()
