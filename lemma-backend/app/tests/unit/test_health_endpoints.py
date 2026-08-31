"""Health endpoints: /health/live, /health/ready, /health, /livez."""

from __future__ import annotations

import time

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
    monkeypatch.setattr(loop_watchdog._lag, "seconds", 10.0)
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


def test_ready_is_not_ready_when_the_embedded_worker_has_stopped(
    client, monkeypatch, tmp_path
):
    """A dead worker must not read as a healthy backend.

    This is the failure it was found in. On a desktop install the worker
    stopped during a lifespan teardown and never came back, while the database
    and Redis stayed perfectly fine -- so readiness answered 200, locald's
    health gate saw nothing wrong and never restarted the process, and every
    agent run queued behind a worker that was not there. The person watching
    got a spinner for two hours and no log said why.

    The heartbeat file already existed for exactly this: it is what a
    Kubernetes liveness probe reads to restart a wedged worker. Nothing on
    desktop read it.
    """
    monkeypatch.setattr(appmod, "get_engine", lambda: _FakeEngineOk())
    monkeypatch.setattr(appmod.channel_service, "ping", AsyncMock(return_value=True))

    heartbeat = tmp_path / "worker_heartbeat"
    heartbeat.write_text(str(time.time() - 3600), encoding="utf-8")
    monkeypatch.setattr(appmod.settings, "worker_heartbeat_path", str(heartbeat))
    monkeypatch.setattr(appmod.app.state, "embedded_worker", True, raising=False)

    r = client.get("/health/ready")

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["components"]["worker"] == "stalled"
    # The dependencies it does not own are still reported honestly.
    assert body["components"]["db"] == "ok"

    # A fresh heartbeat is ready again, so a restart clears it.
    heartbeat.write_text(str(time.time()), encoding="utf-8")
    assert client.get("/health/ready").status_code == 200


def test_ready_ignores_the_worker_where_this_process_runs_none(
    client, monkeypatch, tmp_path
):
    """The cloud topology runs the worker as its own deployment.

    An API process there has no heartbeat to read, and must not call itself
    unready for the absence. Nor may a process that has simply not written its
    first heartbeat yet -- which is every start before the first tick.
    """
    monkeypatch.setattr(appmod, "get_engine", lambda: _FakeEngineOk())
    monkeypatch.setattr(appmod.channel_service, "ping", AsyncMock(return_value=True))

    monkeypatch.setattr(appmod.app.state, "embedded_worker", False, raising=False)
    monkeypatch.setattr(
        appmod.settings, "worker_heartbeat_path", str(tmp_path / "never-written")
    )
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert "worker" not in r.json()["components"]

    # Embedded, but the file is not there yet: still starting, not stalled.
    monkeypatch.setattr(appmod.app.state, "embedded_worker", True, raising=False)
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert "worker" not in r.json()["components"]


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


def test_capability_health_reports_how_the_deployment_is_configured(
    client, monkeypatch
):
    """A remote client cannot read this deployment's `.env`, so it asks.

    The product scenario suite decides from this whether a promise is even
    provable here — whether a real model answers, whether it may sign anyone up.
    Reading a local file instead would describe a different machine, and a suite
    that trusts one skips and runs for the wrong reasons.
    """
    monkeypatch.setattr(appmod.settings, "environment", "development")
    monkeypatch.setattr(appmod.settings, "e2e_llm_mode", "real")
    monkeypatch.setattr(appmod.settings, "auth_abuse_protection_enabled", False)
    monkeypatch.setattr(appmod.settings, "auth_email_verification_required", False)

    configuration = client.get("/health/capabilities").json()["configuration"]

    assert configuration["environment"] == "development"
    assert configuration["llm_mode"] == "real"
    assert configuration["abuse_protection"] is False
    assert configuration["email_verification_required"] is False
    assert "role_cache_ttl_seconds" in configuration


def test_capability_health_withholds_security_posture_in_production(
    client, monkeypatch
):
    """This endpoint is unauthenticated, and production owes a stranger nothing.

    Whether signup is rate limited and whether a connector may reach a private
    address are the two facts an attacker would most like handed to them. They
    are reported where a test suite needs them and withheld where they would be
    reconnaissance. `llm_mode` survives both ways: production serving the
    scripted test model is a misconfiguration worth seeing.
    """
    monkeypatch.setattr(appmod.settings, "environment", "production")

    configuration = client.get("/health/capabilities").json()["configuration"]

    assert configuration["environment"] == "production"
    assert configuration["llm_mode"]
    withheld = {
        "abuse_protection",
        "altcha",
        "email_verification_required",
        "email_deliverability_checks",
        "disposable_email_domains",
        "private_network_targets",
    }
    assert not withheld & set(configuration), (
        f"production disclosed its security posture: "
        f"{sorted(withheld & set(configuration))}"
    )


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
