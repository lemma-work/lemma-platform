"""Full diagnostics in the log; nothing but a generic message in the response.

These two properties are only useful together, so they are asserted together.
Logs are for whoever has to fix it and should hold everything — message, stack,
the lot. An HTTP response goes to whoever asked, including anyone probing the
API, and must never carry an exception's text or frames.
"""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.api.exception_handlers import register_exception_handlers
from app.core.log.log import get_logger, setup_logging

pytestmark = pytest.mark.unit

_SECRET_DETAIL = "connection to db-primary-7 failed: password authentication"


@pytest.fixture
def captured_stdout():
    setup_logging(
        "development", service_name="lemma-test", json_logs=True, log_level="DEBUG"
    )
    handler = next(
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter)
    )
    buffer = io.StringIO()
    original_stream = handler.stream
    handler.stream = buffer

    def records() -> list[dict]:
        return [json.loads(line) for line in buffer.getvalue().splitlines() if line]

    yield records
    handler.stream = original_stream


@pytest.fixture
def failing_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError(_SECRET_DETAIL)

    return app


def test_a_500_response_reveals_nothing_about_the_exception(failing_app) -> None:
    client = TestClient(failing_app, raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["message"] == "Internal server error"
    assert body["code"] == "INTERNAL_ERROR"

    rendered = json.dumps(body)
    assert _SECRET_DETAIL not in rendered
    assert "RuntimeError" not in rendered
    assert "Traceback" not in rendered
    assert "db-primary-7" not in rendered
    # No frame, file or line number anywhere in the envelope.
    assert ".py" not in rendered


def test_the_same_failure_is_logged_with_its_message_and_stack(
    captured_stdout,
) -> None:
    """The other half: what the response withholds, the log must contain."""
    setup_logging("development", service_name="lemma-test", json_logs=True)
    try:
        raise RuntimeError(_SECRET_DETAIL)
    except RuntimeError:
        get_logger("app.demo").error("http.request.failed", exc_info=True)

    record = captured_stdout()[0]
    assert record["error_type"] == "RuntimeError"
    assert record["error_message"] == _SECRET_DETAIL
    assert "Traceback (most recent call last):" in record["error_traceback"]
    assert "RuntimeError" in record["error_traceback"]
