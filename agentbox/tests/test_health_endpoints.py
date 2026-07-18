from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import agentbox.api.app as app_module
from agentbox.api.app import RequestContextMiddleware, health_live, health_ready


def _request(*, store, manager, task_done: bool = False):
    task = SimpleNamespace(done=lambda: task_done)
    state = SimpleNamespace(
        store=store,
        lifecycle_manager=manager,
        reconciliation_task=task,
        cleanup_task=task,
        provider_lease_renewal_task=task,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_liveness_is_process_only() -> None:
    assert await health_live() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_reports_only_generic_component_states() -> None:
    store = SimpleNamespace(healthcheck=AsyncMock())
    manager = SimpleNamespace(reconciliation_is_fresh=Mock(return_value=True))
    response = await health_ready(_request(store=store, manager=manager))
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "status": "ready",
        "components": {
            "manager": "ready",
            "state_store": "ready",
            "provider": "ready",
        },
    }
    store.healthcheck.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_fails_for_store_reconciliation_or_task_failure() -> None:
    store = SimpleNamespace(
        healthcheck=AsyncMock(side_effect=RuntimeError("CANARY database URL"))
    )
    manager = SimpleNamespace(reconciliation_is_fresh=Mock(return_value=False))
    response = await health_ready(
        _request(store=store, manager=manager, task_done=True)
    )
    assert response.status_code == 503
    body = json.loads(response.body)
    assert body == {
        "status": "not_ready",
        "components": {
            "manager": "unavailable",
            "state_store": "unavailable",
            "provider": "unavailable",
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
        {
            "type": "http",
            "path": path,
            "method": "GET",
            "headers": [],
        },
        AsyncMock(),
        send,
    )
    assert len(messages) == 2
    debug.assert_not_called()
    warning.assert_not_called()
    error.assert_not_called()
