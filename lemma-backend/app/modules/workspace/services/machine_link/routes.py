"""Which API replica holds a connected machine's link, and how another reaches it.

A machine's link is one WebSocket, held by whichever replica its ``hello``
landed on. A request that needs the machine can arrive at any replica. Agent
Host ops cross replicas on Redis pub/sub, because an op is a small message;
a stream is not, and pumping bytes through pub/sub would put every preview
and every file read through Redis. So the holder records itself here and the
others connect to it directly, over the cluster network, and splice:

* ``machine-link:route:<connection_id>`` -- ``{"url", "session", "generation"}``,
  written when a link takes ownership and refreshed on every heartbeat, with a
  TTL of three heartbeats so a replica that died stops being named by itself;
* the holder's internal stream route,
  ``WS /agent-host/compute/streams/<connection_id>``, which only accepts a
  request signed with ``internal_signature`` -- an HMAC under the runtime
  credential key, bound to the connection and a minute-long timestamp window.

Losing the record is never wrong, only slower: a replica that finds no record
reports the machine not connected, and the next heartbeat writes it again.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import time
from dataclasses import dataclass
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings

logger = get_logger(__name__)

#: Where the holder answers. Under ``/agent-host``, which the session gate
#: exempts: the signature is the credential.
INTERNAL_STREAM_PATH = "/agent-host/compute/streams/{connection_id}"

#: Close code the holder uses when it does not hold the link after all.
PROXY_NOT_HELD = 4404
#: Close code the holder uses when the machine refused the stream.
PROXY_REFUSED = 4502

SIGNATURE_HEADER = "x-lemma-machine-link-signature"
TIMESTAMP_HEADER = "x-lemma-machine-link-timestamp"
#: How far a signed request's timestamp may be from this replica's clock.
SIGNATURE_WINDOW_SECONDS = 60

_RELEASE_IF_OURS = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


#: The most a control-path JSON value here may be: a route record, a stream
#: target, a reset. Bounded so decoding one on the loop is never the slow part.
SMALL_JSON_BYTES = 8 * 1024


def small_json(value: object) -> str:
    """Encode a control-path value: a few hundred bytes, safe on the loop."""
    return json.dumps(value, separators=(",", ":"))


def read_small_json(raw: str | bytes) -> object:
    """Decode a control-path value, refusing one too large to be one."""
    if len(raw) > SMALL_JSON_BYTES:
        raise ValueError("a control value is larger than any this link sends")
    return json.loads(raw)


def route_key(connection_id: UUID) -> str:
    return f"machine-link:route:{connection_id}"


def replica_url() -> str:
    """How the other replicas reach this one.

    ``MACHINE_LINK_REPLICA_URL`` when set; otherwise the pod's own address
    (``POD_IP``, from the downward API, else this host's resolved name) on the
    port the image serves on. Only ever dialled by other replicas, inside the
    cluster.
    """
    configured = workspace_settings.machine_link_replica_url
    if configured:
        return configured.rstrip("/")
    address = os.environ.get("POD_IP") or socket.gethostbyname(socket.gethostname())
    return f"http://{address}:{workspace_settings.machine_link_replica_port}"


def _signing_key() -> bytes:
    key = workspace_settings.runtime_credential_key
    if not key:
        raise RuntimeError("WORKSPACE_RUNTIME_CREDENTIAL_KEY is not configured")
    return hmac.new(
        key.encode(), b"machine-link-replica-proxy", hashlib.sha256
    ).digest()


def internal_signature(connection_id: UUID, timestamp: int) -> str:
    message = f"{connection_id}:{timestamp}".encode()
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()


def signed_headers(connection_id: UUID) -> dict[str, str]:
    timestamp = int(time.time())
    return {
        SIGNATURE_HEADER: internal_signature(connection_id, timestamp),
        TIMESTAMP_HEADER: str(timestamp),
    }


def verify_signature(
    connection_id: UUID, signature: str | None, timestamp: str | None
) -> bool:
    if not signature or not timestamp or not timestamp.isdigit():
        return False
    if abs(int(timestamp) - time.time()) > SIGNATURE_WINDOW_SECONDS:
        return False
    try:
        expected = internal_signature(connection_id, int(timestamp))
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True, slots=True)
class LinkRoute:
    url: str
    session: str
    generation: int


class LinkRoutes:
    """The Redis half: record, refresh, read and release a link's route."""

    def __init__(self, *, ttl_seconds: float) -> None:
        self._ttl_ms = max(1000, int(ttl_seconds * 1000))

    @staticmethod
    def _client():
        return get_redis(url=settings.redis_url)

    async def record(
        self, connection_id: UUID, *, session: UUID, generation: int
    ) -> None:
        value = small_json(
            {"url": replica_url(), "session": str(session), "generation": generation}
        )
        try:
            await self._client().set(route_key(connection_id), value, px=self._ttl_ms)
        except RedisError, OSError:
            logger.warning(
                "workspace.machine_link.route_record_skipped.degraded",
                connection_id=str(connection_id),
                exc_info=True,
            )

    async def read(self, connection_id: UUID) -> LinkRoute | None:
        try:
            raw = await self._client().get(route_key(connection_id))
        except RedisError, OSError:
            logger.warning(
                "workspace.machine_link.route_read_failed.degraded",
                connection_id=str(connection_id),
                exc_info=True,
            )
            return None
        if not raw:
            return None
        try:
            decoded = read_small_json(raw)
            return LinkRoute(
                url=str(decoded["url"]),
                session=str(decoded["session"]),
                generation=int(decoded["generation"]),
            )
        except ValueError, KeyError, TypeError:
            return None

    async def release(self, connection_id: UUID, *, session: UUID) -> None:
        """Forget the route, but only if it is still this session's: a newer
        link on another replica may already have written its own."""
        try:
            client = self._client()
            current = await client.get(route_key(connection_id))
            if not current:
                return
            try:
                recorded = read_small_json(current)
                if not isinstance(recorded, dict) or recorded.get("session") != str(
                    session
                ):
                    return
            except ValueError:
                pass
            await client.eval(_RELEASE_IF_OURS, 1, route_key(connection_id), current)
        except RedisError, OSError:
            logger.warning(
                "workspace.machine_link.route_release_skipped.degraded",
                connection_id=str(connection_id),
                exc_info=True,
            )


def notice_channel(connection_id: UUID) -> str:
    """The machine's own notice channel: ``superseded`` and ``revoked``, read
    by ``agent_host.channels.parse_host_notice`` as a host's are."""
    return f"machine-link:{connection_id}:notice"


async def notify_revoked(connection_id: UUID) -> None:
    """Close a revoked machine's live link wherever it is held; never raises."""
    from app.core.infrastructure.channels.channel_service import get_channel_service

    try:
        service = await get_channel_service()
        await service.publish(notice_channel(connection_id), {"type": "revoked"})
    except RedisError, RuntimeError, OSError:
        logger.warning(
            "workspace.machine_link.revoke_notice_skipped.degraded",
            connection_id=str(connection_id),
            exc_info=True,
        )
