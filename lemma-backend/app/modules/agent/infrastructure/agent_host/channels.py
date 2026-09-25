"""The per-host notice channel every live Agent Host link subscribes to.

A host's link subscribes to this one channel once, for as long as the socket is
open, and three kinds of notice travel on it:

``poke``
    A command was queued for this host. The link that holds its socket re-reads
    the queue and pushes what it finds. A pure latency optimization: the link
    also re-reads every 5 seconds, so a lost poke costs a few seconds and never
    correctness. That is why publishing is best effort and never raises.
``superseded``
    A newer link for this host said ``hello``. It carries the link generation
    that ``hello`` claimed in the database, and every link holding a smaller
    one closes with 4409, so commands go out on one socket at a time even when
    a network drop left a half-open one behind on another replica. The
    generation, not the connection id, is what decides: two handshakes racing
    each hear the other's notice, and "any other connection id is newer" closed
    both of them.
``revoked``
    The user revoked the host. Its secret is already dead in the database; this
    closes the socket that authenticated with it before the revocation, which
    would otherwise stay open until the host next reconnected.

Redis pub/sub, so any API replica can reach the one holding the socket without a
routing table. Run events do not travel here: they go to the run's Redis Stream,
which is ordered and replayable, and this channel is neither.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.log.log import get_logger


logger = get_logger(__name__)

POKE = "poke"
SUPERSEDED = "superseded"
REVOKED = "revoked"


def host_poke_channel(host_id: UUID) -> str:
    return f"agent-host:host:{host_id}:poke"


@dataclass(frozen=True, slots=True)
class HostNotice:
    kind: str
    connection_id: str | None = None
    #: The link generation a ``superseded`` notice announces. None for a notice
    #: that carries none, which supersedes nothing.
    generation: int | None = None


def parse_host_notice(raw: str | bytes) -> HostNotice:
    """Read one message off the channel; anything unrecognised reads as a poke.

    A poke is the harmless default: the worst it does is re-read the queue.
    Treating an unreadable message as ``superseded`` would close a healthy link.
    """
    try:
        decoded = json.loads(raw)
    except ValueError:
        return HostNotice(POKE)
    if not isinstance(decoded, dict):
        return HostNotice(POKE)
    kind = decoded.get("type")
    connection_id = decoded.get("connection_id")
    generation = decoded.get("generation")
    return HostNotice(
        kind if kind in {POKE, SUPERSEDED, REVOKED} else POKE,
        connection_id if isinstance(connection_id, str) else None,
        # bool is an int; a JSON true is not a generation.
        generation
        if isinstance(generation, int) and not isinstance(generation, bool)
        else None,
    )


def superseded_notice(connection_id: UUID, generation: int) -> dict[str, object]:
    """What a new link publishes so every older link for its host closes."""
    return {
        "type": SUPERSEDED,
        "connection_id": str(connection_id),
        "generation": generation,
    }


async def _publish(host_id: UUID, notice: dict[str, str]) -> None:
    try:
        service = await get_channel_service()
        await service.publish(host_poke_channel(host_id), notice)
    except RedisError, RuntimeError, OSError:
        logger.warning(
            "agent.infrastructure.agent_host_channels.poke_skipped.degraded",
            host_id=str(host_id),
            exc_info=True,
        )


async def poke_host(host_id: UUID) -> None:
    """Best-effort wake-up for the host's link; never raises."""
    await _publish(host_id, {"type": POKE})


async def notify_host_revoked(host_id: UUID) -> None:
    """Close any live link for a host that was just revoked; never raises."""
    await _publish(host_id, {"type": REVOKED})
