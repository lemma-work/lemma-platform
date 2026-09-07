import asyncio
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import Depends, FastAPI
from opentelemetry import metrics
from fastapi.openapi.utils import get_openapi
from scalar_fastapi import get_scalar_api_reference
from starlette.middleware.cors import CORSMiddleware
from supertokens_python import get_all_cors_headers
from supertokens_python.framework.fastapi import get_middleware

from app.version import API_VERSION
from app.core.api.session_cookie_scope import RefreshCookieScopeMiddleware
from app.core.api.exception_handlers import register_exception_handlers
from app.core.api.streaming_multipart import install_streaming_multipart_openapi
from app.core.config import settings
from app.health import router as health_router
from app.middleware import (
    RequestBodyLimitMiddleware,
    RequestIdMiddleware,
    TrailingSlashMiddleware,
)
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
from app.core.infrastructure.jobs.streaq_runtime import ensure_task_lanes_registered
from app.core.infrastructure.cache.redis_json_cache import close_redis_json_caches
from app.core.infrastructure.redis.client import close_redis_clients
from app.core.security import verify_auth
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    initialize_supertokens,
)
from app.modules.identity.infrastructure.supertokens_auth.abuse_middleware import (
    AuthAbuseMiddleware,
)
from app.core.log.log import setup_logging, get_logger, validate_release_identity
from app.core.observability.telemetry import (
    init_telemetry,
    instrument_database_engine,
    instrument_fastapi_app,
    shutdown_telemetry,
)
from app.sandbox_health import record_sandbox_probe
from app.core.infrastructure.channels.channel_service import channel_service

