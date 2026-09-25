"""Refusals for the HTTP routes Agent Host protocol 2 used to call.

Protocol 3 moved everything a paired machine says onto the link WebSocket, and
the HTTP routes went with it. But Desktop has no auto-updater, so a machine
paired to a hosted workspace keeps running the host it shipped with, and that
host never opens the socket: it cannot be sent close code 4426. Left to 404s it
would sit "offline" forever with nothing telling anyone why.

So each old path answers with the one refusal the old host already understands,
and serves nothing else. What the old host does with each (read against
``desktop/agent-host/src`` as of protocol 2, on main before the link merged):

``POST /agent-host/poll``
    200 with a well-formed poll response naming protocol 3. ``TargetClient::poll``
    (``api.rs``) checks ``protocol_version != PROTOCOL_VERSION`` right after
    decoding and returns ``ApiError::Protocol(3)``. That is not a request
    rejection, so the worker (``runtime/worker.rs``) records the target OFFLINE
    with "target requested Agent Host protocol 3 is unsupported" as its last
    error and backs off to one attempt every 30 seconds. It keeps its pairing,
    so the updated app reconnects over the link with the same secret and no
    re-pair. The host's row is also marked UPGRADE_REQUIRED, which the
    workspace shows as "Needs updating".

    Every alternative is worse. A non-2xx is a request rejection, and the
    worker bisects its control batch around one -- up to 12 extra requests per
    cycle. A 200 saying ``UPGRADE_REQUIRED`` under protocol 2 first marks the
    target ONLINE and then ends the worker, which the supervisor restarts every
    scan. And ``401 AGENT_HOST_REVOKED_OR_MISSING`` makes the host drop its
    pairing after three refusals, so updating the app would not be enough:
    the person would have to pair the machine again.

Everything else
    410 Gone. Each is a request rejection to the old host, which stops rather
    than retries: an event batch is replayed once and then dropped (the run it
    belongs to is lost either way -- no protocol-2 host can hold a lease any
    more), a harness publish waits for the poll loop's next turn, and the MCP
    bridge fails the one tool call with a non-retryable error. Pairing and
    self-revocation surface the body's message to the person who asked.

Removable once no protocol-2 host can still be installed: see the removal note
in docs/architecture/agent-host.md#retired-http-routes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from app.core.api.dependencies import UoWDep
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostStatus,
)
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.agent.services.agent_host_auth import host_secret_hash
from app.modules.agent.services.agent_host_link_wire import bearer_secret


logger = get_logger(__name__)

router = APIRouter(tags=["agent_host"], include_in_schema=False)

#: The old host reads ``detail.code`` out of error bodies; this is the one it
#: is told when a route of its protocol has been retired.
LEGACY_REFUSAL_CODE = "AGENT_HOST_UPGRADE_REQUIRED"
LEGACY_REFUSAL_MESSAGE = (
    "This version of Lemma Desktop can no longer connect to this workspace. "
    "Update Lemma Desktop to reconnect this computer."
)
#: How long the poll answer asks the host to wait. The old host never reads it
#: -- it fails the poll on the protocol version first -- but a poll response
#: must carry one, and zero would read as "ask again at once".
LEGACY_POLL_AFTER_MS = 30_000


def legacy_poll_answer() -> dict[str, object]:
    """The poll response a protocol-2 host decodes, and then refuses."""
    return {
        "protocol_version": AGENT_HOST_PROTOCOL_VERSION,
        "host_status": AgentHostStatus.UPGRADE_REQUIRED.value,
        "commands": [],
        "poll_after_ms": LEGACY_POLL_AFTER_MS,
    }


def legacy_refusal() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "detail": {"code": LEGACY_REFUSAL_CODE, "message": LEGACY_REFUSAL_MESSAGE}
        },
    )


@router.post("/agent-host/poll")
async def refuse_legacy_poll(
    uow: UoWDep,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Tell a protocol-2 host its protocol is gone, and show it needs updating."""
    secret = bearer_secret({"authorization": authorization or ""})
    if secret is not None:
        # Changes the row only the first time, which is also what keeps this
        # to one log line per host rather than one per 30-second retry.
        host_id = await AgentHostRepository(uow).mark_upgrade_required(
            host_secret_hash(secret)
        )
        await uow.commit()
        if host_id is not None:
            logger.info(
                "agent.agent_host_legacy.upgrade_required", host_id=str(host_id)
            )
    return JSONResponse(content=legacy_poll_answer())


@router.post("/agent-host/pairings/complete")
@router.post("/agent-host/pairings:complete")
async def refuse_legacy_pairing() -> JSONResponse:
    """Refuse to pair a protocol-2 host; the person pairing it sees why."""
    logger.info("agent.agent_host_legacy.pairing_refused")
    return legacy_refusal()


@router.post("/agent-host/events/append")
@router.post("/agent-host/events:append")
@router.put("/agent-host/harnesses")
@router.post("/agent-host/revoke")
async def refuse_legacy_host_request() -> JSONResponse:
    """Refuse what a protocol-2 host sends besides its poll."""
    return legacy_refusal()


@router.api_route(
    "/agent-runtime/conversations/{legacy_path:path}",
    methods=["GET", "POST", "DELETE"],
)
async def refuse_legacy_conversation_mcp(legacy_path: str) -> JSONResponse:
    """Refuse the conversation MCP mount a protocol-2 host's bridge called."""
    del legacy_path
    return legacy_refusal()
