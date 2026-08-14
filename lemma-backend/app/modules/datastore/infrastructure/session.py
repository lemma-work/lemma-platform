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
    """Build asyncpg connect_args with server-side session settings."""
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


def get_datastore_engine():
    global _engine
    if _engine is None:
        url = settings.datastore_database_url or settings.database_url
        engine_kwargs = {}
        connect_args = {}
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