from app.modules.apps.api.host_routing import AppHostRoutingMiddleware
from app.core.registry.assembly import enter_api_lifespans, include_module_routers
from app.core.registry.installed import OSS_MODULES
from app.auth_app import get_auth_app
from app.mcp_server import get_agent_mcp_app, get_pod_mcp_app
from app.core.infrastructure.db.session import get_engine
from app.core.request_context import (
    create_background_task,
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
        from app.core.analytics.bootstrap import start_analytics
        from app.core.concurrency.offload import configure_thread_pool, run_blocking
        from app.core.observability.connection_scope import (
            start_connection_scope_monitor_from_settings,
            stop_connection_scope_monitor,
        )
        from app.core.observability.loop_watchdog import loop_lag_watchdog
        from app.core.observability.memory_sampler import memory_sampler

        configure_thread_pool()
        start_connection_scope_monitor_from_settings(service_name="lemma-api")
        # Installs a null sink unless ANALYTICS_WRITE_KEY is set, so a
        # self-hosted or Desktop-local process reports nothing.
        start_analytics()
        embedded_worker = getattr(app.state, "embedded_worker", False)
        watchdog_task = (
            None
            if embedded_worker
            else create_background_task(
                loop_lag_watchdog(service_name="lemma-api"),
                name="api-loop-lag-watchdog",
            )
        )
        # Skipped under an embedded worker for the same reason as the watchdog:
        # the worker runtime starts its own, and two samplers in one process
        # would report the same resident memory twice under two service names.
        memory_task = (
            None
            if embedded_worker
            else create_background_task(
                memory_sampler(service_name="lemma-api"),
                name="api-memory-sampler",
            )
        )
        initialize_supertokens()
        # Prove the sandbox fabric is usable before a user's first tool call
        # rather than after it. Off the loop because it stats a socket path.
        await run_blocking(record_sandbox_probe, limiter="cpu_bound")
        # Build the OpenAPI document now rather than on whichever request first
        # asks for it. `custom_openapi` caches correctly, but the first call
        # costs ~3.35s of pydantic model-graph construction on the event loop —
        # the api's loop-stall sampler caught it in
        # `fastapi._compat.get_flat_models_from_fields`, and it blocks every
        # concurrent request when it lands. Off the loop because it is pure CPU.
        #
        # Only where the document is actually served. In production it is not,
        # so this whole cost leaves the cold start rather than moving within it.
        if settings.api_docs_served():
            await run_blocking(app.openapi, limiter="cpu_bound")
        # Learn which lane each task runs on before serving traffic. The
        # enqueue path resolves this lazily as a safety net, but the first
        # resolution imports every module's handlers — half a second that
        # would otherwise land on whichever request first enqueues a job.
        # The composed list, not OSS: lemma-cloud installs more modules, and a
        # cloud-only task missing here would be enqueued to the wrong lane.
        ensure_task_lanes_registered(getattr(app.state, "lemma_modules", OSS_MODULES))
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
            for lifecycle_task in (watchdog_task, memory_task):
                if lifecycle_task is not None and not lifecycle_task.done():
                    lifecycle_task.cancel()
                    try:
                        await lifecycle_task
                    except asyncio.CancelledError:
                        # The expected path: we just cancelled it. Swallowed
                        # rather than re-raised because this is a `finally` and
                        # every closer below still has to run.
                        pass
                    except BaseException:
                        # Anything else is the sampler failing on its own way
                        # out. Still swallowed, for the same reason -- a broken
                        # diagnostic must not take the shutdown with it -- but
                        # not silently: a bare `pass` here is how a sampler that
                        # has been dying at every shutdown for months goes
                        # unnoticed.
                        logger.warning(
                            "runtime.lifecycle_task.shutdown_failed.degraded",
                            task=getattr(lifecycle_task.get_coro(), "__name__", "?"),
                            exc_info=True,
                        )
            # Before the shared HTTP client closes below: the sink delivers
            # what it has buffered on the way out.
            from app.core.analytics.bootstrap import stop_analytics

            await stop_analytics()
            await close_streaq_job_queue()
            await close_message_bus()
            # Outbound connector plumbing: the shared HTTP pool and any engines
            # opened against customer databases. Closed explicitly so a reload
            # does not leak sockets into the next process.
            from app.core.net.http_client import close_shared_http_client
            from app.core.net.impersonating_client import close_impersonating_client
            from app.modules.agent.services.runtime_model_factory import (
                close_agent_provider_clients,
            )
            from app.modules.connectors.infrastructure.adapters.sql_executor import (
                dispose_shared_sql_engines,
            )

            await close_shared_http_client()
            # The separate libcurl session `web_fetch` reads pages through.
            await close_impersonating_client()
            # Per-endpoint LLM provider pools, kept alive across runs for
            # connection reuse.
            await close_agent_provider_clients()
            await dispose_shared_sql_engines()
            await close_redis_json_caches()
            # Symmetric with the monitor started at startup: a module
            # singleton that nothing stopped, which outlives an in-process
            # lifespan and attaches to whatever engines come next.
            stop_connection_scope_monitor()
            await close_redis_clients()
            await close_engine()
            await channel_service.disconnect()
            from app.modules.datastore.infrastructure.session import (
                close_datastore_engine,
            )

            await close_datastore_engine()
            shutdown_telemetry()


#: The route label for a request that reached no FastAPI route. It is a real
#: outcome (a 404, a sub-app mount, an error before routing), not an error --
#: but it is also not an identity, which is why the raw path rides along on the
#: signals that matter.


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
    # Production serves no API documentation. Building the document costs ~3.35s
    # of a cold start (second only to the imports), nothing in production reads
    # it -- both SDKs are generated at build time and the route inventory is a CI
    # gate -- and the endpoints are unauthenticated, so serving them publishes
    # the shape of every route to anyone who asks. `API_DOCS_ENABLED` overrides
    # in either direction.
    docs_served = settings.api_docs_served()
    app = FastAPI(
        title=settings.app_name,
        description="Authentication API with JWT, user management, and OAuth support",
        version=API_VERSION,
        debug=settings.debug,
        lifespan=lifespan,
        dependencies=[Depends(verify_auth)],
        redirect_slashes=False,
        separate_input_output_schemas=False,
        openapi_url="/openapi.json" if docs_served else None,
        docs_url="/docs" if docs_served else None,
        redoc_url="/redoc" if docs_served else None,
    )
    app.state.lemma_modules = modules

    # Global error handling — every error response uses one envelope
    # ({"message","code","request_id","details"}). Domain errors translate automatically via
    # their status_code/code, so controllers don't catch-and-remap them.
    register_exception_handlers(app)

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

    # Apply the shared 60/minute/IP auth ceiling to custom /auth routes. The
    # mounted SuperTokens app applies the same key itself, so non-auth APIs and
    # /st requests are deliberately skipped here.
    app.add_middleware(AuthAbuseMiddleware, auth_paths_only=True)

    # Transport-level guard, and the ceiling on how much body the abuse
    # middleware below it can be made to buffer -- so it is registered after
    # that one, which puts it outside it.
    #
    # Registered *before* CORS, which puts it inside: `add_middleware` prepends,
    # so the last registration is outermost. This middleware writes its own 413
    # rather than raising, and a response that never passes through
    # `CORSMiddleware` carries no `Access-Control-Allow-Origin` -- which the
    # browser reports to the page as a CORS failure, withholding the status and
    # the body, so an oversized upload was indistinguishable from a broken
    # origin and the `max_bytes` in the envelope reached nobody.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_allowed_cors_origins(),
        allow_origin_regex=get_allowed_cors_origin_regex(),
        allow_credentials=True,
        allow_methods=["GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"],
        # X-Lemma-Client is sent by the browser SDK on every request; it must be
        # allowed or the browser blocks the (preflighted) call as a CORS error.
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Lemma-Client",
            "X-Lemma-App",
            "x-altcha-payload",
        ]
        + get_all_cors_headers(),
        # Let browser SDK clients read the correlation id off the response.
        # SuperTokens sets `front-token`/`anti-csrf` (and the `st-*` token pair in
        # header-based auth mode) as expose headers per-response, but this outer
        # CORSMiddleware wraps everything (including the /st mount) and Starlette's
        # `headers.update` REPLACES Access-Control-Expose-Headers — so we must list
        # them here or the front-token gets clobbered and the SDK can't read it.
        expose_headers=[
            "X-Request-Id",
            "Retry-After",
            "front-token",
            "anti-csrf",
            "st-access-token",
            "st-refresh-token",
        ],
    )

    # Sits outside the auth routes so it sees their Set-Cookie headers. No-op
    # unless apps call the API on their own origin; see the module docstring.
    app.add_middleware(RefreshCookieScopeMiddleware)

    # Host-based app serving: rewrite `<slug>.<app_base_domain>` requests onto
    # the public app asset endpoint. Outermost so the slug is resolved before
    # routing/auth (the rewritten /public/* path is unauthenticated).
    app.add_middleware(AppHostRoutingMiddleware)

    # Correlation id — added last so it is the outermost middleware and stamps
    # every response (including app-host-routed ones).
    app.add_middleware(RequestIdMiddleware)

    # Routers — registered from the module registry (app/core/registry).
    # Order follows OSS_MODULES; intra-module order follows each module's
    # routers() thunk. See app/modules/<name>/module.py.
    # Before the modules: probes must answer even if a module's router raises
    # while being included, which is exactly when a readiness probe is worth
    # having.
    app.include_router(health_router)
    include_module_routers(app, modules)

    # Registered only alongside the document it renders. Left on with
    # `openapi_url=None` it would serve a reference UI pointed at nothing.
    if docs_served:

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
