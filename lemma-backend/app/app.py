import uuid
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.openapi.utils import get_openapi
from scalar_fastapi import get_scalar_api_reference
from starlette.middleware.cors import CORSMiddleware
from supertokens_python import get_all_cors_headers
from supertokens_python.framework.fastapi import get_middleware

from app.version import API_VERSION
from app.core.api.exception_handlers import register_exception_handlers
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
from app.core.log.log import setup_logging, get_logger
from app.core.observability.telemetry import (
    init_telemetry,
    instrument_database_engine,
    instrument_fastapi_app,
)
from app.core.infrastructure.channels.channel_service import channel_service

from app.modules.apps.api.host_routing import AppHostRoutingMiddleware
from app.core.registry.assembly import enter_api_lifespans, include_module_routers
from app.core.registry.installed import OSS_MODULES
from app.auth_app import get_auth_app
from app.mcp_server import get_agent_mcp_app, get_pod_mcp_app
from app.core.infrastructure.db.session import get_engine

logger = get_logger(__name__)

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

    All error responses share ``{"message","code","details"}`` (see
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
                except (TypeError, ValueError):
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
        logger.info("Application starting up")
        initialize_supertokens()
        await channel_service.connect()
        await get_streaq_job_queue().connect()
        await get_message_bus().connect()
        logger.info("Redis broker started")

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
                yield
        finally:
            # Core closers — explicit and last so they tear down after modules.
            logger.info("Application shutting down")
            await close_streaq_job_queue()
            await close_message_bus()
            await close_engine()
            await channel_service.disconnect()
            from app.modules.datastore.infrastructure.session import close_datastore_engine

            await close_datastore_engine()


class RequestIdMiddleware:
    """Ensure every request carries an ``x-request-id``: reuse an inbound one or
    mint a new one, make it visible to downstream handlers (the authz layer reads
    it), and echo it on the response so clients/agents can quote it in reports."""

    HEADER = b"x-request-id"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        existing = next((v for k, v in headers if k == self.HEADER), None)
        if existing is not None:
            request_id = existing.decode("latin-1")
        else:
            request_id = uuid.uuid4().hex
            # Inject into request headers so request.headers.get("x-request-id")
            # (read by the authz layer) sees the minted id.
            scope = dict(scope)
            scope["headers"] = [*headers, (self.HEADER, request_id.encode("latin-1"))]

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers") or [])
                if not any(k.lower() == self.HEADER for k, _ in raw_headers):
                    raw_headers.append((self.HEADER, request_id.encode("latin-1")))
                message = {**message, "headers": raw_headers}
            await send(message)

        await self.app(scope, receive, send_with_request_id)


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
    # ({"message","code","details"}). Domain errors translate automatically via
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

    # Correlation id — added last so it is the outermost middleware and stamps
    # every response (including app-host-routed ones).
    app.add_middleware(RequestIdMiddleware)

    # Routers — registered from the module registry (app/core/registry).
    # Order follows OSS_MODULES; intra-module order follows each module's
    # routers() thunk. See app/modules/<name>/module.py.
    include_module_routers(app, modules)

    # Health check
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "message": "API is running"}

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

    uvicorn.run("app.app:app", host="0.0.0.0", port=8000, reload=True)
