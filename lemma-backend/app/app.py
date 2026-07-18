import asyncio
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import Depends, FastAPI
from opentelemetry import metrics
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
from scalar_fastapi import get_scalar_api_reference
from starlette.middleware.cors import CORSMiddleware
from supertokens_python import get_all_cors_headers
from supertokens_python.framework.fastapi import get_middleware

from app.version import API_VERSION
from app.core.api.exception_handlers import register_exception_handlers
from app.core.api.streaming_multipart import install_streaming_multipart_openapi
from app.core.domain.errors import PayloadTooLargeError
from app.core.config import settings
from app.core.cors import get_allowed_cors_origin_regex, get_allowed_cors_origins
from app.core.infrastructure.events.message_bus import (
    close_message_bus,
    get_message_bus,
)
from app.core.infrastructure.db.session import close_engine
from app.core.infrastructure.jobs.streaq_job_queue import (
    close_streaq_job_queue,
    get_streaq_job_queue,
)
from app.core.security import verify_auth
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    initialize_supertokens,
)
from app.core.log.log import setup_logging, get_logger, validate_release_identity
from app.core.observability.telemetry import (
    init_telemetry,
    instrument_database_engine,
    instrument_fastapi_app,
    shutdown_telemetry,
)
from app.core.infrastructure.channels.channel_service import channel_service

from app.modules.apps.api.host_routing import AppHostRoutingMiddleware
from app.core.registry.assembly import enter_api_lifespans, include_module_routers
from app.core.registry.installed import OSS_MODULES
from app.auth_app import get_auth_app
from app.mcp_server import get_agent_mcp_app, get_pod_mcp_app
from app.core.infrastructure.db.session import get_engine
from app.core.request_context import (
    bind_request_context,
    create_background_task,
    create_inherited_task,
)

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
http_request_count = meter.create_counter("lemma.http.server.requests")
http_request_duration = meter.create_histogram("lemma.http.server.duration_ms")

OPENAPI_SCHEMA_RENAMES = {
    "fastapi___compat__v2__Body_file__upload": "DatastoreFileUploadRequest",
    "fastapi___compat__v2__Body_icon__upload": "IconUploadRequest",
    "fastapi___compat__v2__Body_app__bundle__upload": "AppBundleUploadRequest",
}


def _replace_openapi_refs(value: object, renames: dict[str, str]) -> object:
    if isinstance(value, Mapping):
        updated: dict[object, object] = {}
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                replacement = item
                for old_name, new_name in renames.items():
                    replacement = replacement.replace(
                        f"#/components/schemas/{old_name}",
                        f"#/components/schemas/{new_name}",
                    )
                updated[key] = replacement
            else:
                updated[key] = _replace_openapi_refs(item, renames)
        return updated
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_replace_openapi_refs(item, renames) for item in value]
    return value


_HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


