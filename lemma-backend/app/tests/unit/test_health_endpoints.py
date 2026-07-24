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


def test_ready_echoes_runtime_instance_id(client, monkeypatch):
    monkeypatch.setattr(appmod, "get_engine", lambda: _FakeEngineOk())
    monkeypatch.setattr(appmod.channel_service, "ping", AsyncMock(return_value=True))
    monkeypatch.setattr(appmod.settings, "lemma_runtime_instance_id", "launch-123")

    r = client.get("/health/ready")

    assert r.status_code == 200
    assert r.json()["instance_id"] == "launch-123"


def test_capability_health_reports_embeddings_separately(client, monkeypatch):
    from app.modules.datastore import module as datastore_module

    monkeypatch.setattr(datastore_module._embedding_capability, "status", "preparing")
    monkeypatch.setattr(
        datastore_module._embedding_capability,
        "detail",
        "Preparing the local search model",
    )

    r = client.get("/health/capabilities")

    assert r.status_code == 200
    assert r.json()["capabilities"]["embeddings"] == {
        "status": "preparing",
        "detail": "Preparing the local search model",
    }


def test_capability_health_exposes_safe_local_ai_readiness(client, monkeypatch):
    monkeypatch.setattr(appmod.settings, "lemma_local_ai_ready", False)

    r = client.get("/health/capabilities")

    assert r.status_code == 200
    assert r.json()["capabilities"]["ai_profile"] == {
        "status": "needs_setup",
        "detail": "Configure an AI provider in Lemma Control Center",
    }


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
