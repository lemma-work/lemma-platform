from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agentbox.observability import create_inherited_task

from .runner import GatewayClient, _resolve_artifact_root
from .runtime_models import (
    FunctionArtifactManifest,
    RunAccepted,
    RunClaim,
    RuntimeFailure,
    TerminalReport,
    WorkerRequest,
)
from .types import JsonObject
from .trace_context import bind_trace_context
from .worker_pool import RevisionWorkerRegistry, RuntimeOverloaded


_MAX_INPUT_BYTES = 1024 * 1024
_MAX_RUN_RECORDS = 4096


class InvocationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: JsonObject


@dataclass(slots=True)
class _Run:
    signature: str
    invocation_token_digest: bytes
    run_token_digest: bytes
    task: asyncio.Task[TerminalReport]
    accepted: asyncio.Event
    acceptance_error: BaseException | None = None


class FunctionRuntimeService:
    """Resident runtime that executes each run ID at most once."""

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
        run_token: str,
        gateway_url: str,
        input_data: JsonObject,
    ) -> TerminalReport:
        run = await self._start(
            function_token=function_token,
            function_id=function_id,
            revision_hash=revision_hash,
            run_id=run_id,
            run_token=run_token,
            gateway_url=gateway_url,
            input_data=input_data,
        )
        return await asyncio.shield(run.task)

    async def accept(
        self,
        *,
        function_token: str,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        run_token: str,
        gateway_url: str,
        input_data: JsonObject,
    ) -> RunAccepted:
        run = await self._start(
            function_token=function_token,
            function_id=function_id,
            revision_hash=revision_hash,
            run_id=run_id,
            run_token=run_token,
            gateway_url=gateway_url,
            input_data=input_data,
        )
        await run.accepted.wait()
        if run.acceptance_error is not None:
            raise run.acceptance_error
        return RunAccepted(run_id=run_id)

    async def _start(
        self,
        *,
        function_token: str,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        run_token: str,
        gateway_url: str,
        input_data: JsonObject,
    ) -> _Run:
        signature = self._signature(
            function_id=function_id,
            revision_hash=revision_hash,
            run_id=run_id,
            gateway_url=gateway_url,
            input_data=input_data,
        )
        token_digest = hashlib.sha256(function_token.encode()).digest()
        run_token_digest = hashlib.sha256(run_token.encode()).digest()
        async with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                if (
                    existing.signature != signature
                    or not hmac.compare_digest(
                        existing.invocation_token_digest, token_digest
                    )
                    or not hmac.compare_digest(
                        existing.run_token_digest, run_token_digest
                    )
                ):
                    raise ValueError(
                        "run ID was reused for a different invocation or session"
                    )
                self._runs.move_to_end(run_id)
                run = existing
            else:
                task = create_inherited_task(
                    self._execute(
                        function_token=function_token,
                        function_id=function_id,
                        revision_hash=revision_hash,
                        run_id=run_id,
                        gateway_url=gateway_url,
                        input_data=input_data,
                    )
                )
                task.add_done_callback(self._consume_task_result)
                run = _Run(
                    signature=signature,
                    invocation_token_digest=token_digest,
                    run_token_digest=run_token_digest,
                    task=task,
                    accepted=asyncio.Event(),
                )
                self._runs[run_id] = run
                self._evict_completed()
        return run

    async def cancel(self, run_id: UUID, callback_token: str) -> bool:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None or not hmac.compare_digest(
                run.run_token_digest,
                hashlib.sha256(callback_token.encode()).digest(),
            ):
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
        input_data: JsonObject,
    ) -> TerminalReport:
        gateway = await self._gateway(gateway_url)
        claim = None
        accepted = False
        try:
            try:
                claim = await gateway.claim(
                    function_token,
                    run_id=run_id,
                    revision_hash=revision_hash,
                    input_data=input_data,
                )
                self._validate_claim(
                    claim=claim,
                    function_id=function_id,
                    revision_hash=revision_hash,
                    run_id=run_id,
                    input_data=input_data,
                )
                await self._mark_accepted(run_id, claim.callback_token)
                accepted = True
                root = await _resolve_artifact_root(gateway, claim)
                worker = WorkerRequest(
                    artifact_root=str(root),
                    manifest=self._manifest(root),
                    run_id=run_id,
                    input_data=input_data,
                    config=claim.config,
                    identity=claim.identity,
                    lemma_token=claim.lemma_token,
                    lemma_base_url=claim.lemma_base_url,
                )
                response = await self._workers.execute(
                    function_id=function_id,
                    revision_hash=revision_hash,
                    artifact_root=root,
                    run_id=run_id,
                    request=worker,
                    deadline_at=claim.deadline_at,
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
                if not accepted:
                    await self._mark_rejected(run_id, exc)
                    raise
                report = TerminalReport(
                    status="failed",
                    error=RuntimeFailure(name=type(exc).__name__, message=str(exc)),
                    stdout="",
                    stderr="",
                )
            await gateway.terminal(claim, report)
            return report
        except asyncio.CancelledError as exc:
            if not accepted:
                await self._mark_rejected(run_id, exc)
            raise

    async def _mark_accepted(self, run_id: UUID, callback_token: str) -> None:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            if not hmac.compare_digest(
                run.run_token_digest,
                hashlib.sha256(callback_token.encode()).digest(),
            ):
                raise ValueError("runtime gateway returned a different run token")
            run.accepted.set()

    async def _mark_rejected(self, run_id: UUID, error: BaseException) -> None:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.accepted.is_set():
                return
            run.acceptance_error = error
            run.accepted.set()

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
    def _validate_claim(
        *,
        claim: RunClaim,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        input_data: JsonObject,
    ) -> None:
        if claim.identity.function_id != function_id:
            raise ValueError("function ID does not match the authorized run")
        if claim.revision_hash != revision_hash:
            raise ValueError("revision hash does not match the authorized run")
        if claim.run_id != run_id:
            raise ValueError("run ID does not match the authorized run")
        if claim.input_data != input_data:
            raise ValueError("input does not match the persisted function run")

    @staticmethod
    def _signature(
        *,
        function_id: UUID,
        revision_hash: str,
        run_id: UUID,
        gateway_url: str,
        input_data: JsonObject,
    ) -> str:
        payload = json.dumps(
            {
                "function_id": str(function_id),
                "revision_hash": revision_hash,
                "run_id": str(run_id),
                "gateway_url": gateway_url,
                "input": input_data,
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


def _run_token(request: Request) -> str:
    value = request.headers.get("x-lemma-run-token", "").strip()
    if len(value) < 32:
        raise ValueError("X-Lemma-Run-Token is required")
    return value


async def health(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ready": True,
            "runtime_abi": "lemma-function-python-3.14-linux-x86_64-1",
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
        body = InvocationBody.model_validate_json(body_bytes)
        function_id = UUID(request.path_params["function_id"])
        run_id = UUID(request.path_params["run_id"])
        service: FunctionRuntimeService = request.app.state.runtime
        function_token = _bearer(request)
        revision_hash = _quoted_digest(request)
        gateway_url = _gateway_url(request)
        run_token = _run_token(request)
        if request.headers.get("prefer", "").strip().lower() == "respond-async":
            accepted = await service.accept(
                function_token=function_token,
                function_id=function_id,
                revision_hash=revision_hash,
                run_id=run_id,
                run_token=run_token,
                gateway_url=gateway_url,
                input_data=body.input,
            )
            return JSONResponse(
                accepted.model_dump(mode="json"),
                status_code=202,
                headers={"Preference-Applied": "respond-async"},
            )
        report = await service.invoke(
            function_token=function_token,
            function_id=function_id,
            revision_hash=revision_hash,
            run_id=run_id,
            run_token=run_token,
            gateway_url=gateway_url,
            input_data=body.input,
        )
        return JSONResponse(report.model_dump(mode="json"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except RuntimeOverloaded as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except httpx.HTTPStatusError as exc:
        status = 401 if exc.response.status_code in {401, 403} else 502
        return JSONResponse(
            {"error": "runtime gateway rejected invocation"}, status_code=status
        )
    except Exception:
        return JSONResponse({"error": "function runtime failed"}, status_code=500)


async def cancel(request: Request) -> JSONResponse:
    try:
        run_id = UUID(request.path_params["run_id"])
        service: FunctionRuntimeService = request.app.state.runtime
        accepted = await service.cancel(run_id, _bearer(request))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return JSONResponse({"accepted": accepted}, status_code=202 if accepted else 404)


def create_app(
    *,
    max_workers: int | None = None,
    max_cached_revisions: int | None = None,
) -> Starlette:
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

    return Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route(
                "/functions/{function_id}/runs/{run_id}",
                invoke,
                methods=["POST"],
            ),
            Route(
                "/runs/{run_id}:cancel",
                cancel,
                methods=["POST"],
            ),
        ],
        lifespan=lifespan,
    )
