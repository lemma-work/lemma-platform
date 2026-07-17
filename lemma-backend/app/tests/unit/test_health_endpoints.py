"""Health endpoints: /health/live, /health/ready, /health, /livez."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.app as appmod
from app.core.observability import loop_watchdog

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    loop_watchdog.reset_loop_watchdog_state()
    return TestClient(appmod.app, raise_server_exceptions=False)


def test_liveness_endpoints_return_ok(client):
    for path in ("/health/live", "/livez", "/health"):
        r = client.get(path)
        assert r.status_code == 200, path
        body = r.json()
        assert body["status"] == "ok"
        assert "loop_lag_seconds" in body


def test_liveness_returns_503_when_loop_wedged(client, monkeypatch):
    # Force unhealthy lag above the unhealthy threshold.
    monkeypatch.setattr(loop_watchdog, "_last_lag_seconds", 10.0)
    monkeypatch.setattr(
        "app.core.observability.loop_watchdog.settings.loop_lag_unhealthy_seconds", 5.0
    )
    r = client.get("/health/live")
    assert r.status_code == 503
    assert r.json()["status"] == "unhealthy"


class _FakeConn:
    async def execute(self, *_a, **_kw):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakeEngineOk:
    def connect(self):
        return _FakeConn()


class _FakeEngineDown:
    def connect(self):
        raise ConnectionError("db down")


def test_ready_returns_200_when_dependencies_ok(client, monkeypatch):
    monkeypatch.setattr(appmod, "get_engine", lambda: _FakeEngineOk())
    monkeypatch.setattr(appmod.channel_service, "ping", AsyncMock(return_value=True))
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["components"] == {"db": "ok", "redis": "ok"}


def test_ready_returns_503_when_db_down(client, monkeypatch):
    monkeypatch.setattr(appmod, "get_engine", lambda: _FakeEngineDown())
    monkeypatch.setattr(appmod.channel_service, "ping", AsyncMock(return_value=True))
    r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["components"]["db"] == "down"
    assert body["components"]["redis"] == "ok"


def test_ready_returns_503_when_redis_down(client, monkeypatch):
    monkeypatch.setattr(appmod, "get_engine", lambda: _FakeEngineOk())
    monkeypatch.setattr(appmod.channel_service, "ping", AsyncMock(return_value=False))
    r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["components"]["db"] == "ok"
    assert body["components"]["redis"] == "down"
