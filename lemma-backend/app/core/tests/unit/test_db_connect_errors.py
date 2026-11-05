"""A refused connection reaches callers as a SQLAlchemy error with its SQLSTATE.

Raw, it slipped past every ``except SQLAlchemyError`` meant to ride out the
database being busy, and crashed the task holding the catch instead.
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.infrastructure.db.connect_errors import translate_connect_errors
from app.core.infrastructure.db.transient_errors import is_database_unavailable


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize("translated", [True, False])
async def test_a_refused_connection_is_an_operational_error(translated: bool) -> None:
    engine = create_async_engine(
        f"postgresql+asyncpg://u:p@127.0.0.1:{_closed_port()}/db", poolclass=NullPool
    )
    if translated:
        translate_connect_errors(engine)
    try:
        with pytest.raises(Exception) as raised:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

    assert is_database_unavailable(raised.value)
    assert isinstance(raised.value, OperationalError) is translated
