"""Severity policy for the global HTTP exception handlers.

Verifies the Phase-1 contract: routine 4xx -> debug (no steady-state prod event),
auth denial (401/403) -> sampled info, rate-limit (429) -> rate-limited warning,
5xx -> error once. Domain ``message``/``details``, validation payloads, and HTTP
``detail`` must NOT appear in the log record.
"""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.api import exception_handlers
from app.core.api.exception_handlers import register_exception_handlers
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger, setup_logging

pytestmark = pytest.mark.unit


def _pf_handler() -> logging.Handler:
    for h in logging.getLogger().handlers:
        if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter):
            return h
    raise AssertionError("ProcessorFormatter handler not installed")


@pytest.fixture
def log_buf():
    setup_logging("development", service_name="lemma-test", json_logs=True, log_level="DEBUG")
    # Module-level loggers were bound at import time with INFO filtering, so they
    # ignore a later DEBUG reconfigure. Rebind to a fresh DEBUG logger so the
    # severity policy is observable. (In production 4xx debug is correctly
    # filtered at INFO — that is the contract.) Also reset the cross-test
    # throttle state so each test starts clean.
    exception_handlers.logger = get_logger("app.core.api.exception_handlers")
    exception_handlers._last_throttled_emit.clear()
    buf = io.StringIO()
    handler = _pf_handler()
    original = handler.stream
    handler.stream = buf
    yield buf
    handler.stream = original


def _events(buf):
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def _app() -> FastAPI:
    test_app = FastAPI()
    register_exception_handlers(test_app)

    class _Body(BaseModel):
        n: int

    @test_app.get("/domain404")
    async def _d4():
        raise DomainError("nope", code="WIDGET_GONE", status_code=404, details={"x": 1})

    @test_app.get("/domain500")
    async def _d5():
        raise DomainError("server boom", code="WIDGET_BROKE", status_code=500)

    @test_app.get("/http401")
    async def _h401():
        raise HTTPException(status_code=401, detail="no token")

    @test_app.get("/http403")
    async def _h403():
        raise HTTPException(status_code=403, detail="forbidden")

    @test_app.get("/http429")
    async def _h429():
        raise HTTPException(status_code=429, detail="slow down")

    @test_app.post("/validate")
    async def _v(body: _Body):
        return body

    @test_app.get("/boom")
    async def _boom():
        raise RuntimeError("kaboom")

    return test_app


def _client():
    return TestClient(_app(), raise_server_exceptions=False)


def test_routine_4xx_logs_at_debug_without_sensitive_payload(log_buf):
    c = _client()
    r = c.get("/domain404", headers={"x-request-id": "rid-1"})
    assert r.status_code == 404
    ev = [e for e in _events(log_buf) if e.get("event") == "request.error"]
    assert len(ev) == 1
    assert ev[0]["level"] == "debug"
    assert ev[0]["code"] == "WIDGET_GONE"
    assert ev[0]["status_code"] == 404
    assert ev[0]["error_type"] == "DomainError"
    assert ev[0]["request_id"] == "rid-1"
    # Domain message/details must not be logged.
    assert "message" not in ev[0]
    assert "details" not in ev[0]
    assert "nope" not in log_buf.getvalue()


def test_validation_422_logs_at_debug(log_buf):
    c = _client()
    r = c.post("/validate", json={"n": "not-int"})
    assert r.status_code == 422
    ev = [e for e in _events(log_buf) if e.get("event") == "request.error"]
    assert len(ev) == 1
    assert ev[0]["level"] == "debug"
    assert ev[0]["code"] == "VALIDATION_ERROR"
    # No validation payload logged.
    assert "errors" not in ev[0]


def test_auth_denial_logs_throttled_info(log_buf):
    c = _client()
    c.get("/http401")
    c.get("/http403")
    auth = [e for e in _events(log_buf) if e.get("event") == "auth.denied"]
    assert len(auth) == 2
    assert all(e["level"] == "info" for e in auth)
    assert {e["status_code"] for e in auth} == {401, 403}
    # detail string must not be logged.
    assert "no token" not in log_buf.getvalue()
    assert "forbidden" not in log_buf.getvalue()


def test_auth_denial_throttled_within_window(log_buf):
    c = _client()
    # Two rapid 401 on the same route within the throttle window emit once.
    c.get("/http401")
    c.get("/http401")
    auth = [e for e in _events(log_buf) if e.get("event") == "auth.denied"]
    assert len(auth) == 1


def test_rate_limit_logs_throttled_warning(log_buf):
    c = _client()
    c.get("/http429")
    rl = [e for e in _events(log_buf) if e.get("event") == "request.rate_limited"]
    assert len(rl) == 1
    assert rl[0]["level"] == "warning"
    assert rl[0]["status_code"] == 429


def test_5xx_domain_error_logs_error_once(log_buf):
    c = _client()
    r = c.get("/domain500")
    assert r.status_code == 500
    ev = [e for e in _events(log_buf) if e.get("event") == "request.error"]
    assert len(ev) == 1
    assert ev[0]["level"] == "error"
    assert ev[0]["code"] == "WIDGET_BROKE"
    assert "server boom" not in log_buf.getvalue()


def test_unhandled_500_logs_error_once_with_traceback(log_buf):
    c = _client()
    r = c.get("/boom")
    assert r.status_code == 500
    ev = [e for e in _events(log_buf) if e.get("event") == "request.error"]
    assert len(ev) == 1
    assert ev[0]["level"] == "error"
    assert "exception" in ev[0]
    assert "RuntimeError" in ev[0]["exception"]
