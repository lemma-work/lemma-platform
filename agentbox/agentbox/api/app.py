from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agentbox.config import settings
from agentbox.providers import build_sandbox_provider
from agentbox.providers.errors import ProviderError
from agentbox.state import AgentBoxStateStore

from .apps import router as apps_router
from .lifecycle import cleanup_loop
from .sandboxes import router as sandboxes_router
from .sessions import router as sessions_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    provider = build_sandbox_provider()
    store = AgentBoxStateStore(settings.agentbox_state_db_path)
    app.state.sandbox_provider = provider
    app.state.store = store
    app.state.sandbox_app_ready_cache = set()
    app.state.cleanup_task = asyncio.create_task(cleanup_loop(provider, store))
    try:
        yield
    finally:
        app.state.cleanup_task.cancel()
        try:
            await app.state.cleanup_task
        except asyncio.CancelledError:
            pass
        store.close()


app = FastAPI(title="AgentBox Manager", version="0.1.0", lifespan=lifespan)


@app.exception_handler(ProviderError)
async def provider_exception_handler(
    request: Request, exc: ProviderError
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "detail": {
                "message": str(exc),
                "code": exc.code,
                "retryable": exc.retryable,
            }
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(sandboxes_router)
app.include_router(sessions_router)
app.include_router(apps_router)