def _apply_error_response_schema(schema: dict) -> dict:
    """Point every 4xx/5xx response at the unified ``ErrorResponse`` envelope.

    All error responses share ``{"message","code","request_id","details"}`` (see
    ``app.core.api.exception_handlers``). FastAPI documents the auto 422 as
    ``HTTPValidationError`` and per-route error responses ad hoc; rewrite them so
    the OpenAPI spec — and therefore the generated SDKs — matches what the server
    actually returns.
    """
    from app.core.api.schemas import ErrorResponse

    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components["ErrorResponse"] = ErrorResponse.model_json_schema()

    error_ref = {"$ref": "#/components/schemas/ErrorResponse"}
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, Mapping):
            continue
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, Mapping):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            for status_code, response in responses.items():
                try:
                    code_int = int(status_code)
                except TypeError, ValueError:
                    continue
                if code_int < 400 or not isinstance(response, dict):
                    continue
                response["content"] = {"application/json": {"schema": error_ref}}
    return schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        agent_mcp_app = getattr(app.state, "agent_mcp_app", None)
        if agent_mcp_app is not None:
            await stack.enter_async_context(agent_mcp_app.lifespan(app))
        pod_mcp_app = getattr(app.state, "pod_mcp_app", None)
        if pod_mcp_app is not None:
            await stack.enter_async_context(pod_mcp_app.lifespan(app))

        # Core startup
        from app.core.concurrency.offload import configure_thread_pool
        from app.core.observability.loop_watchdog import loop_lag_watchdog

        configure_thread_pool()
        watchdog_task = (
            None
            if getattr(app.state, "embedded_worker", False)
            else create_background_task(
                loop_lag_watchdog(service_name="lemma-api"),
                name="api-loop-lag-watchdog",
            )
        )
        initialize_supertokens()
        await channel_service.connect()
        await get_streaq_job_queue().connect()
        await get_message_bus().connect()
        started = False
        try:
            # Module-contributed API lifespans (e.g. datastore query-role
            # backfill on enter; surface-dedup + user-cache close on exit).
            # Entered after core startup so startup hooks can use core
            # resources, and unwound before the core closers below.
            async with AsyncExitStack() as module_stack:
                # The composed module list (OSS by default; lemma-cloud passes
                # CLOUD_MODULES) is stashed on app.state by create_app.
                modules = getattr(app.state, "lemma_modules", OSS_MODULES)
                await enter_api_lifespans(module_stack, modules, app)
                # Emit only after every core and module lifespan has entered.
                # service.version and release.sha come from LEMMA_RELEASE_SHA.
                logger.info("service.started")
                started = True
                yield
        finally:
            # Core closers — explicit and last so they tear down after modules.
            if started:
                logger.info("service.stopped")
            if watchdog_task is not None and not watchdog_task.done():
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except BaseException:
                    pass
            await close_streaq_job_queue()
            await close_message_bus()
            await close_engine()
            await channel_service.disconnect()
            from app.modules.datastore.infrastructure.session import (
                close_datastore_engine,
            )

            await close_datastore_engine()
            shutdown_telemetry()


