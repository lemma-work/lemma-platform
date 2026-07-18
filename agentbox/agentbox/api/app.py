from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import re
import time
import uuid
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agentbox.config import settings
from agentbox.endpoint_state import validate_endpoint_state_keyring
from agentbox.lifecycle_manager import SandboxLifecycleManager, reconciliation_loop
from agentbox.providers import build_sandbox_provider
from agentbox.providers.errors import ProviderError
from agentbox.providers.protocol import SandboxCapabilitiesProvider
from agentbox.state_store import create_state_store
from agentbox.telemetry import instrument_app, shutdown_telemetry
from agentbox.observability import (
    bind_context,
    create_background_task,
    get_logger,
    validate_release_identity,
)

from .apps import router as apps_router
from .lifecycle import cleanup_loop, provider_lease_renewal_loop
from .sandboxes import router as sandboxes_router
from .sessions import router as sessions_router

logger = get_logger(__name__)
_QUIET_HEALTH_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/livez"})


class RequestContextMiddleware:
    REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
    JOB_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
    SLOW_SECONDS = 2.0

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = list(scope.get("headers") or [])

        def header(name: bytes) -> str:
            value = next((v for k, v in headers if k.lower() == name), b"")
            return value.decode("latin-1")

        inbound_request_id = header(b"x-request-id")
        request_id = (
            inbound_request_id
            if self.REQUEST_ID_RE.fullmatch(inbound_request_id)
            else uuid.uuid4().hex
        )
        provided_key = header(b"x-api-key").strip()
        expected_key = (settings.agentbox_api_key or "").strip()
        trusted = bool(provided_key and expected_key) and hmac.compare_digest(
            provided_key, expected_key
        )

        def trusted_uuid(name: bytes) -> UUID | None:
            if not trusted:
                return None
            try:
                return UUID(header(name))
            except ValueError:
                return None

        correlation_id = trusted_uuid(b"x-lemma-correlation-id") or uuid.uuid4()
        event_id = trusted_uuid(b"x-lemma-event-id")
        raw_job_id = header(b"x-lemma-job-id") if trusted else ""
        job_id = raw_job_id if self.JOB_ID_RE.fullmatch(raw_job_id) else None
        scope = dict(scope)
        scope["headers"] = [
            (key, value) for key, value in headers if key.lower() != b"x-request-id"
        ] + [(b"x-request-id", request_id.encode("ascii"))]

        started_at = time.perf_counter()
        response_started_at: float | None = None
        status_code = 500
        content_type = ""

        async def send_with_request_id(message):
            nonlocal response_started_at, status_code, content_type
            if message["type"] == "http.response.start":
                response_started_at = time.perf_counter()
                status_code = int(message.get("status", 500))
                response_headers = [
                    (key, value)
                    for key, value in list(message.get("headers") or [])
                    if key.lower() != b"x-request-id"
                ]
                content_type = next(
                    (
                        value.decode("latin-1").lower()
                        for key, value in response_headers
                        if key.lower() == b"content-type"
                    ),
                    "",
                )
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": response_headers}
            await send(message)

        caught: Exception | None = None
        cancelled = False
        with bind_context(
            request_id=request_id,
            correlation_id=correlation_id,
            event_id=event_id,
            job_id=job_id,
        ):
            try:
                await self.app(scope, receive, send_with_request_id)
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as exc:
                caught = exc
                if response_started_at is not None:
                    raise
                state = scope.setdefault("state", {})
                state["lemma_error_type"] = type(exc).__name__
                state["lemma_error_code"] = "INTERNAL_ERROR"
                status_code = 500
                response = JSONResponse(
                    status_code=500,
                    content={
                        "detail": {
                            "message": "Internal server error",
                            "code": "INTERNAL_ERROR",
                            "retryable": False,
                        }
                    },
                )
                await response(scope, receive, send_with_request_id)
            finally:
                if (
                    not cancelled
                    and str(scope.get("path", "")) not in _QUIET_HEALTH_PATHS
                ):
                    finished_at = time.perf_counter()
                    duration_ms = round((finished_at - started_at) * 1000, 1)
                    route_object = scope.get("route")
                    route = getattr(route_object, "path_format", None) or "unmatched"
                    state = scope.get("state") or {}
                    error_type = state.get(
                        "lemma_error_type",
                        type(caught).__name__ if caught else "HTTPError",
                    )
                    error_code = state.get("lemma_error_code", "INTERNAL_ERROR")
                    if status_code >= 500 or caught is not None:
                        exc_info = (
                            (type(caught), caught, caught.__traceback__)
                            if caught is not None
                            else None
                        )
                        logger.error(
                            "http.request.failed",
                            method=str(scope.get("method", "UNKNOWN")),
                            route=str(route),
                            status_code=status_code,
                            duration_ms=duration_ms,
                            error_type=str(error_type),
                            error_code=str(error_code),
                            exc_info=exc_info,
                        )
                    elif status_code == 429:
                        logger.warning(
                            "http.request.rate_limited",
                            method=str(scope.get("method", "UNKNOWN")),
                            route=str(route),
                            status_code=status_code,
                            duration_ms=duration_ms,
                        )
                    else:
                        streaming = content_type.startswith("text/event-stream")
                        elapsed = (
                            response_started_at - started_at
                            if streaming and response_started_at is not None
                            else finished_at - started_at
                        )
                        if elapsed >= self.SLOW_SECONDS:
                            logger.warning(
                                "http.request.slow",
                                method=str(scope.get("method", "UNKNOWN")),
                                route=str(route),
                                status_code=status_code,
                                duration_ms=round(elapsed * 1000, 1),
                                latency_kind=(
                                    "time_to_first_byte" if streaming else "total"
                                ),
                            )
                        else:
                            logger.debug(
                                "http.request.completed",
                                method=str(scope.get("method", "UNKNOWN")),
                                route=str(route),
                                status_code=status_code,
                                duration_ms=duration_ms,
                            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_release_identity()
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
        manager.record_reconciliation_success()
        app.state.cleanup_task = create_background_task(
            cleanup_loop(manager), name="agentbox-cleanup"
        )
        app.state.reconciliation_task = create_background_task(
            reconciliation_loop(manager), name="agentbox-reconciliation"
        )
        app.state.provider_lease_renewal_task = create_background_task(
            provider_lease_renewal_loop(manager), name="agentbox-provider-lease-renewal"
        )
        logger.info("service.started")
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
            logger.info("service.stopped")
            shutdown_telemetry()
    finally:
        await provider.close()
        if store is not None:
            await store.close()


app = FastAPI(title="AgentBox Manager", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
instrument_app(app)


@app.exception_handler(ProviderError)
async def provider_exception_handler(
    request: Request, exc: ProviderError
) -> JSONResponse:
    state = request.scope.setdefault("state", {})
    state["lemma_error_type"] = type(exc).__name__
    state["lemma_error_code"] = exc.code
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


@app.get("/health/live")
@app.get("/livez")
async def health_live() -> dict[str, str]:
    """Process-only liveness; never performs dependency I/O."""
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    """Bounded readiness with generic, non-sensitive component states."""
    components = {
        "manager": "ready",
        "state_store": "ready",
        "provider": "ready",
    }
    app_state = request.app.state
    manager = getattr(app_state, "lifecycle_manager", None)
    store = getattr(app_state, "store", None)
    reconciliation_task = getattr(app_state, "reconciliation_task", None)
    cleanup_task = getattr(app_state, "cleanup_task", None)
    lease_task = getattr(app_state, "provider_lease_renewal_task", None)

    if manager is None or any(
        task is None or task.done()
        for task in (reconciliation_task, cleanup_task, lease_task)
    ):
        components["manager"] = "unavailable"

    if store is None:
        components["state_store"] = "unavailable"
    else:
        try:
            await asyncio.wait_for(store.healthcheck(), timeout=0.75)
        except Exception:
            components["state_store"] = "unavailable"

    if (
        manager is None
        or reconciliation_task is None
        or reconciliation_task.done()
        or not manager.reconciliation_is_fresh()
    ):
        components["provider"] = "unavailable"

    ready = all(value == "ready" for value in components.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "components": components,
        },
    )


app.include_router(sandboxes_router)
app.include_router(sessions_router)
app.include_router(apps_router)
