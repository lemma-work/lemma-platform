from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hmac
import re
import time
import uuid
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agentbox.adapters.docker import (
    DockerAdapterConfig,
    DockerSandboxAdapter,
    RuntimeCredentialSigner,
)
from agentbox.adapters.docker_engine import DockerEngineClient
from agentbox.config import settings
from agentbox.domain import (
    AgentBoxError,
    ProviderAdmissionPolicy,
    SandboxCapability,
    SandboxProfileRef,
    WorkloadKind,
)
from agentbox.filesystem import FilesystemService
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.maintenance import SandboxMaintenanceWorker, maintenance_loop
from agentbox.port_access import PortAccessService, PortAccessSigner
from agentbox.persistence.uow import StateDatabase
from agentbox.processes import ProcessExecutionService
from agentbox.profiles import (
    DockerProfileArtifact,
    E2BProfileArtifact,
    ProfileRegistry,
    SandboxProfile,
)
from agentbox.python_sessions import PythonSessionService
from agentbox.observability import bind_context, get_logger
from agentbox.observability import create_background_task
from agentbox.reconciliation import AgentBoxReconciler, reconciliation_loop

from .fabric import agentbox_error_response, router
from .port_proxy import access_router, create_port_proxy_http_client


logger = get_logger(__name__)
_QUIET_HEALTH_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/livez"})


