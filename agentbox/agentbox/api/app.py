from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agentbox.config import settings
from agentbox.endpoint_state import validate_endpoint_state_keyring
from agentbox.lifecycle_manager import SandboxLifecycleManager, reconciliation_loop
from agentbox.providers import build_sandbox_provider
from agentbox.providers.errors import ProviderError
from agentbox.providers.protocol import SandboxCapabilitiesProvider
from agentbox.state_store import create_state_store

from .apps import router as apps_router
from .lifecycle import cleanup_loop, provider_lease_renewal_loop
from .sandboxes import router as sandboxes_router
from .sessions import router as sessions_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Routes can contain provider access tokens. Refuse to accept traffic unless
    # the durable-state encryption keyring is valid, rather than discovering a
    # missing key on the first sandbox create or migrated-route republish.
    validate_endpoint_state_keyring()
    provider = build_sandbox_provider()
    store = None
    try:
        store = await create_state_store(
            database_url=settings.agentbox_state_database_url,
            sqlite_path=settings.agentbox_state_db_path,
            durable_env_keys=settings.agentbox_state_durable_env_key_set,
        )
        manager = SandboxLifecycleManager(provider, store, owner=str(uuid.uuid4()))
        app.state.sandbox_provider = provider
        app.state.store = store
        app.state.lifecycle_manager = manager
        app.state.sandbox_app_ready_cache = set()
        # Reconcile before accepting requests so durable reservations and
        # provider inventory agree after a manager revision restart.
        await manager.reconcile()
        app.state.cleanup_task = asyncio.create_task(cleanup_loop(manager))
        app.state.reconciliation_task = asyncio.create_task(
            reconciliation_loop(manager)
        )
        app.state.provider_lease_renewal_task = asyncio.create_task(
            provider_lease_renewal_loop(manager)
        )
        try:
            yield
        finally:
            tasks = (
                app.state.cleanup_task,
                app.state.reconciliation_task,
                app.state.provider_lease_renewal_task,
            )
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await manager.close()
    finally:
        await provider.close()
        if store is not None:
            await store.close()


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
async def health(request: Request) -> dict[str, str | bool]:
    provider = request.app.state.sandbox_provider
    response: dict[str, str | bool] = {
        "status": "ok",
        "provider": provider.provider_name,
    }
    if isinstance(provider, SandboxCapabilitiesProvider):
        response.update(provider.capabilities.diagnostic())
    return response


app.include_router(sandboxes_router)
app.include_router(sessions_router)
app.include_router(apps_router)
