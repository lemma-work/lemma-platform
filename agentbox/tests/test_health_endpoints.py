from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import agentbox.api.app as app_module
from agentbox.api.app import RequestContextMiddleware, health_live, health_ready


def _request(*, database=None, provider=None):
    reconciliation_task = SimpleNamespace(done=lambda: False)
    maintenance_task = SimpleNamespace(done=lambda: False)
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                database=database,
                provider=provider,
                reconciliation_task=reconciliation_task,
                maintenance_task=maintenance_task,
            )
        )
    )


@pytest.mark.asyncio
async def test_liveness_is_process_only() -> None:
    assert await health_live() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_probes_database_and_reports_provider() -> None:
    database = SimpleNamespace(healthcheck=AsyncMock())
    provider = SimpleNamespace(name="docker")
    response = await health_ready(_request(database=database, provider=provider))

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "status": "ready",
        "provider": "docker",
        "components": {
            "database": "ready",
            "provider": "ready",
            "reconciler": "ready",
            "maintenance": "ready",
        },
    }
    database.healthcheck.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_redacts_dependency_failure() -> None:
    database = SimpleNamespace(
        healthcheck=AsyncMock(side_effect=RuntimeError("CANARY database URL"))
    )
    response = await health_ready(_request(database=database, provider=None))

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "not_ready",
        "provider": None,
        "components": {
            "database": "unavailable",
            "provider": "unavailable",
            "reconciler": "ready",
            "maintenance": "ready",
        },
    }
    assert "CANARY" not in response.body.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/health/live", "/health/ready", "/livez"])
async def test_all_health_routes_are_quiet(monkeypatch, path: str) -> None:
    async def downstream(scope, receive, send) -> None:
        del scope, receive
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    debug = Mock()
    warning = Mock()
    error = Mock()
    monkeypatch.setattr(app_module.logger, "debug", debug)
    monkeypatch.setattr(app_module.logger, "warning", warning)
    monkeypatch.setattr(app_module.logger, "error", error)
    middleware = RequestContextMiddleware(downstream)
    messages = []

    async def send(message) -> None:
        messages.append(message)

    await middleware(
        {"type": "http", "path": path, "method": "GET", "headers": []},
        AsyncMock(),
        send,
    )
    assert len(messages) == 2
    debug.assert_not_called()
    warning.assert_not_called()
    error.assert_not_called()
