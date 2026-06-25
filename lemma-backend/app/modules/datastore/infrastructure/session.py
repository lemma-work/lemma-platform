from __future__ import annotations

import json
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

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
    timeout_ms = int(settings.db_idle_in_transaction_timeout_seconds * 1000)
    if timeout_ms > 0:
        server_settings["idle_in_transaction_session_timeout"] = str(timeout_ms)
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
            engine_kwargs["pool_size"] = settings.datastore_db_pool_size
            engine_kwargs["max_overflow"] = settings.datastore_db_max_overflow
            engine_kwargs["pool_recycle"] = settings.db_pool_recycle_seconds
            connect_args = _build_datastore_connect_args()
        _engine = create_async_engine(
            url,
            json_serializer=lambda obj: json.dumps(obj, default=_json_serial),
            pool_pre_ping=True,
            connect_args=connect_args,
            **engine_kwargs,
        )
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
