"""Shared Redis clients.

Every component used to build its own client with ``Redis.from_url``. Each of
those carries its own connection pool, and redis-py's default
``max_connections`` is 2**31 — effectively unbounded — so a process with twenty
independent clients had twenty unbounded pools and no single place to bound,
tune, or shut them down. Only two sites set a limit; only two enabled
``health_check_interval``, without which a connection the server dropped while
idle surfaces as an error in the middle of the next command.

Callers now share one pool per (url, decode_responses) pair, with consistent
tuning and one teardown.

``decode_responses`` is part of the key rather than a caller-side detail
because it changes the type of every reply: FastStream and streaq require raw
bytes, while application code wants ``str``. The two cannot share a pool.

This factory is synchronous because ``Redis.from_url`` performs no I/O — it
builds a pool whose connections are opened lazily on first command. Keeping it
sync means components can keep constructing their client in ``__init__``
instead of growing an async lazy-init path just to obtain one.

Blocking reads (``XREAD BLOCK``) and pub/sub each hold a connection for their
whole duration. That is why the pool is bounded by ``redis_max_connections``
rather than left to grow: a bounded pool applies backpressure, an unbounded one
exhausts the server's connection limit instead.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings


# Keyed by (url, decode_responses); see the module docstring for why the flag
# has to participate in identity.
_clients: dict[tuple[str, bool], Redis] = {}


def get_redis(
    *,
    decode_responses: bool = True,
    url: str | None = None,
) -> Redis:
    """Return the shared client for these settings, creating it on first use."""
    key = (url or settings.redis_url, decode_responses)
    client = _clients.get(key)
    if client is None:
        client = Redis.from_url(
            key[0],
            decode_responses=key[1],
            max_connections=settings.redis_max_connections,
            # Liveness-check a pooled connection on checkout so a server-side
            # idle drop is replaced transparently rather than failing the next
            # command mid-flight.
            health_check_interval=30,
            socket_keepalive=True,
        )
        _clients[key] = client
    return client


async def close_redis_clients() -> None:
    """Close every shared client. Safe to call more than once."""
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - teardown must not mask a real error
            pass
