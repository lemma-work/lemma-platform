"""`DEBUG` decides whether the error envelope exists at all.

Starlette installs the application's ``Exception`` handler as
``ServerErrorMiddleware.handler`` and checks ``self.debug`` **first**::

    if self.debug:
        response = self.debug_response(request, exc)   # full HTML traceback
    elif self.handler is None:
        ...
    else:
        response = await self.handler(request, exc)    # never reached

So ``debug`` is not a verbosity setting: while it is on,
``handle_unexpected_exception`` -- and with it the ``{message, code, details}``
envelope every client parses -- is unreachable, and a 500 answers with file
paths, framework versions and local variable names instead.

The setting used to default to ``True``, which meant a deployment that never
mentioned ``DEBUG`` served tracebacks. These tests hold both halves of the fix:
the default, and the startup refusal that catches a deployment setting it
deliberately.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.api.exception_handlers import register_exception_handlers
from app.core.config import Settings

PRODUCTION = {"environment": "production", "app_base_domain": "apps.example.com"}


def test_debug_is_off_unless_somebody_asks_for_it() -> None:
    assert Settings(**PRODUCTION, _env_file=None).debug is False


def test_debug_is_refused_outside_local() -> None:
    with pytest.raises(ValueError, match="DEBUG must be false"):
        Settings(**PRODUCTION, debug=True, _env_file=None)


@pytest.mark.parametrize("environment", ["local", "testing"])
def test_debug_is_still_allowed_where_a_person_reads_the_traceback(
    environment: str,
) -> None:
    assert Settings(environment=environment, debug=True, _env_file=None).debug is True


def _app_that_fails(*, debug: bool) -> FastAPI:
    """The application's error wiring, and one route that raises through it."""
    app = FastAPI(debug=debug)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("a secret-looking internal detail")

    return app


async def _answer_to_a_500(*, debug: bool):
    # `raise_app_exceptions=False` so the transport reports what a real client
    # over a real socket would receive, rather than re-raising into the test.
    transport = ASGITransport(
        app=_app_that_fails(debug=debug), raise_app_exceptions=False
    )
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/boom")


async def test_an_unhandled_error_answers_the_envelope() -> None:
    answer = await _answer_to_a_500(debug=False)

    assert answer.status_code == 500
    body = answer.json()
    assert body["message"] == "Internal server error"
    assert body["code"] == "INTERNAL_ERROR"
    assert "a secret-looking internal detail" not in answer.text, (
        "the exception message reached the client; an API response never carries one"
    )
    assert "Traceback" not in answer.text


async def test_debug_replaces_the_envelope_with_a_traceback() -> None:
    """Not a promise -- the reason the two guards above exist.

    If this ever stops holding, Starlette has changed the ordering and the
    startup refusal is protecting against something that no longer happens.
    """
    answer = await _answer_to_a_500(debug=True)

    assert "application/json" not in answer.headers.get("content-type", "")
    assert "a secret-looking internal detail" in answer.text
