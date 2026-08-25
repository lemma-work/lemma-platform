from __future__ import annotations

import json
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.observability.connection_scope import attach_connection_scope_monitor

_engine = None
_session_maker = None


def _json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _build_datastore_connect_args() -> dict:
    """Build asyncpg connect_args with server-side session settings.

    ``statement_cache_size=0`` is the one that is not about timeouts, and it is
    here rather than on the primary engine because only this one runs SQL
    against tables a person can change.

    asyncpg caches prepared statements per connection, keyed on the SQL text.
    A record read is ``SELECT * FROM "<schema>"."<table>"`` — the same text
    before and after a column is added or removed — so the cached plan is
    reused with a result descriptor that no longer matches the table, and
    asyncpg raises ``InvalidCachedStatementError``. Nothing caught it, so it
    left as a 400: a person removed a column and their table stopped being
    readable.

    Worse than one bad request, because the connection is pooled. Any request
    landing on it saw the same 400, and it cleared itself only when the
    connection recycled — a table that broke and then healed with nothing done
    in between. Which of add or remove hit it was never about the operation but
    about which connection served the next read; ``pool_use_lifo`` makes that
    reliably unpredictable.

    The cost is a parse per statement. That is the right trade for a schema
    that belongs to users rather than to migrations: the primary engine keeps
    its cache, because its tables change only at deploy time.
    """
    connect_args: dict = {"statement_cache_size": 0}
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


def get_datastore_engine():
    global _engine
    if _engine is None:
        url = settings.datastore_database_url or settings.database_url
        engine_kwargs = {}
        # In both branches, because it is a property of what this engine runs
        # rather than of where it runs. Testing pools with NullPool, so a stale
        # cached plan cannot survive to be reused there — which is exactly why
        # this bug was invisible to the whole scenario suite locally and showed
        # up only against a deployment with a real pool. Setting it in one
        # branch would have preserved that difference.
        connect_args: dict = {"statement_cache_size": 0}
        if settings.environment == "testing":
            engine_kwargs["poolclass"] = NullPool
        else:
            # Same knob as the primary engine, same reasoning: one number, and
            # pool_size is the ceiling because max_overflow is pinned to 0.
            engine_kwargs["pool_size"] = settings.db_pool_size
            engine_kwargs["max_overflow"] = 0
            engine_kwargs["pool_timeout"] = settings.db_pool_timeout_seconds
            engine_kwargs["pool_recycle"] = settings.db_pool_recycle_seconds
            engine_kwargs["pool_use_lifo"] = True
            connect_args = _build_datastore_connect_args()
        _engine = create_async_engine(
            url,
            json_serializer=lambda obj: json.dumps(obj, default=_json_serial),
            pool_pre_ping=True,
            connect_args=connect_args,
            # Distinguishes this pool from the primary one in the connection
            # metrics. When both point at the same database the two pools are
            # otherwise indistinguishable, and their readings sum into a single
            # meaningless series.
            pool_logging_name="datastore",
            **engine_kwargs,
        )
        # The datastore pool has no telemetry of its own; one line gives the
        # monitor both engines.
        attach_connection_scope_monitor(_engine)
    return _engine


def get_datastore_session_maker():
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            get_datastore_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_maker


async def close_datastore_engine() -> None:
    global _engine, _session_maker
    engine = _engine
    _engine = None
    _session_maker = None
    if engine is not None:
        await engine.dispose()
    from app.modules.datastore.infrastructure.transactional_events import (
        reset_datastore_event_outbox_state,
    )

    reset_datastore_event_outbox_state()
