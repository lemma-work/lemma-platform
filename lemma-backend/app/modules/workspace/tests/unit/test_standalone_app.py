from __future__ import annotations

import importlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from anyio import TASK_STATUS_IGNORED, sleep_forever
from anyio.abc import TaskStatus
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
standalone_app = importlib.import_module("standalone_app")


@pytest.mark.asyncio
async def test_standalone_lifespan_starts_api_scheduler_and_worker(monkeypatch) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def api_lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        events.append("api-started")
        yield
        events.append("api-stopped")

    api_app = FastAPI(lifespan=api_lifespan)

    monkeypatch.setattr(standalone_app, "create_api_app", lambda: api_app)

    class FakeScheduler:
        _started = False

        async def start(self) -> None:
            self._started = True
            events.append("scheduler-started")

        async def shutdown(self) -> None:
            self._started = False
            events.append("scheduler-stopped")

    class FakeWorker:
        async def run_async(
            self,
            *,
            task_status: TaskStatus[None] = TASK_STATUS_IGNORED,
        ) -> None:
            task_status.started()
            await sleep_forever()

    # The scheduler + worker assembly lives in app.standalone now (shared with
    # the cloud standalone app), so patch it there. _prepare_embedded_worker
    # takes the worker as an argument now.
    monkeypatch.setattr("app.standalone.get_scheduler_service", lambda: FakeScheduler())
    monkeypatch.setattr(
        "app.standalone._prepare_embedded_worker", lambda worker: FakeWorker()
    )

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
        "scheduler-started",
        "scheduler-stopped",
        "api-stopped",
    ]