class RequestObserverMiddleware:
    """Bind HTTP correlation, emit bounded terminal signals, and record metrics."""

    HEADER = b"x-request-id"
    REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
    SLOW_SECONDS = 2.0
    QUIET_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/livez"})

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers") or [])
        existing = next((v for k, v in headers if k.lower() == self.HEADER), None)
        inbound = existing.decode("latin-1") if existing is not None else ""
        if self.REQUEST_ID_RE.fullmatch(inbound):
            request_id = inbound
        else:
            request_id = uuid.uuid4().hex

        correlation_id = uuid.uuid7()
        scope = dict(scope)
        scope["headers"] = [
            (key, value) for key, value in headers if key.lower() != self.HEADER
        ] + [(self.HEADER, request_id.encode("ascii"))]
        scope.setdefault("state", {})

        started_at = time.perf_counter()
        response_started_at: float | None = None
        status_code = 500
        content_type = ""

        async def send_with_request_id(message):
            nonlocal response_started_at, status_code, content_type
            if message["type"] == "http.response.start":
                response_started_at = time.perf_counter()
                status_code = int(message.get("status", 500))
                raw_headers = [
                    (key, value)
                    for key, value in list(message.get("headers") or [])
                    if key.lower() != self.HEADER
                ]
                content_type = next(
                    (
                        value.decode("latin-1").lower()
                        for key, value in raw_headers
                        if key.lower() == b"content-type"
                    ),
                    "",
                )
                raw_headers.append((self.HEADER, request_id.encode("ascii")))
                message = {**message, "headers": raw_headers}
            await send(message)

        caught: Exception | None = None
        cancelled = False
        with bind_request_context(request_id=request_id, correlation_id=correlation_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as exc:
                caught = exc
                raise
            finally:
                finished_at = time.perf_counter()
                duration_ms = round((finished_at - started_at) * 1000, 1)
                route = self._route_template(scope)
                attributes = {
                    "http.request.method": str(scope.get("method", "UNKNOWN")),
                    "http.route": route,
                    "http.response.status_class": f"{status_code // 100}xx",
                }
                http_request_count.add(1, attributes)
                http_request_duration.record(duration_ms, attributes)

                if str(scope.get("path", "")) in self.QUIET_PATHS:
                    continue_logging = False
                else:
                    continue_logging = True
                if continue_logging and not cancelled:
                    state = scope.get("state") or {}
                    recorded = state.get("lemma_exception")
                    failure = caught or recorded
                    fields = {
                        "method": str(scope.get("method", "UNKNOWN")),
                        "route": route,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    }
                    if status_code >= 500 or caught is not None:
                        fields["error_type"] = state.get(
                            "lemma_error_type",
                            type(failure).__name__ if failure else "HTTPError",
                        )
                        fields["error_code"] = state.get(
                            "lemma_error_code", "INTERNAL_ERROR"
                        )
                        exc_info = (
                            (type(failure), failure, failure.__traceback__)
                            if isinstance(failure, BaseException)
                            else None
                        )
                        logger.error(
                            "http.request.failed",
                            method=fields["method"],
                            route=fields["route"],
                            status_code=fields["status_code"],
                            duration_ms=fields["duration_ms"],
                            error_type=fields["error_type"],
                            error_code=fields["error_code"],
                            exc_info=exc_info,
                        )
                    elif status_code == 429:
                        logger.warning(
                            "http.request.rate_limited",
                            method=fields["method"],
                            route=fields["route"],
                            status_code=fields["status_code"],
                            duration_ms=fields["duration_ms"],
                        )
                    else:
                        streaming = content_type.startswith("text/event-stream")
                        elapsed = (
                            (response_started_at - started_at)
                            if streaming and response_started_at is not None
                            else (finished_at - started_at)
                        )
                        if elapsed >= self.SLOW_SECONDS:
                            fields["duration_ms"] = round(elapsed * 1000, 1)
                            fields["latency_kind"] = (
                                "time_to_first_byte" if streaming else "total"
                            )
                            logger.warning(
                                "http.request.slow",
                                method=fields["method"],
                                route=fields["route"],
                                status_code=fields["status_code"],
                                duration_ms=fields["duration_ms"],
                                latency_kind=fields["latency_kind"],
                            )
                        else:
                            logger.debug(
                                "http.request.completed",
                                method=fields["method"],
                                route=fields["route"],
                                status_code=fields["status_code"],
                                duration_ms=fields["duration_ms"],
                            )

    @staticmethod
    def _route_template(scope: dict) -> str:
        route = scope.get("route")
        value = getattr(route, "path_format", None) or getattr(route, "path", None)
        return value if isinstance(value, str) else "unmatched"


# Compatibility name retained for imports and generated SDK tests.
RequestIdMiddleware = RequestObserverMiddleware


class RequestBodyLimitMiddleware:
    """Enforce a byte ceiling without trusting the Content-Length header."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or None
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._send_too_large(scope, receive, send, request_id)
                    return
            except ValueError:
                pass

        received = 0

        async def receive_limited():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise PayloadTooLargeError(max_bytes=self.max_bytes)
            return message

        try:
            await self.app(scope, receive_limited, send)
        except PayloadTooLargeError:
            await self._send_too_large(scope, receive, send, request_id)

    async def _send_too_large(self, scope, receive, send, request_id):
        response = JSONResponse(
            status_code=413,
            content={
                "message": "request exceeds the maximum allowed size",
                "code": "UPLOAD_TOO_LARGE",
                "request_id": request_id,
                "details": {"field": "request", "max_bytes": self.max_bytes},
            },
        )
        await response(scope, receive, send)


def create_app(modules=OSS_MODULES) -> FastAPI:
    """Factory function to create a new FastAPI app instance.

    ``modules`` is the composed module list to mount. It defaults to
    ``OSS_MODULES``; lemma-cloud calls ``create_app(CLOUD_MODULES)`` to add
    billing/admin. The list is stashed on ``app.state`` so the module-level
    lifespan (which only receives ``app``) can enter the same modules' hooks.
    """
    setup_logging(
        settings.environment,
        service_name="lemma-api",
        json_logs=settings.json_logs_enabled,
        log_level=settings.log_level,
    )
    validate_release_identity(settings.environment)
    app = FastAPI(
        title=settings.app_name,
        description="Authentication API with JWT, user management, and OAuth support",
        version=API_VERSION,
        debug=settings.debug,
        lifespan=lifespan,
        dependencies=[Depends(verify_auth)],
        redirect_slashes=False,
        separate_input_output_schemas=False,
    )
    app.state.lemma_modules = modules

    # Global error handling — every error response uses one envelope
    # ({"message","code","request_id","details"}). Domain errors translate automatically via
    # their status_code/code, so controllers don't catch-and-remap them.
    register_exception_handlers(app)

    class TrailingSlashMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            path = scope.get("path", "")
            if path != "/" and path.endswith("/"):
                scope = dict(scope)
                scope["path"] = path.rstrip("/")

            await self.app(scope, receive, send)

    init_telemetry(service_name="lemma-api")
    instrument_database_engine(get_engine())

    # Auth App for SuperTokens (mounted at /st to match legacy config)
    # The middleware gets added to the specific app handling the requests
    auth_app = get_auth_app()
    instrument_fastapi_app(auth_app)
    app.mount("/st", auth_app)
    agent_mcp_app = get_agent_mcp_app()
    app.state.agent_mcp_app = agent_mcp_app
    app.mount("/agent-runtime/conversations", agent_mcp_app)
    pod_mcp_app = get_pod_mcp_app()
    app.state.pod_mcp_app = pod_mcp_app
    app.mount("/agent-runtime/pods", pod_mcp_app)

    # Middleware
    # SuperTokens middleware might not be needed on main app if all auth routes are in sub-app?
    # BUT request verification (session verifying) happens on main endpoints.
    # Therefore, we MUST add get_middleware() to the main app as well for session verification.
    app.add_middleware(TrailingSlashMiddleware)

    app.add_middleware(get_middleware())

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_allowed_cors_origins(),
        allow_origin_regex=get_allowed_cors_origin_regex(),
        allow_credentials=True,
        allow_methods=["GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"],
        # X-Lemma-Client is sent by the browser SDK on every request; it must be
        # allowed or the browser blocks the (preflighted) call as a CORS error.
        allow_headers=["Content-Type", "Authorization", "X-Lemma-Client"]
        + get_all_cors_headers(),
        # Let browser SDK clients read the correlation id off the response.
        # SuperTokens sets `front-token`/`anti-csrf` (and the `st-*` token pair in
        # header-based auth mode) as expose headers per-response, but this outer
        # CORSMiddleware wraps everything (including the /st mount) and Starlette's
        # `headers.update` REPLACES Access-Control-Expose-Headers — so we must list
        # them here or the front-token gets clobbered and the SDK can't read it.
        expose_headers=[
            "X-Request-Id",
            "front-token",
            "anti-csrf",
            "st-access-token",
            "st-refresh-token",
        ],
    )

    # Host-based app serving: rewrite `<slug>.<app_base_domain>` requests onto
    # the public app asset endpoint. Outermost so the slug is resolved before
    # routing/auth (the rewritten /public/* path is unauthenticated).
    app.add_middleware(AppHostRoutingMiddleware)

    # Transport-level guard. Added before RequestIdMiddleware so the latter
    # remains outermost and stamps 413 responses with the correlation id.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
    )

    # Correlation id — added last so it is the outermost middleware and stamps
    # every response (including app-host-routed ones).
    app.add_middleware(RequestIdMiddleware)

    # Routers — registered from the module registry (app/core/registry).
    # Order follows OSS_MODULES; intra-module order follows each module's
    # routers() thunk. See app/modules/<name>/module.py.
    include_module_routers(app, modules)

    # Liveness: process/event-loop check only. No DB or network dependency, so
    # it normally completes within ~100 ms. 503 when the event loop is wedged
    # (lag over the unhealthy threshold), so a liveness probe restarts the
    # process. A fully blocked loop can't serve this at all, which trips the
    # probe's timeout — either way a hung process is restarted instead of
    # hanging silently.
    @app.get("/health/live", include_in_schema=False)
    @app.get("/livez", include_in_schema=False)
    async def health_live():
        from app.core.observability.loop_watchdog import (
            get_loop_lag_seconds,
            is_loop_healthy,
        )

        healthy = is_loop_healthy()
        payload = {
            "status": "ok" if healthy else "unhealthy",
            "loop_lag_seconds": round(get_loop_lag_seconds(), 3),
        }
        return JSONResponse(payload, status_code=200 if healthy else 503)

    # Readiness: bounded, concurrent checks for dependencies required to serve
    # new work. Each check has ~1 s; the whole endpoint has a ~2 s deadline.
    # 503 when not ready; only generic component states are exposed, never
    # connection strings or provider responses.
    @app.get("/health/ready", include_in_schema=False)
    async def health_ready():
        import asyncio as _asyncio

        from sqlalchemy import text

        async def _db_ok() -> bool:
            try:
                engine = get_engine()
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                return True
            except Exception:
                return False

        async def _redis_ok() -> bool:
            try:
                return await channel_service.ping()
            except Exception:
                return False

        async def _with_timeout(coro, seconds: float) -> bool:
            try:
                return await _asyncio.wait_for(coro, timeout=seconds)
            except Exception:
                return False

        # Run dependency checks concurrently; each is individually bounded and
        # the pair is bounded by the overall gather timeout.
        db_task = redis_task = None
        try:
            db_task = create_inherited_task(_with_timeout(_db_ok(), 1.0))
            redis_task = create_inherited_task(_with_timeout(_redis_ok(), 1.0))
            db_ok, redis_ok = await _asyncio.wait_for(
                _asyncio.gather(db_task, redis_task), timeout=2.0
            )
        except Exception:
            db_ok, redis_ok = False, False
        finally:
            for t in (db_task, redis_task):
                if t is not None and not t.done():
                    t.cancel()

        components = {
            "db": "ok" if db_ok else "down",
            "redis": "ok" if redis_ok else "down",
        }
        ready = bool(db_ok) and bool(redis_ok)
        payload = {
            "status": "ready" if ready else "not_ready",
            "components": components,
        }
        return JSONResponse(payload, status_code=200 if ready else 503)

    # Compatibility alias for /health/live during probe migration.
    @app.get("/health", include_in_schema=False)
    async def health_alias():
        from app.core.observability.loop_watchdog import (
            get_loop_lag_seconds,
            is_loop_healthy,
        )

        healthy = is_loop_healthy()
        payload = {
            "status": "ok" if healthy else "unhealthy",
            "loop_lag_seconds": round(get_loop_lag_seconds(), 3),
        }
        return JSONResponse(payload, status_code=200 if healthy else 503)

    @app.get("/scalar", include_in_schema=False)
    async def scalar_html():
        return get_scalar_api_reference(
            # Your OpenAPI document
            openapi_url=app.openapi_url,
            # authentication={"preferredSecurityScheme": "HTTPBearer"},
            persist_auth=True,
        )

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
            description=app.description,
        )
        schema = _replace_openapi_refs(schema, OPENAPI_SCHEMA_RENAMES)
        schema = install_streaming_multipart_openapi(schema)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for old_name, new_name in OPENAPI_SCHEMA_RENAMES.items():
            if old_name in components:
                component = components.pop(old_name)
                if isinstance(component, dict) and not component.get("title"):
                    component["title"] = new_name
                components[new_name] = component

        # Unify error responses on the ErrorResponse envelope.
        schema = _apply_error_response_schema(schema)

        # x-lemma metadata spine for SDK codegen (Wave 3, CG-4).
        from app.core.openapi_extensions import apply_lemma_metadata

        schema = apply_lemma_metadata(schema)

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi
    instrument_fastapi_app(app)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        access_log=False,
    )
