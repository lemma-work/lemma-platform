"""Raw asyncpg connect arguments derived from a SQLAlchemy URL."""

from __future__ import annotations

from app.core.infrastructure.db.direct_connection import asyncpg_connect_kwargs


def test_connect_kwargs_lift_ssl_out_of_the_query_string() -> None:
    """asyncpg takes ssl as an argument; leaving it in the DSN fails to connect."""
    kwargs = asyncpg_connect_kwargs(
        "postgresql+asyncpg://u:p@h:5432/db?ssl=require", application_name="probe"
    )
    assert kwargs["dsn"] == "postgresql://u:p@h:5432/db"
    assert kwargs["ssl"] == "require"
    assert kwargs["server_settings"]["application_name"] == "probe"


def test_connect_kwargs_accept_the_libpq_spelling() -> None:
    kwargs = asyncpg_connect_kwargs(
        "postgresql+asyncpg://u:p@h/db?sslmode=verify-full", application_name="probe"
    )
    assert kwargs["ssl"] == "verify-full"


def test_connect_kwargs_omit_ssl_when_unset() -> None:
    kwargs = asyncpg_connect_kwargs(
        "postgresql+asyncpg://u:p@h/db", application_name="probe"
    )
    assert "ssl" not in kwargs
