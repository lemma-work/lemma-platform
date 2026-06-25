import asyncio
import json
from datetime import datetime, date
from uuid import UUID
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

engine = None
_async_session_maker = None


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _set_idle_in_transaction_timeout(conn):
    """Set idle_in_transaction_session_timeout on each new raw DB connection.

    This is a server-side guard: Postgres automatically aborts any transaction
    that sits idle (not executing a query) for longer than the specified time.
    The aborted transaction's connection is then returned to the pool by
    SQLAlchemy's rollback-on-checkin. This catches the "session held open
    during external I/O" anti-pattern at the database level, preventing a
    single leaked session from monopolizing a pooled connection indefinitely.
    """
    timeout_ms = int(settings.db_idle_in_transaction_timeout_seconds * 1000)
    if timeout_ms > 0:
        conn.execute(f"SET idle_in_transaction_session_timeout = {timeout_ms}")


def _log_pool_utilization(pool):
    """Log a warning when pool utilization exceeds 80% of max capacity.

    Called on each checkout (connection borrowed from pool). Uses the pool's
    internal counters to compute checked-out vs. max connections. This gives
    early visibility into pool exhaustion before it surfaces as a
    ``TimeoutError`` (pool_timeout) to application code.
    """
    try:
        max_conn = pool.size() + pool._max_overflow  # noqa: SLF001
        checked_out = pool.checkedout()
        if max_conn > 0 and checked_out / max_conn >= 0.8:
            logger.warning(
                "DB pool utilization high",
                extra={
                    "checked_out": checked_out,
                    "max_connections": max_conn,
                    "utilization_pct": round(checked_out / max_conn * 100, 1),
                    "pool_size": pool.size(),
                    "max_overflow": pool._max_overflow,  # noqa: SLF001
                    "overflow": pool.overflow(),
                },
            )
    except Exception:
        pass


def get_engine():
    global engine
    if engine is None:
        engine_kwargs = {}
        if settings.environment == "testing":
            engine_kwargs["poolclass"] = NullPool
        else:
            engine_kwargs["pool_size"] = settings.db_pool_size
            engine_kwargs["max_overflow"] = settings.db_max_overflow
            engine_kwargs["pool_timeout"] = settings.db_pool_timeout_seconds
            engine_kwargs["pool_recycle"] = settings.db_pool_recycle_seconds
        engine = create_async_engine(
            settings.database_url,
            json_serializer=lambda obj: json.dumps(obj, default=json_serial),
            pool_pre_ping=True,
            **engine_kwargs,
        )
        if settings.environment != "testing":
            event.listen(engine.sync_engine, "connect", _set_idle_in_transaction_timeout)
            event.listen(engine.sync_engine.pool, "checkout", _log_pool_utilization)
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
