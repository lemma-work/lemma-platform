from __future__ import annotations

import json
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import event
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


def _set_datastore_idle_in_transaction_timeout(conn):
    timeout_ms = int(settings.db_idle_in_transaction_timeout_seconds * 1000)
    if timeout_ms > 0:
        conn.execute(f"SET idle_in_transaction_session_timeout = {timeout_ms}")


def get_datastore_engine():
    global _engine
    if _engine is None:
        url = settings.datastore_database_url or settings.database_url
        engine_kwargs = {}
        if settings.environment == "testing":
            engine_kwargs["poolclass"] = NullPool
        else:
            engine_kwargs["pool_size"] = settings.datastore_db_pool_size
            engine_kwargs["max_overflow"] = settings.datastore_db_max_overflow
            engine_kwargs["pool_recycle"] = settings.db_pool_recycle_seconds
        _engine = create_async_engine(
            url,
            json_serializer=lambda obj: json.dumps(obj, default=_json_serial),
            pool_pre_ping=True,
            **engine_kwargs,
        )
        if settings.environment != "testing":
            event.listen(
                _engine.sync_engine, "connect", _set_datastore_idle_in_transaction_timeout
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
