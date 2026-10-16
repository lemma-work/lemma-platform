import asyncio
import json
from datetime import datetime, date
from itertools import count
from uuid import UUID
from sqlalchemy import event, text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.infrastructure.db.connect_errors import translate_connect_errors
from app.core.config import settings
from app.core.log.log import get_logger
from app.core.observability.connection_scope import attach_connection_scope_monitor
from app.core.observability.memory_sampler import watch_compiled_cache
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
# Counts failures of the pool-utilization probe below, so a broken diagnostic is
# reported once rather than on every checkout. A counter rather than a flag
# because checkout runs on whichever thread borrowed the connection, and a
# read-then-set flag lets two of them both see the unset value and both warn.
# `itertools.count.__next__` is a single C call, so incrementing it cannot be
# interleaved -- the same reason the stdlib's own `threading` module numbers
# threads with `_count().__next__` instead of taking a lock. A lock would also
# work, but this handler is on the checkout path and must not add contention
# there to buy one log line.
_pool_probe_failures = count()
_pool_pressure_incident = DependencyIncident(
    "database_pool_capacity",
    logger=logger,
    degradation_threshold=3,
)

engine = None
_async_session_maker = None


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


#: Every time this code writes is UTC. Pinned per connection so that a naive
#: value cast to ``timestamp with time zone`` is read as UTC whatever the
#: server's or role's default zone is, rather than as that zone.
SESSION_ZONE = {"timezone": "UTC"}


def _build_connect_args() -> dict:
    """Build asyncpg connect_args with server-side session settings.

    asyncpg's ``server_settings`` dict is sent as ``SET <key> = <value>`` on
    each new connection. This is the asyncpg-native way to set
    ``idle_in_transaction_session_timeout`` — using a SQLAlchemy ``connect``
    event listener doesn't work because the event fires with a raw
    ``AsyncAdapt_asyncpg_connection`` that has no sync ``execute()`` method.
    """
    connect_args: dict = {}
    server_settings: dict[str, str] = dict(SESSION_ZONE)
    idle_ms = int(settings.db_idle_in_transaction_timeout_seconds * 1000)
    if idle_ms > 0:
        server_settings["idle_in_transaction_session_timeout"] = str(idle_ms)
    statement_ms = int(settings.db_statement_timeout_seconds * 1000)
    if statement_ms > 0:
        server_settings["statement_timeout"] = str(statement_ms)
    if server_settings:
        connect_args["server_settings"] = server_settings
    return connect_args


def _pool_utilization_listener(pool):
    """Build the checkout listener for one pool.

    The pool is closed over rather than read off the event's arguments. It used
    to be `connection_record.pool`, and `_ConnectionRecord` has no such
    attribute -- SQLAlchemy name-mangles it to `_ConnectionRecord__pool` inside
    the class body. So the probe raised `AttributeError` on every checkout, the
    handler below swallowed it, and the `next(...) == 0` guard meant it was
    logged once per process and never again. The effect was not a noisy log but
    a silent one: `_pool_pressure_incident` never recorded once, so the
    `database_pool_capacity` incident that replaced the old concurrency
    guardrail has never fired, and pool exhaustion still arrives only as an
    unexplained `TimeoutError`. The pool is in hand at registration, which is
    the one place it is unambiguous.
    """

    def _log_pool_utilization(dbapi_conn, connection_record, proxy=None):
        """Track a degraded/recovered pair for sustained pool utilization.

        Called on each checkout (connection borrowed from pool). Compares
        checked-out against max connections, giving visibility into pool
        exhaustion before it surfaces as a ``TimeoutError`` to application
        code, without one warning per checkout while the pool stays pressured.
        """
        try:
            max_conn = pool.size()
            checked_out = pool.checkedout()
            if max_conn > 0 and checked_out / max_conn >= 0.8:
                _pool_pressure_incident.record_failure(error_type="PoolUtilizationHigh")
            else:
                _pool_pressure_incident.record_success()
        except Exception as exc:
            # This runs on every checkout, so the handler must never break one
            # -- but a bare `pass` means a diagnostic that starts failing is
            # simply gone, and pool exhaustion goes back to arriving as an
            # unexplained `TimeoutError`. Warned once, not per checkout: the
            # second occurrence says nothing the first did not, and there can
            # be thousands a second.
            if next(_pool_probe_failures) == 0:
                logger.warning(
                    "db.session.pool_utilization_probe_failed",
                    error_type=type(exc).__name__,
                    exc_info=exc,
                )

    return _log_pool_utilization


