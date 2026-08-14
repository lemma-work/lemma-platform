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
whole duration, so the pool is bounded by ``redis_max_connections`` rather than
left to grow: an unbounded pool exhausts the server's connection limit instead.
Bounding alone is not backpressure, though. redis-py's plain ``ConnectionPool``
raises ``ConnectionError("Too many connections")`` the instant the ceiling is
reached, which converts a brief burst into failed commands. Only
``BlockingConnectionPool`` waits for a connection to come back, so that is what
these clients use: a caller queues for up to ``_POOL_WAIT_SECONDS`` and only
then fails.

Connect timeouts are set here for everyone, and so are read timeouts — but the
read timeout used to be opt-in, and exactly one of the forty-odd call sites
opted in. Everything else, including every cache on the request path, would wait
for TCP keepalive to give up if Redis accepted the connection and then stopped
answering. That is tens of minutes, and any lock, transaction or pooled database
connection the caller is holding waits with it.

The reason it was opt-in is real: redis-py applies ``socket_timeout`` to *every*
read, including the indefinite one Pub/Sub's ``listen()`` performs, so a global
value would tear down and resubscribe the realtime multiplexer on every idle
interval. But that makes the safe choice the one you have to remember, which is
how thirty-nine call sites ended up unbounded.

So the default is inverted. Callers get a read timeout unless they declare
themselves ``blocking=True``, which is true of exactly four things: Pub/Sub
listeners, the streaq/FastStream stream readers, and the realtime multiplexer.
Those hold a connection for their whole duration by design and must not be
interrupted; everything else is a command that should answer promptly or fail.
"""

from __future__ import annotations

from redis.asyncio import BlockingConnectionPool, Redis

from app.core.config import settings


# How long a caller waits for a pooled connection before failing. This is the
# backpressure window: long enough to ride out a burst, short enough that a
# genuinely exhausted pool surfaces as an error instead of a hang.
_POOL_WAIT_SECONDS = 20.0
_CONNECT_TIMEOUT_SECONDS = 5.0

# Keyed by (url, decode_responses, socket_timeout); see the module docstring for
# why the flag and the timeout have to participate in identity.
_clients: dict[tuple[str, bool, float | None], Redis] = {}


def get_redis(
    *,
    decode_responses: bool = True,
    url: str | None = None,
    socket_timeout: float | None = None,
    blocking: bool = False,
) -> Redis:
    """Return the shared client for these settings, creating it on first use.

    ``blocking=True`` is for callers that hold a connection open waiting for the
    server to say something — Pub/Sub ``listen()``, ``XREAD BLOCK``. They get no
    read timeout, because for them a silent connection is the normal state.
    Everything else gets ``redis_read_timeout_seconds``.
    """
    if socket_timeout is None and not blocking:
        socket_timeout = settings.redis_read_timeout_seconds or None
    key = (url or settings.redis_url, decode_responses, socket_timeout)
    client = _clients.get(key)
    if client is None:
        pool = BlockingConnectionPool.from_url(
            key[0],
            decode_responses=key[1],
            max_connections=settings.redis_max_connections,
            timeout=_POOL_WAIT_SECONDS,
            socket_timeout=key[2],
            socket_connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            # Liveness-check a pooled connection on checkout so a server-side
            # idle drop is replaced transparently rather than failing the next
            # command mid-flight.
            health_check_interval=30,
            socket_keepalive=True,
        )
        # from_pool, not Redis(connection_pool=...): only the former hands the
        # client ownership of the pool, so close_redis_clients() below actually
        # disconnects it instead of dropping a live pool on the floor.
        client = Redis.from_pool(pool)
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
