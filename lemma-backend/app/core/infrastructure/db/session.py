import asyncio
import json
from datetime import datetime, date
from uuid import UUID
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings
from app.core.log.log import get_logger
from app.core.observability.connection_scope import attach_connection_scope_monitor
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
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


def _build_connect_args() -> dict:
    """Build asyncpg connect_args with server-side session settings.

    asyncpg's ``server_settings`` dict is sent as ``SET <key> = <value>`` on
    each new connection. This is the asyncpg-native way to set
    ``idle_in_transaction_session_timeout`` — using a SQLAlchemy ``connect``
    event listener doesn't work because the event fires with a raw
    ``AsyncAdapt_asyncpg_connection`` that has no sync ``execute()`` method.
    """
    connect_args: dict = {}
    server_settings: dict[str, str] = {}
    idle_ms = int(settings.db_idle_in_transaction_timeout_seconds * 1000)
    if idle_ms > 0:
        server_settings["idle_in_transaction_session_timeout"] = str(idle_ms)
    statement_ms = int(settings.db_statement_timeout_seconds * 1000)
    if statement_ms > 0:
        server_settings["statement_timeout"] = str(statement_ms)
    if server_settings:
        connect_args["server_settings"] = server_settings
    return connect_args


def _log_pool_utilization(dbapi_conn, connection_record, proxy=None):
    """Track a degraded/recovered pair for sustained pool utilization.

    Called on each checkout (connection borrowed from pool). SQLAlchemy's
    PoolEvents.checkout passes (dbapi_connection, connection_record, proxy).
    Uses the pool's internal counters to compute checked-out vs. max
    connections. This gives early visibility into pool exhaustion before it
    surfaces as a ``TimeoutError`` (pool_timeout) to application code, without
    emitting one warning for every checkout while the pool remains pressured.
    """
    try:
        pool = connection_record.pool
        max_conn = pool.size()
        checked_out = pool.checkedout()
        if max_conn > 0 and checked_out / max_conn >= 0.8:
            _pool_pressure_incident.record_failure(error_type="PoolUtilizationHigh")
        else:
            _pool_pressure_incident.record_success()
    except Exception:
        pass


def get_engine():
    global engine
    if engine is None:
        engine_kwargs = {}
        connect_args = {}
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
            **engine_kwargs,
        )
        if settings.environment != "testing":
            event.listen(engine.sync_engine.pool, "checkout", _log_pool_utilization)
        # Unconditional, unlike the pool-utilization listener above: the scope
        # monitor works under NullPool too (checkout/checkin still fire), which
        # is what lets the ordinary test suite catch a held connection without
        # needing a real pool.
        attach_connection_scope_monitor(engine)
    return engine


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
    def __call__(self, *args, **kwargs):
        return get_session_maker()(*args, **kwargs)

    def configure(self, **kwargs):
        # Allow reconfiguration for tests
        return get_session_maker().configure(**kwargs)


async_session_maker = LazyAsyncSessionMaker()
