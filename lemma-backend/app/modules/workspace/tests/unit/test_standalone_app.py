from __future__ import annotations

import importlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from app.standalone import EmbeddedApp, build_standalone_app  # noqa: E402

standalone_app = importlib.import_module("standalone_app")


def _fake_worker(monkeypatch, events: list[str]) -> None:
    """The embedded work runtime, recorded: it starts after the API and stops first."""

    @asynccontextmanager
    async def worker_services(modules, **kwargs) -> AsyncIterator[None]:
        assert kwargs == {"service_name": "lemma-api", "embedded": True}
        assert modules
        events.append("worker-started")
        yield
        events.append("worker-stopped")

    monkeypatch.setattr(
        "app.core.infrastructure.work.process.worker_services", worker_services
    )


@pytest.mark.asyncio
async def test_standalone_lifespan_starts_api_then_the_embedded_worker(
    monkeypatch,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def api_lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        events.append("api-started")
        yield
        events.append("api-stopped")

    api_app = FastAPI(lifespan=api_lifespan)
    monkeypatch.setattr(standalone_app, "create_api_app", lambda: api_app)
    _fake_worker(monkeypatch, events)

    app = standalone_app.create_standalone_app()
    assert app.state.embedded_worker is True

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/sandbox/state")

    assert response.status_code == 404
    assert events == [
        "api-started",
        "worker-started",
        "worker-stopped",
        "api-stopped",
    ]


@pytest.mark.asyncio
async def test_standalone_owns_mounted_app_lifespan(monkeypatch) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan_named(name: str) -> AsyncIterator[None]:
        events.append(f"{name}-started")
        yield
        events.append(f"{name}-stopped")

    @asynccontextmanager
    async def api_lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        async with lifespan_named("api"):
            yield

    @asynccontextmanager
    async def embedded_lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        async with lifespan_named("embedded"):
            yield

    api_app = FastAPI(lifespan=api_lifespan)
    embedded_app = FastAPI(lifespan=embedded_lifespan)

    @embedded_app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    _fake_worker(monkeypatch, events)
    app = build_standalone_app(
        api_app,
        embedded_apps=(EmbeddedApp("/internal/manager", embedded_app),),
    )

    assert app.state.embedded_apps == ("/internal/manager",)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/internal/manager/ping")
            assert response.json() == {"status": "ok"}

    assert events == [
        "api-started",
        "embedded-started",
        "worker-started",
        "worker-stopped",
        "embedded-stopped",
        "api-stopped",
    ]