class RequestContextMiddleware:
    """Bind trusted request lineage and emit one redacted request outcome."""

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
                        "error": {
                            "code": "INTERNAL",
                            "message": "Internal server error",
                            "retry": "do_not_retry",
                            "retry_after_ms": None,
                            "context": None,
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
                        method = str(scope.get("method", "UNKNOWN"))
                        route_name = str(route)
                        elapsed_ms = round(elapsed * 1000, 1)
                        if elapsed >= self.SLOW_SECONDS:
                            latency_kind = (
                                "time_to_first_byte" if streaming else "total"
                            )
                            logger.warning(
                                "http.request.slow",
                                method=method,
                                route=route_name,
                                status_code=status_code,
                                duration_ms=elapsed_ms,
                                latency_kind=latency_kind,
                            )
                        else:
                            logger.debug(
                                "http.request.completed",
                                method=method,
                                route=route_name,
                                status_code=status_code,
                                duration_ms=elapsed_ms,
                            )


def _database_url() -> str:
    if settings.agentbox_state_database_url:
        return settings.agentbox_state_database_url
    return f"sqlite+aiosqlite:///{settings.agentbox_state_db_path}"


def _profiles() -> ProfileRegistry:
    workspace_e2b = (
        E2BProfileArtifact(
            template_id=settings.agentbox_e2b_workspace_template,
            build_id=settings.agentbox_e2b_workspace_build_id,
        )
        if settings.agentbox_e2b_workspace_template
        and settings.agentbox_e2b_workspace_build_id
        else None
    )
    workspace = SandboxProfile(
        ref=SandboxProfileRef(
            settings.agentbox_workspace_profile_name,
            settings.agentbox_workspace_profile_digest,
        ),
        workload_kind=WorkloadKind.WORKSPACE,
        # The portable ABI is semantic; every provider runs the profile-owned
        # Python 3.14 environment behind its native session implementation.
        runtime_abi="lemma-workspace-python-1-node-24",
        capabilities=frozenset(
            {
                SandboxCapability.PROCESS,
                SandboxCapability.PTY,
                SandboxCapability.PYTHON_SESSION,
                SandboxCapability.FILESYSTEM,
                SandboxCapability.PORT_ACCESS,
                SandboxCapability.BROWSER,
            }
        ),
        allowed_roots=("/workspace", "/tmp"),
        docker=DockerProfileArtifact(
            image=settings.agentbox_workspace_image,
            command=(),
            readiness_argv=(),
            published_ports=(8080, 4848),
            runtime_port=8080,
        ),
        e2b=workspace_e2b,
    )
    function_e2b = (
        E2BProfileArtifact(
            template_id=settings.agentbox_e2b_function_template,
            build_id=settings.agentbox_e2b_function_build_id,
        )
        if settings.agentbox_e2b_function_template
        and settings.agentbox_e2b_function_build_id
        else None
    )
    function = SandboxProfile(
        ref=SandboxProfileRef(
            settings.agentbox_function_profile_name,
            settings.agentbox_function_profile_digest,
        ),
        workload_kind=WorkloadKind.FUNCTION,
        runtime_abi="lemma-function-python-3.14-linux-x86_64-1",
        capabilities=frozenset({SandboxCapability.PORT_ACCESS}),
        allowed_roots=("/tmp",),
        docker=DockerProfileArtifact(
            image=settings.agentbox_function_image,
            command=(
                "lemma-function-runtime",
                "serve",
                "--host",
                "0.0.0.0",
                "--port",
                "8090",
            ),
            readiness_argv=(),
            published_ports=(8090,),
            runtime_port=8090,
        ),
        e2b=function_e2b,
    )
    return ProfileRegistry((workspace, function))


def _runtime_key() -> bytes:
    configured = (settings.agentbox_runtime_credential_key or "").encode()
    if len(configured) < 32:
        raise RuntimeError(
            "AGENTBOX_RUNTIME_CREDENTIAL_KEY must be configured with at least "
            "32 bytes; it must be stable across replicas and restarts"
        )
    return configured


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = StateDatabase(_database_url())
    if settings.agentbox_auto_create_schema:
        await database.create_schema_for_test()
    profiles = _profiles()
    if settings.agentbox_provider == "docker":
        engine = DockerEngineClient(socket_path=settings.agentbox_docker_socket_path)
        provider = DockerSandboxAdapter(
            engine,
            profiles,
            DockerAdapterConfig(
                scope=settings.agentbox_docker_scope,
                allow_mutable_images=settings.agentbox_docker_allow_mutable_images,
                add_host_gateway=settings.agentbox_add_host_gateway,
                host_alias=settings.agentbox_host_alias,
                private_network=settings.agentbox_docker_private_network,
                memory_bytes=settings.agentbox_docker_workspace_memory_bytes,
                nano_cpus=settings.agentbox_docker_workspace_nano_cpus,
                function_memory_bytes=(settings.agentbox_docker_function_memory_bytes),
                function_nano_cpus=settings.agentbox_docker_function_nano_cpus,
                max_file_transfer_bytes=settings.agentbox_max_file_transfer_bytes,
            ),
            runtime_credentials=RuntimeCredentialSigner(_runtime_key()),
        )
    elif settings.agentbox_provider == "lemma_local":
        from agentbox.adapters.lemma_local import (
            LemmaLocalAdapterConfig,
            LemmaLocalSandboxAdapter,
        )

        provider = LemmaLocalSandboxAdapter(
            profiles,
            LemmaLocalAdapterConfig(
                executable=settings.agentbox_local_runtime_cli,
                scope=settings.agentbox_local_scope,
                request_timeout_seconds=(
                    settings.agentbox_local_runtime_timeout_seconds
                ),
                workspace_memory=settings.agentbox_local_workspace_memory,
                workspace_cpus=settings.agentbox_local_workspace_cpus,
                function_memory=settings.agentbox_local_function_memory,
                function_cpus=settings.agentbox_local_function_cpus,
                callback_required=settings.agentbox_local_callback_required,
                callback_url=settings.agentbox_local_callback_url,
                callback_health_path=settings.agentbox_local_callback_health_path,
                callback_timeout_seconds=(
                    settings.agentbox_local_callback_timeout_seconds
                ),
            ),
            RuntimeCredentialSigner(_runtime_key()),
        )
    elif settings.agentbox_provider == "e2b":
        from agentbox.adapters.e2b import E2BAdapterConfig, E2BSandboxAdapter

        if settings.agentbox_e2b_api_key is None:
            raise RuntimeError("E2B_API_KEY is required for the E2B provider")
        if (
            settings.agentbox_e2b_workspace_template is None
            or settings.agentbox_e2b_workspace_build_id is None
            or settings.agentbox_e2b_function_template is None
            or settings.agentbox_e2b_function_build_id is None
        ):
            raise RuntimeError(
                "E2B workspace/function template IDs and immutable build IDs are required"
            )
        provider = E2BSandboxAdapter(
            profiles,
            E2BAdapterConfig(
                api_key=settings.agentbox_e2b_api_key,
                scope=settings.agentbox_e2b_scope,
                request_timeout_seconds=(settings.agentbox_e2b_request_timeout_seconds),
                function_allow_out=(settings.agentbox_e2b_function_allow_out_hosts),
                max_file_transfer_bytes=settings.agentbox_max_file_transfer_bytes,
            ),
        )
    else:
        raise RuntimeError(
            f"unsupported AgentBox provider: {settings.agentbox_provider!r}"
        )
    app.state.database = database
    app.state.provider = provider
    admission_policy = ProviderAdmissionPolicy(
        max_active=settings.agentbox_provider_max_active,
        create_rate_per_second=(settings.agentbox_provider_create_rate_per_second),
        create_burst=settings.agentbox_provider_create_burst,
        interactive_capacity_reserve=(
            settings.agentbox_provider_interactive_capacity_reserve
        ),
        latency_capacity_reserve=(settings.agentbox_provider_latency_capacity_reserve),
    )
    lifecycle = SandboxLifecycleService(
        database,
        provider,
        admission_policy,
        workspace_retention_seconds=settings.agentbox_workspace_retention_seconds,
    )
    app.state.sandbox_lifecycle = lifecycle
    app.state.process_execution = ProcessExecutionService(database, provider)
    app.state.filesystem = FilesystemService(
        database,
        provider,
        max_transfer_bytes=settings.agentbox_max_file_transfer_bytes,
    )
    app.state.python_sessions = PythonSessionService(database, provider)
    app.state.port_access = PortAccessService(
        database,
        provider,
        PortAccessSigner(_runtime_key()),
        public_base_url=(settings.agentbox_public_url or settings.agentbox_api_url),
    )
    app.state.port_proxy_http_client = create_port_proxy_http_client()
    reconciler = AgentBoxReconciler(
        database,
        provider,
        create_absence_grace_seconds=(
            settings.agentbox_ambiguous_create_absence_grace_seconds
        ),
        claim_seconds=settings.agentbox_reconcile_claim_seconds,
        retry_seconds=max(0.1, settings.agentbox_reconcile_interval_seconds),
    )
    app.state.reconciler = reconciler
    await reconciler.reconcile_once(
        deadline_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.agentbox_reconcile_operation_timeout_seconds)
    )
    app.state.reconciliation_task = create_background_task(
        reconciliation_loop(
            reconciler,
            interval_seconds=settings.agentbox_reconcile_interval_seconds,
            operation_timeout_seconds=(
                settings.agentbox_reconcile_operation_timeout_seconds
            ),
        ),
        name="agentbox-reconciliation",
    )
    maintenance = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=settings.agentbox_workspace_idle_seconds,
        function_idle_seconds=settings.agentbox_function_idle_seconds,
    )
    app.state.maintenance = maintenance
    app.state.maintenance_task = create_background_task(
        maintenance_loop(
            maintenance,
            interval_seconds=settings.agentbox_cleanup_interval_seconds,
            operation_timeout_seconds=(
                settings.agentbox_reconcile_operation_timeout_seconds
            ),
        ),
        name="agentbox-maintenance",
    )
    try:
        yield
    finally:
        app.state.reconciliation_task.cancel()
        app.state.maintenance_task.cancel()
        await asyncio.gather(
            app.state.reconciliation_task,
            app.state.maintenance_task,
            return_exceptions=True,
        )
        await app.state.port_proxy_http_client.aclose()
        await provider.close()
        await database.dispose()