def get_engine():
    global engine
    if engine is None:
        engine_kwargs = {}
        connect_args: dict[str, object] = {"server_settings": dict(SESSION_ZONE)}
        if settings.environment == "testing" and not settings.db_pool_in_testing:
            engine_kwargs["poolclass"] = NullPool
        else:
            engine_kwargs["pool_size"] = settings.db_pool_size
            # max_overflow=0 on purpose. Overflow makes the per-process ceiling
            # non-deterministic, which is exactly the property that breaks
            # capacity planning once replicas autoscale; and overflow
            # connections are discarded on return, so they are the expensive
            # kind. pool_size IS the ceiling.
            engine_kwargs["max_overflow"] = 0
            engine_kwargs["pool_timeout"] = settings.db_pool_timeout_seconds
            engine_kwargs["pool_recycle"] = settings.db_pool_recycle_seconds
            # LIFO: keep reusing the hottest connections so the tail of the pool
            # ages out under pool_recycle instead of being kept warm by
            # round-robin. A burst-shaped workload then settles back to a small
            # number of live backends between bursts.
            engine_kwargs["pool_use_lifo"] = True
            connect_args = _build_connect_args()
        engine = create_async_engine(
            settings.database_url,
            json_serializer=lambda obj: json.dumps(obj, default=json_serial),
            pool_pre_ping=True,
            connect_args=connect_args,
            # Names this pool in the connection metrics. Without it the
            # instrumentation labels them with the DSN -- host and database
            # included -- which is not something the export boundary will let
            # through, so the label was dropped and every pool's readings
            # collapsed into one series.
            pool_logging_name="primary",
            **engine_kwargs,
        )
        if settings.environment != "testing":
            pool = engine.sync_engine.pool
            event.listen(pool, "checkout", _pool_utilization_listener(pool))
        # Unconditional, unlike the pool-utilization listener above: the scope
        # monitor works under NullPool too (checkout/checkin still fire), which
        # is what lets the ordinary test suite catch a held connection without
        # needing a real pool.
        translate_connect_errors(engine)
        attach_connection_scope_monitor(engine)
        watch_compiled_cache("primary", engine)
    return engine


def create_dedicated_engine(url: str, *, pool_size: int, logging_name: str):
    """A pooled engine of its own on the main database, for a component whose
    connections must not compete with the rest of the process.

    The work runtime bounds every connection it takes and sizes this pool to
    that bound, which is a guarantee only while nothing else draws from the
    same pool. Same serializer, server-side timeouts and instrumentation as the
    primary engine, and the same pooling rule: never overflowing, and unpooled
    under test unless DB_POOL_IN_TESTING asks for a pool.
    """
    pooling: dict[str, object] = {
        "pool_size": pool_size,
        "max_overflow": 0,
        "pool_timeout": settings.db_pool_timeout_seconds,
        "pool_recycle": settings.db_pool_recycle_seconds,
        "pool_use_lifo": True,
    }
    if settings.environment == "testing" and not settings.db_pool_in_testing:
        # As the primary engine does under test: parallel e2e workers each run
        # a worker process, and idle pooled connections from every one of them
        # add up past the test server's connection limit. The runtime's own
        # semaphores still bound how many it opens at once.
        pooling = {"poolclass": NullPool}
    dedicated = create_async_engine(
        url,
        json_serializer=lambda obj: json.dumps(obj, default=json_serial),
        pool_pre_ping=True,
        connect_args=_build_connect_args(),
        pool_logging_name=logging_name,
        # SQLAlchemy's default, stated: the work runtime's own statements are a
        # fixed set, and handler queries are the same shapes the primary sees.
        query_cache_size=500,
        **pooling,
    )
    if "pool_size" in pooling:
        pool = dedicated.sync_engine.pool
        event.listen(pool, "checkout", _pool_utilization_listener(pool))
    translate_connect_errors(dedicated)
    attach_connection_scope_monitor(dedicated)
    watch_compiled_cache(logging_name, dedicated)
    return dedicated


def get_session_maker():
    global _async_session_maker
    if _async_session_maker is None:
        _async_session_maker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session_maker


async def close_engine() -> None:
    """Dispose the shared async engine and clear cached makers."""
    global engine, _async_session_maker

    current_engine = engine
    engine = None
    _async_session_maker = None
    if current_engine is not None:
        await current_engine.dispose()


async def database_reachable() -> bool:
    """Can this process get a connection and run a statement on it?

    Lives beside the engine rather than in the health endpoint because what
    "reachable" means here is a property of this engine's configuration --
    ``max_overflow=0`` with a bounded ``pool_timeout``, so a saturated pool
    fails this too, which is the honest answer for a process being asked
    whether it can take more work.
    """
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


def reset_engine_state() -> None:
    """Synchronously dispose and clear the shared engine for test bootstrap."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(close_engine())
        return
    raise RuntimeError(
        "reset_engine_state() must be called from sync code; use close_engine() in async code."
    )


class LazyAsyncSessionMaker:
    def __call__(self, *args, **kwargs) -> AsyncSession:
        return get_session_maker()(*args, **kwargs)

    def configure(self, **kwargs):
        # Allow reconfiguration for tests
        return get_session_maker().configure(**kwargs)


async_session_maker = LazyAsyncSessionMaker()
