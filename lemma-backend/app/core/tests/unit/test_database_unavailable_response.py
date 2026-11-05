"""A request the database could not serve is a retryable 503, not a 500.

Refused, full or restarting, the database says nothing about the request: a
500 told clients and monitors that the request had broken.
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from app.core.api.exception_handlers import register_exception_handlers


class _TooManyClients(Exception):
    sqlstate = "53300"


def _app(raised: Exception) -> FastAPI:
    app = FastAPI(debug=False)
    register_exception_handlers(app)

    @app.get("/work")
    async def work() -> None:
        raise raised

    return app


async def _get(app: FastAPI):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/work")


async def test_the_database_being_unavailable_is_a_retryable_503() -> None:
    response = await _get(
        _app(OperationalError("SELECT 1", {}, _TooManyClients("too many clients")))
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert "too many clients" not in response.text


async def test_any_other_unexpected_error_is_still_a_500() -> None:
    response = await _get(_app(RuntimeError("boom")))

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"


async def test_a_deadlock_is_retryable_too() -> None:
    class _Deadlock(Exception):
        sqlstate = "40P01"

    response = await _get(_app(OperationalError("UPDATE", {}, _Deadlock("deadlock"))))

    assert response.status_code == 503