app = FastAPI(title="AgentBox", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(router)
app.include_router(access_router)


@app.exception_handler(AgentBoxError)
async def handle_agentbox_error(
    _request: Request, error: AgentBoxError
) -> JSONResponse:
    return agentbox_error_response(error)


@app.get("/health/live")
@app.get("/livez")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health")
@app.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    database = getattr(request.app.state, "database", None)
    provider = getattr(request.app.state, "provider", None)
    reconciliation_task = getattr(request.app.state, "reconciliation_task", None)
    maintenance_task = getattr(request.app.state, "maintenance_task", None)
    components = {
        "database": "ready",
        "provider": "ready" if provider is not None else "unavailable",
        "reconciler": (
            "ready"
            if reconciliation_task is not None and not reconciliation_task.done()
            else "unavailable"
        ),
        "maintenance": (
            "ready"
            if maintenance_task is not None and not maintenance_task.done()
            else "unavailable"
        ),
    }
    if database is None:
        components["database"] = "unavailable"
    else:
        try:
            await asyncio.wait_for(database.healthcheck(), timeout=0.75)
        except Exception:
            components["database"] = "unavailable"
    ready_state = all(value == "ready" for value in components.values())
    return JSONResponse(
        status_code=200 if ready_state else 503,
        content={
            "status": "ready" if ready_state else "not_ready",
            "provider": getattr(provider, "name", None),
            "components": components,
        },
    )


# Stable import names for tests and embedding applications.
live = health_live
ready = health_ready
