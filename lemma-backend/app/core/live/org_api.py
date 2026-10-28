"""``/organizations/{organization_id}/live``: one socket per (client,
organization), carrying the ``org`` topic.

The pod socket's transport, unchanged: the same handshake and session rules,
the same frames, queue and close codes, a socket slot per user per
organization (``LIVE_MAX_SOCKETS_PER_USER_PER_ORG``). What differs is what it
answers: one topic, ``org`` (``org_hub.py``), and no calls. The protocol is
``docs/modules/live.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from fastapi import APIRouter, WebSocket
from sqlalchemy.exc import SQLAlchemyError

from app.core.live.config import live_settings
from app.core.live.connection import OrgConnection
from app.core.live.limits import (
    claim_socket_slot,
    refresh_socket_slot,
    release_socket_slot,
)
from app.core.live.org_feeds import OrgAccess
from app.core.live.org_hubs import feeds_of, org_hubs
from app.core.live.protocol import (
    CLOSE_FORBIDDEN,
    CLOSE_TOO_MANY_SOCKETS,
    LiveUnavailable,
    ResyncReason,
    SubscribeRequest,
    ready_frame,
)
from app.core.live.sessions import SessionWatch, end_for_session
from app.core.live.socket import LiveDispatcher, accept_and_authenticate, serve
from app.core.live.topics import Call, Topic, live_registry
from app.core.log.log import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["live"])

ORG_TOPIC = "org"


class OrgTopic:
    """``{"op":"subscribe","id":"o1","topic":"org"}``: every covered pod's
    notices, over one subscription."""

    name = ORG_TOPIC

    async def subscribe(
        self, connection: OrgConnection, request: SubscribeRequest
    ) -> None:
        hub = org_hubs().hub(connection.organization_id)
        await hub.join(connection, request.id)
        if request.since is not None:
            # Notices are not a log: a resumed reader re-reads the summary.
            connection.offer_resync(request.id, None, ResyncReason.NO_REPLAY)


class _OrgHandlers:
    """What the organization socket answers: the ``org`` topic, no calls."""

    def __init__(self) -> None:
        self._topics: dict[str, Topic[OrgConnection]] = {ORG_TOPIC: OrgTopic()}
        self._calls: dict[str, Call[OrgConnection]] = {}

    @property
    def topics(self) -> Mapping[str, Topic[OrgConnection]]:
        return self._topics

    @property
    def calls(self) -> Mapping[str, Call[OrgConnection]]:
        return self._calls


_handlers = _OrgHandlers()


_NOT_IN_IT = "You are not in this organization"
_UNAVAILABLE = 1011


async def _access(organization_id: UUID, user_id: UUID) -> OrgAccess | None:
    """Whether the user is in the organization; ``None`` when that cannot be
    decided just now."""
    try:
        directory = feeds_of(live_registry()).directory
        return (await directory.pods_for(organization_id, user_id)).access
    except (LiveUnavailable, SQLAlchemyError, OSError) as exc:
        logger.warning(
            "live.org.access_unavailable.degraded",
            organization_id=str(organization_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None


@router.websocket("/organizations/{organization_id}/live")
async def org_live_ws(websocket: WebSocket, organization_id: UUID) -> None:
    session = await accept_and_authenticate(websocket)
    if session is None:
        return
    user_id = session.user_id
    access = await _access(organization_id, user_id)
    if access is None:
        await websocket.close(code=_UNAVAILABLE, reason="Could not start; retry.")
        return
    if access is not OrgAccess.MEMBER:
        await websocket.close(code=CLOSE_FORBIDDEN, reason=_NOT_IN_IT)
        return

    connection = OrgConnection(
        organization_id=organization_id,
        user_id=user_id,
        send_text=websocket.send_text,
        queue_bytes=live_settings.live_socket_queue_bytes,
    )
    limit = live_settings.live_max_sockets_per_user_per_org
    # Slots are counted per (id, user): an organization's id never names a pod.
    if not await claim_socket_slot(organization_id, user_id, connection.id, limit):
        await websocket.close(
            code=CLOSE_TOO_MANY_SOCKETS,
            reason=f"At most {limit} live sockets per user per organization.",
        )
        return

    watch = SessionWatch(session)

    async def keep() -> None:
        if not await watch.still_valid():
            end_for_session(connection)
            return
        # Taken out of the organization: the socket goes, as a pod's does when
        # its user is taken out of the pod. A check that cannot be made keeps
        # it until the next.
        now = await _access(organization_id, user_id)
        if now is not None and now is not OrgAccess.MEMBER:
            connection.close_with(CLOSE_FORBIDDEN, _NOT_IN_IT)
            return
        await refresh_socket_slot(organization_id, user_id, connection.id)

    try:
        connection.offer(
            ready_frame(connection.id, topics=sorted(_handlers.topics), calls=[])
        )
        dispatcher = LiveDispatcher(
            max_subscriptions=live_settings.live_max_subscriptions_per_org_socket,
            registry=_handlers,
        )
        await serve(
            websocket, connection, handle=dispatcher.handle, session=session, keep=keep
        )
    finally:
        await release_socket_slot(organization_id, user_id, connection.id)


__all__ = ["ORG_TOPIC", "OrgTopic", "router"]
