from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from sandbox_runtime.tasks import create_inherited_task

from .runner import (
    GatewayClient,
    _cached_artifact_root,
    _resolve_artifact_root,
)
from .runtime_models import (
    FunctionArtifactManifest,
    RunAccepted,
    RuntimeFailure,
    RuntimeInvocation,
    SchemaInspection,
    TerminalReport,
    WorkerRequest,
)
from .trace_context import bind_trace_context
from .worker_pool import RevisionWorkerRegistry, RuntimeOverloaded


_MAX_INPUT_BYTES = 1024 * 1024
_MAX_RUN_RECORDS = 4096


@dataclass(slots=True)
class _Run:
    function_id: UUID
    signature: str
    task: asyncio.Task[TerminalReport]


class FunctionRuntimeService:
    """Resident, cache-backed runtime with no durable execution state."""

    def __init__(self, *, max_workers: int, max_cached_revisions: int) -> None:
        self._workers = RevisionWorkerRegistry(
            max_workers=max_workers,
            max_cached_revisions=max_cached_revisions,
        )
        self._runs: OrderedDict[UUID, _Run] = OrderedDict()
        self._gateways: dict[str, GatewayClient] = {}
        self._lock = asyncio.Lock()

    async def invoke(
        self,
        *,
        function_token: str,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        gateway_url: str,
        invocation: RuntimeInvocation,
    ) -> TerminalReport:
        run = await self._start(
            function_token=function_token,
            function_id=function_id,
            revision_hash=revision_hash,
            run_id=run_id,
            gateway_url=gateway_url,
            invocation=invocation,
            report_terminal=False,
        )
        return await asyncio.shield(run.task)

    async def accept(
        self,
        *,
        function_token: str,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        gateway_url: str,
        invocation: RuntimeInvocation,
    ) -> RunAccepted:
        await self._start(
            function_token=function_token,
            function_id=function_id,
            revision_hash=revision_hash,
            run_id=run_id,
            gateway_url=gateway_url,
            invocation=invocation,
            report_terminal=True,
        )
        return RunAccepted(run_id=run_id)

    async def inspect_schemas(
        self,
        *,
        function_token: str,
        function_id: UUID,
        revision_hash: str,
        gateway_url: str,
        generation: UUID | None = None,
    ) -> SchemaInspection:
        """Load one immutable revision and retain its serving worker."""

        deadline_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        gateway = await self._gateway(gateway_url)
        try:
            root = await self._artifact_root(
                gateway,
                function_token=function_token,
                function_id=function_id,
                revision_hash=revision_hash,
                deadline_at=deadline_at,
                **({"generation": generation} if generation else {}),
            )
            schemas = await self._workers.inspect_schemas(
                function_id=function_id,
                revision_hash=revision_hash,
                artifact_root=root,
                deadline_at=deadline_at,
            )
            return SchemaInspection(ok=True, schemas=schemas)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return SchemaInspection(
                ok=False,
                error=RuntimeFailure(name=type(exc).__name__, message=str(exc)),
            )

    async def _start(
        self,
        *,
        function_token: str,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        gateway_url: str,
        invocation: RuntimeInvocation,
        report_terminal: bool,
    ) -> _Run:
        if invocation.identity.function_id != function_id:
            raise ValueError("invocation identity does not match function path")
        signature = self._signature(
            function_id=function_id,
            revision_hash=revision_hash,
            run_id=run_id,
            gateway_url=gateway_url,
            invocation=invocation,
            report_terminal=report_terminal,
        )
        async with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                if (
                    existing.function_id != function_id
                    or existing.signature != signature
                ):
                    raise ValueError("run ID was reused for a different invocation")
                self._runs.move_to_end(run_id)
                return existing

            task = create_inherited_task(
                self._execute(
                    function_token=function_token,
                    function_id=function_id,
                    revision_hash=revision_hash,
                    run_id=run_id,
                    gateway_url=gateway_url,
                    invocation=invocation,
                    report_terminal=report_terminal,
                )
            )
            task.add_done_callback(self._consume_task_result)
            run = _Run(
                function_id=function_id,
                signature=signature,
                task=task,
            )
            self._runs[run_id] = run
            self._evict_completed()
            return run

    async def cancel(self, function_id: UUID, run_id: UUID) -> bool:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.function_id != function_id:
                return False
            task = run.task
        await self._workers.cancel(run_id)
        if not task.done():
            task.cancel()
        return True

    async def close(self) -> None:
        async with self._lock:
            tasks = tuple(
                run.task for run in self._runs.values() if not run.task.done()
            )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._workers.close()
        async with self._lock:
            gateways = tuple(self._gateways.values())
            self._gateways.clear()
        await asyncio.gather(
            *(gateway.close() for gateway in gateways),
            return_exceptions=True,
        )

    async def _gateway(self, gateway_url: str) -> GatewayClient:
        async with self._lock:
            gateway = self._gateways.get(gateway_url)
            if gateway is None:
                gateway = GatewayClient(gateway_url)
                self._gateways[gateway_url] = gateway
            return gateway

    async def _execute(
        self,
        *,
        function_token: str,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        gateway_url: str,
        invocation: RuntimeInvocation,
        report_terminal: bool,
    ) -> TerminalReport:
        gateway = await self._gateway(gateway_url)
        try:
            root = await self._artifact_root(
                gateway,
                function_token=function_token,
                function_id=function_id,
                revision_hash=revision_hash,
                deadline_at=invocation.deadline_at,
            )
            worker = WorkerRequest(
                artifact_root=str(root),
                manifest=self._manifest(root),
                run_id=run_id,
                input_data=invocation.input,
                config=invocation.config,
                identity=invocation.identity,
                lemma_token=function_token,
                lemma_base_url=invocation.lemma_base_url,
            )
            response = await self._workers.execute(
                function_id=function_id,
                revision_hash=revision_hash,
                artifact_root=root,
                run_id=run_id,
                request=worker,
                deadline_at=invocation.deadline_at,
            )
            report = TerminalReport(
                status="completed" if response.ok else "failed",
                output_data=response.output_data,
                error=response.error,
                stdout=response.stdout,
                stderr=response.stderr,
                output_truncated=response.output_truncated,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            report = TerminalReport(
                status="failed",
                error=RuntimeFailure(name=type(exc).__name__, message=str(exc)),
                stdout="",
                stderr="",
            )

        if report_terminal:
            await gateway.terminal(
                function_token,
                run_id=run_id,
                deadline_at=invocation.deadline_at,
                report=report,
            )
        return report

    @staticmethod
    async def _artifact_root(
        gateway: GatewayClient,
        *,
        function_token: str,
        function_id: UUID,
        revision_hash: str,
        deadline_at: datetime,
        generation: UUID | None = None,
    ) -> Path:
        # Fast path avoids even constructing an artifact HTTP request.
        digest = revision_hash.removeprefix("sha256:")
        cached = _cached_artifact_root(digest)
        if cached is not None:
            return cached
        return await _resolve_artifact_root(
            gateway,
            function_token,
            function_id=function_id,
            revision_hash=revision_hash,
            deadline_at=deadline_at,
            **({"generation": generation} if generation else {}),
        )

    @staticmethod
    def _consume_task_result(task: asyncio.Task[TerminalReport]) -> None:
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _manifest(root: Path) -> FunctionArtifactManifest:
        return FunctionArtifactManifest.model_validate_json(
            (root / "manifest.json").read_bytes()
        )

    @staticmethod
    def _signature(
        *,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        gateway_url: str,
        invocation: RuntimeInvocation,
        report_terminal: bool,
    ) -> str:
        payload = json.dumps(
            {
                "function_id": str(function_id),
                "revision_hash": revision_hash,
                "run_id": str(run_id),
                "gateway_url": gateway_url,
                "invocation": invocation.model_dump(mode="json"),
                "report_terminal": report_terminal,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def _evict_completed(self) -> None:
        for run_id, run in tuple(self._runs.items()):
            if len(self._runs) <= _MAX_RUN_RECORDS:
                break
            if run.task.done():
                self._runs.pop(run_id)


def _bearer(request: Request) -> str:
    scheme, separator, value = request.headers.get("authorization", "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not value.strip():
        raise ValueError("Authorization bearer is required")
    return value.strip()


def _quoted_digest(request: Request) -> str:
    value = request.headers.get("if-match", "").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not value.startswith("sha256:") or len(value) != 71:
        raise ValueError("If-Match must contain an exact sha256 revision hash")
    return value


def _gateway_url(request: Request) -> str:
    value = request.headers.get("x-lemma-gateway-url", "").rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("X-Lemma-Gateway-Url is invalid")
    allowed_hosts = {
        item.strip().lower()
        for item in os.environ.get("LEMMA_FUNCTION_GATEWAY_HOSTS", "").split(",")
        if item.strip()
    }
    if allowed_hosts and parsed.hostname.lower() not in allowed_hosts:
        raise ValueError("X-Lemma-Gateway-Url host is not allowed")
    return value


async def health(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ready": True,
            "runtime_abi": "lemma-function-python-3.14-linux-x86_64-1",
            "protocol_version": 2,
        }
    )


async def invoke(request: Request) -> JSONResponse:
    with bind_trace_context(request.headers):
        return await _invoke(request)


async def _invoke(request: Request) -> JSONResponse:
    try:
        content_length = int(request.headers.get("content-length", "0"))
    except ValueError:
        content_length = _MAX_INPUT_BYTES + 1
    if content_length > _MAX_INPUT_BYTES:
        return JSONResponse({"error": "invocation body is too large"}, status_code=413)
    try:
        body_bytes = await request.body()
        if len(body_bytes) > _MAX_INPUT_BYTES:
            raise ValueError("invocation body is too large")
        invocation = RuntimeInvocation.model_validate_json(body_bytes)
        function_id = UUID(request.path_params["function_id"])
        run_id = UUID(request.path_params["run_id"])
        service: FunctionRuntimeService = request.app.state.runtime
        parameters = {
            "function_token": _bearer(request),
            "function_id": function_id,
            "revision_hash": _quoted_digest(request),
            "run_id": run_id,
            "gateway_url": _gateway_url(request),
            "invocation": invocation,
        }
        if request.headers.get("prefer", "").strip().lower() == "respond-async":
            accepted = await service.accept(**parameters)
            return JSONResponse(
                accepted.model_dump(mode="json"),
                status_code=202,
                headers={"Preference-Applied": "respond-async"},
            )
        report = await service.invoke(**parameters)
        return JSONResponse(report.model_dump(mode="json"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except RuntimeOverloaded as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception:
        return JSONResponse({"error": "function runtime failed"}, status_code=500)


async def inspect_schemas(request: Request) -> JSONResponse:
    with bind_trace_context(request.headers):
        try:
            function_id = UUID(request.path_params["function_id"])
            service: FunctionRuntimeService = request.app.state.runtime
            result = await service.inspect_schemas(
                function_token=_bearer(request),
                function_id=function_id,
                revision_hash=_quoted_digest(request),
                gateway_url=_gateway_url(request),
                **(
                    {"generation": UUID(request.headers["x-lemma-artifact-generation"])}
                    if "x-lemma-artifact-generation" in request.headers
                    else {}
                ),
            )
            return JSONResponse(
                result.model_dump(mode="json"),
                status_code=200 if result.ok else 422,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except TimeoutError:
            return JSONResponse(
                {"error": "function schema inspection timed out"},
                status_code=504,
            )
        except Exception:
            return JSONResponse(
                {"error": "function schema inspection failed"},
                status_code=500,
            )


async def cancel(request: Request) -> JSONResponse:
    try:
        function_id = UUID(request.path_params["function_id"])
        run_id = UUID(request.path_params["run_id"])
        service: FunctionRuntimeService = request.app.state.runtime
        accepted = await service.cancel(function_id, run_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return JSONResponse({"accepted": accepted}, status_code=202 if accepted else 404)


#: Where the provider puts this sandbox's runtime credential, and the header
#: the backend presents it in -- the same header the workspace runtime takes.
RUNTIME_TOKEN_ENV = "LEMMA_FUNCTION_RUNTIME_TOKEN"
RUNTIME_TOKEN_HEADER = "x-lemma-runtime-token"
#: The one route anybody may call: a readiness probe says nothing and does
#: nothing.
_UNAUTHENTICATED_PATHS = frozenset({"/healthz"})


def _load_runtime_token(explicit: str | None) -> str | None:
    """This sandbox's credential, taken out of the environment.

    Popped rather than read, so the worker processes that run function code --
    spawned from this one -- do not inherit it.
    """

    configured = os.environ.pop(RUNTIME_TOKEN_ENV, "").strip()
    return explicit or configured or None


class _RequireRuntimeToken:
    """Refuse every call that does not carry this sandbox's credential.

    The runtime executes whatever artifact it is told to, fetched from
    whichever gateway it is told to use. The function token in
    `Authorization` is the *function's* credential against the gateway, and
    any string passes as one here, so without this anything that could reach
    the port -- another sandbox on the same bridge -- could have it run code of
    its choosing, or cancel somebody else's run. The credential is derived
    per sandbox by the backend and delivered only to this sandbox and to the
    caller the backend hands the lease to.

    Optional because a provider that does not deliver one -- an image rolled
    out before a provider sends it -- must keep serving; every provider whose
    sandboxes share a network with others delivers it.
    """

    def __init__(self, app, token: str) -> None:
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") in _UNAUTHENTICATED_PATHS:
            await self._app(scope, receive, send)
            return
        provided = b""
        for name, value in scope.get("headers", ()):
            if name.lower() == RUNTIME_TOKEN_HEADER.encode():
                provided = value.strip()
                break
        if not provided or not hmac.compare_digest(provided, self._token):
            response = JSONResponse(
                {"error": "runtime credential is missing or wrong"},
                status_code=401,
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


def create_app(
    *,
    max_workers: int | None = None,
    max_cached_revisions: int | None = None,
    runtime_token: str | None = None,
) -> Starlette:
    token = _load_runtime_token(runtime_token)
    configured_max = max_workers or int(
        os.environ.get("LEMMA_FUNCTION_MAX_WORKERS", "32")
    )
    configured_cached_revisions = max_cached_revisions or int(
        os.environ.get("LEMMA_FUNCTION_MAX_CACHED_REVISIONS", "16")
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.runtime = FunctionRuntimeService(
            max_workers=configured_max,
            max_cached_revisions=configured_cached_revisions,
        )
        try:
            yield
        finally:
            await app.state.runtime.close()

    app = Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            # Before the run route, which would otherwise take it: `{run_id}`
            # matches `<uuid>:cancel`, fails to parse, and every cancellation
            # the backend sent was answered 422 without reaching `cancel`.
            Route(
                "/functions/{function_id}/runs/{run_id}:cancel",
                cancel,
                methods=["POST"],
            ),
            Route(
                "/functions/{function_id}/runs/{run_id}",
                invoke,
                methods=["POST"],
            ),
            Route(
                "/functions/{function_id}/schemas",
                inspect_schemas,
                methods=["POST"],
            ),
        ],
        lifespan=lifespan,
    )
    if token is not None:
        app.add_middleware(_RequireRuntimeToken, token=token)
    return app
