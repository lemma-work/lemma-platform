from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from agentbox.observability import create_inherited_task


FUNCTION_FILE_NAME = "function.py"
MANIFEST_NAME = ".lemma-function-cache.json"
CACHE_READY_NAME = ".lemma-function-ready"
DEPENDENCIES_DIR_NAME = ".dependencies"
DEPENDENCIES_READY_NAME = ".dependencies-ready"
_TERMINATION_GRACE_SECONDS = 2.0
_DEFAULT_STDOUT_LIMIT_BYTES = 256 * 1024
_DEFAULT_STDERR_LIMIT_BYTES = 256 * 1024
_DEFAULT_RESULT_LIMIT_BYTES = 2 * 1024 * 1024
_MIN_OUTPUT_LIMIT_BYTES = 1024
_MAX_LOG_LIMIT_BYTES = 8 * 1024 * 1024
_MAX_RESULT_LIMIT_BYTES = 16 * 1024 * 1024

# A function may declare pip dependencies in its `#python_packages:` header. The
# values are passed to `pip install`, so each must match a PEP 508-ish spec
# (name + optional [extras] + optional version specifier) — never a flag, URL,
# path, or anything with a space/shell metacharacter.
MAX_PYTHON_PACKAGES = 30
MAX_PACKAGE_SPEC_LENGTH = 128
PACKAGE_INSTALL_TIMEOUT_SECONDS = 180
_PACKAGE_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"  # distribution name
    r"(\[[A-Za-z0-9._,-]+\])?"  # optional extras, e.g. [socks,security]
    r"([<>=!~]=?[A-Za-z0-9._*+!,<>=~-]*)?$"  # optional version specifier(s)
)


def is_valid_python_package(spec: str) -> bool:
    return (
        bool(spec)
        and len(spec) <= MAX_PACKAGE_SPEC_LENGTH
        and _PACKAGE_SPEC_RE.match(spec) is not None
    )


def parse_python_packages(code: str) -> list[str]:
    """Extract the deduped `#python_packages:` requirements from a function's code.

    Entries are whitespace-separated; a leading/trailing comma is tolerated (so
    `pandas, numpy` works) while commas *inside* a token are preserved (version
    ranges / multi-extras like `numpy>=1.0,<2.0` or `requests[socks,security]`).
    """
    headers: dict[str, str] = {}
    for line in code.splitlines()[:8]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#") or ":" not in stripped:
            break
        key, value = stripped[1:].split(":", 1)
        headers[key.strip()] = value.strip()
    raw = headers.get("python_packages", "")
    packages: list[str] = []
    for token in raw.split():
        spec = token.strip().strip(",")
        if spec and spec not in packages:
            packages.append(spec)
    return packages


class RuntimeErrorInfo(BaseModel):
    name: str
    message: str
    traceback: list[str] = Field(default_factory=list)
    retryable: bool = False


class FunctionExecuteRequest(BaseModel):
    run_id: UUID
    input_data: dict[str, Any] = Field(default_factory=dict)
    async_job: bool = False
    timeout_seconds: int = Field(default=120, ge=1, le=3600)


class FunctionLogEntry(BaseModel):
    timestamp: str
    stream: Literal["stdout", "stderr", "system"]
    message: str


class FunctionInvokeResponse(BaseModel):
    status: Literal["completed", "failed", "cancelled", "timeout"]
    output_data: dict[str, Any] | None = None
    error: RuntimeErrorInfo | None = None
    logs: list[FunctionLogEntry] = Field(default_factory=list)
    code_hash: str
    duration_ms: int


class FunctionJobAcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    run_id: UUID
    job_id: str


class FunctionJobStatusResponse(BaseModel):
    run_id: UUID
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled", "timeout"]
    output_data: dict[str, Any] | None = None
    error: RuntimeErrorInfo | None = None
    code_hash: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None


class FunctionLogsResponse(BaseModel):
    run_id: UUID
    logs: list[FunctionLogEntry] = Field(default_factory=list)


class FunctionSchemaRequest(BaseModel):
    code_hash: str | None = None


class FunctionSchemaResponse(BaseModel):
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    config_schema: dict[str, Any] | None = None
    code_hash: str


class VerifiedToken(BaseModel):
    user_id: UUID
    email: str | None = None
    pod_id: UUID | None = None
    organization_id: UUID | None = None
    function_id: UUID | None = None
    function_name: str | None = None
    scopes: list[str] = Field(default_factory=list)


class FunctionMetadata(BaseModel):
    id: UUID
    name: str
    pod_id: UUID
    type: str = "API"
    code: str
    code_hash: str | None = None
    config: dict[str, Any] | None = None


class FunctionExecutionContext(BaseModel):
    run_id: UUID
    function_id: UUID
    function_name: str
    pod_id: UUID
    organization_id: UUID | None = None
    user_id: UUID
    user_email: str | None = None
    lemma_token: str
    lemma_base_url: str
    config: Any = None
    workspace_root: str = "/workspace"

    model_config = {"arbitrary_types_allowed": True}


@dataclass
class StoredJob:
    run_id: UUID
    job_id: str
    status: Literal[
        "queued", "running", "completed", "failed", "cancelled", "timeout"
    ] = "queued"
    logs: list[FunctionLogEntry] = field(default_factory=list)
    output_data: dict[str, Any] | None = None
    error: RuntimeErrorInfo | None = None
    code_hash: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None


@dataclass
class _RunHandle:
    run_id: UUID
    fingerprint: str
    submitted_at: float
    deadline: float
    runner: Callable[["_RunHandle"], Awaitable[Any]]
    future: asyncio.Future[Any]
    job: StoredJob | None = None
    task: asyncio.Task[None] | None = None
    expiry_task: asyncio.Task[None] | None = None
    process: asyncio.subprocess.Process | None = None
    queued: bool = False
    cancel_requested: bool = False
    cache_result: bool = False
    ephemeral: bool = False
    redactions: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RunTombstone:
    """Small, lifetime idempotency record without retained output or logs."""

    fingerprint: str
    async_job: bool
    status: Literal["completed", "failed", "cancelled", "timeout"]
    code_hash: str
    duration_ms: int
    completed_at: str


class ResultPayloadTooLargeError(RuntimeError):
    """The isolated worker exceeded the configured result-channel limit."""


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def function_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def parse_code_headers(code: str) -> tuple[str, str, str, str | None]:
    headers: dict[str, str] = {}
    for line in code.splitlines()[:8]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#") or ":" not in stripped:
            break
        key, value = stripped[1:].split(":", 1)
        headers[key.strip()] = value.strip()
    input_model = headers.get("input_type_name")
    output_model = headers.get("output_type_name")
    function_name = headers.get("function_name")
    config_model = headers.get("config_type_name")
    missing = [
        key
        for key, value in {
            "input_type_name": input_model,
            "output_type_name": output_model,
            "function_name": function_name,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"Missing function code header(s): {', '.join(missing)}")
    return input_model or "", output_model or "", function_name or "", config_model


def log_entry(
    stream: Literal["stdout", "stderr", "system"], message: str
) -> FunctionLogEntry:
    return FunctionLogEntry(timestamp=utc_timestamp(), stream=stream, message=message)


class LemmaFunctionApiClient:
    def __init__(
        self, *, base_url: str, token: str, timeout_seconds: float = 30.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def verify_token(self) -> VerifiedToken:
        payload = self._get_json("/auth/verify-token")
        return VerifiedToken.model_validate(payload)

    def get_function(self, pod_id: UUID, function_name: str) -> FunctionMetadata:
        quoted_name = urlparse.quote(function_name, safe="")
        payload = self._get_json(f"/pods/{pod_id}/functions/{quoted_name}")
        return FunctionMetadata.model_validate(payload)

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urlrequest.Request(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urlrequest.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise HTTPException(
                status_code=exc.code, detail=body or exc.reason
            ) from exc
        except urlerror.URLError as exc:
            raise HTTPException(
                status_code=502, detail=f"Lemma API request failed: {exc}"
            ) from exc
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=502, detail="Lemma API returned non-object JSON"
            )
        return payload


# Full output/log payloads are retained briefly and in a bounded cache. A separate
# lightweight tombstone remains for the sandbox lifetime, so cache eviction can
# never turn a transport retry into a second function execution.
_RESULT_TTL_SECONDS = 600.0
_MAX_COMPLETED_RESULTS = 32


class FunctionExecutor:
    def __init__(
        self,
        *,
        workspace_root: str = "/workspace",
        lemma_base_url: str | None = None,
        max_active: int | None = None,
        max_queued: int | None = None,
        max_stdout_bytes: int | None = None,
        max_stderr_bytes: int | None = None,
        max_result_bytes: int | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.lemma_base_url = lemma_base_url or os.environ.get(
            "LEMMA_BASE_URL", "http://localhost:8000"
        )
        configured_active = os.environ.get(
            "FUNCTION_EXECUTOR_MAX_ACTIVE",
            os.environ.get(
                "AGENTBOX_FUNCTION_MAX_CONCURRENCY",
                os.environ.get("AGENTBOX_FUNCTION_EXECUTOR_MAX_ACTIVE", "8"),
            ),
        )
        configured_queued = os.environ.get(
            "FUNCTION_EXECUTOR_MAX_QUEUED",
            os.environ.get(
                "AGENTBOX_FUNCTION_MAX_QUEUED",
                os.environ.get("AGENTBOX_FUNCTION_EXECUTOR_MAX_QUEUED", "32"),
            ),
        )
        self.max_active = (
            max_active if max_active is not None else int(configured_active)
        )
        self.max_queued = (
            max_queued if max_queued is not None else int(configured_queued)
        )
        if self.max_active < 1 or self.max_queued < 0:
            raise RuntimeError("Function executor capacity must be positive")
        self.max_stdout_bytes = self._configured_byte_limit(
            value=max_stdout_bytes,
            env_name="AGENTBOX_FUNCTION_MAX_STDOUT_BYTES",
            default=_DEFAULT_STDOUT_LIMIT_BYTES,
            maximum=_MAX_LOG_LIMIT_BYTES,
        )
        self.max_stderr_bytes = self._configured_byte_limit(
            value=max_stderr_bytes,
            env_name="AGENTBOX_FUNCTION_MAX_STDERR_BYTES",
            default=_DEFAULT_STDERR_LIMIT_BYTES,
            maximum=_MAX_LOG_LIMIT_BYTES,
        )
        self.max_result_bytes = self._configured_byte_limit(
            value=max_result_bytes,
            env_name="AGENTBOX_FUNCTION_MAX_RESULT_BYTES",
            default=_DEFAULT_RESULT_LIMIT_BYTES,
            maximum=_MAX_RESULT_LIMIT_BYTES,
        )
        self.jobs: dict[UUID, StoredJob] = {}
        self._runs: dict[UUID, _RunHandle] = {}
        self._queued: deque[_RunHandle] = deque()
        self._active = 0
        self._registry_lock = asyncio.Lock()
        self._cache_registry_lock = asyncio.Lock()
        self._cache_locks: dict[str, asyncio.Lock] = {}
        self._cache_tasks: dict[
            str, asyncio.Task[tuple[Path, dict[str, Any], Path]]
        ] = {}
        self._completed: "OrderedDict[UUID, tuple[float, FunctionInvokeResponse]]" = (
            OrderedDict()
        )
        self._tombstones: dict[UUID, _RunTombstone] = {}

    @staticmethod
    def _configured_byte_limit(
        *, value: int | None, env_name: str, default: int, maximum: int
    ) -> int:
        configured = (
            value if value is not None else int(os.environ.get(env_name, default))
        )
        if not _MIN_OUTPUT_LIMIT_BYTES <= configured <= maximum:
            raise RuntimeError(
                f"{env_name} must be between {_MIN_OUTPUT_LIMIT_BYTES} and {maximum}"
            )
        return configured

    def api_client(self, token: str) -> LemmaFunctionApiClient:
        return LemmaFunctionApiClient(base_url=self.lemma_base_url, token=token)

    async def execute(
        self,
        *,
        pod_id: UUID,
        function_name: str,
        request: FunctionExecuteRequest,
        token: str,
    ) -> FunctionInvokeResponse | FunctionJobAcceptedResponse:
        fingerprint = self._fingerprint(pod_id, function_name, request)
        job = (
            StoredJob(run_id=request.run_id, job_id=f"function:{request.run_id}")
            if request.async_job
            else None
        )

        async def runner(handle: _RunHandle) -> FunctionInvokeResponse:
            return await self._execute_isolated(
                handle,
                pod_id=pod_id,
                function_name=function_name,
                request=request,
                token=token,
            )

        handle, created = await self._admit(
            run_id=request.run_id,
            fingerprint=fingerprint,
            timeout_seconds=request.timeout_seconds,
            runner=runner,
            job=job,
            cache_result=not request.async_job,
            redactions=(token,),
        )
        if request.async_job:
            actual_job = handle.job
            if actual_job is None:
                raise HTTPException(
                    status_code=409,
                    detail="run_id is already used by a synchronous invocation",
                )
            if created:
                self.jobs[request.run_id] = actual_job
            return FunctionJobAcceptedResponse(
                run_id=request.run_id, job_id=actual_job.job_id
            )
        return await asyncio.shield(handle.future)

    async def schemas(
        self,
        *,
        pod_id: UUID,
        function_name: str,
        request: FunctionSchemaRequest,
        token: str,
    ) -> FunctionSchemaResponse:
        run_id = uuid4()
        timeout_seconds = int(
            os.environ.get("FUNCTION_EXECUTOR_SCHEMA_TIMEOUT_SECONDS", "120")
        )

        async def runner(handle: _RunHandle) -> FunctionSchemaResponse:
            (
                verified,
                metadata,
                cache_dir,
                manifest,
                dependency_dir,
            ) = await self._prepare_invocation(pod_id, function_name, token)
            del verified
            if (
                request.code_hash
                and metadata.code_hash
                and request.code_hash != metadata.code_hash
            ):
                raise HTTPException(
                    status_code=409, detail="Function code hash mismatch"
                )
            worker_result, _logs = await self._run_worker(
                handle,
                {
                    "mode": "schemas",
                    "cache_dir": str(cache_dir),
                    "dependency_dir": str(dependency_dir),
                    "manifest": manifest,
                },
                timeout_seconds=max(0.001, handle.deadline - time.monotonic()),
                token=token,
            )
            if not worker_result.get("ok"):
                error = worker_result.get("error") or {}
                raise RuntimeError(str(error.get("message") or "Schema worker failed"))
            return FunctionSchemaResponse(
                input_schema=worker_result["input_schema"],
                output_schema=worker_result["output_schema"],
                config_schema=worker_result.get("config_schema"),
                code_hash=manifest["code_hash"],
            )

        handle, _ = await self._admit(
            run_id=run_id,
            fingerprint=f"schema:{pod_id}:{function_name}:{request.code_hash}",
            timeout_seconds=timeout_seconds,
            runner=runner,
            ephemeral=True,
            redactions=(token,),
        )
        return await asyncio.shield(handle.future)

    async def _admit(
        self,
        *,
        run_id: UUID,
        fingerprint: str,
        timeout_seconds: float,
        runner: Callable[[_RunHandle], Awaitable[Any]],
        job: StoredJob | None = None,
        cache_result: bool = False,
        ephemeral: bool = False,
        redactions: tuple[str, ...] = (),
    ) -> tuple[_RunHandle, bool]:
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        async with self._registry_lock:
            self._sweep_expired_locked(now)
            existing = self._runs.get(run_id)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise self._run_id_conflict(run_id)
                return existing, False
            tombstone = self._tombstones.get(run_id)
            if tombstone is not None:
                if tombstone.fingerprint != fingerprint:
                    raise self._run_id_conflict(run_id)
                cached = self._completed.get(run_id)
                if tombstone.async_job:
                    response = cached[1] if cached is not None else None
                    prior_job = self.jobs.get(run_id)
                    completed_job = prior_job or self._job_from_tombstone(
                        run_id, tombstone, response
                    )
                    return self._completed_handle(
                        run_id=run_id,
                        fingerprint=fingerprint,
                        runner=runner,
                        response=response,
                        job=completed_job,
                    ), False
                if cached is None:
                    raise self._result_evicted(run_id, tombstone)
                return self._completed_handle(
                    run_id=run_id,
                    fingerprint=fingerprint,
                    runner=runner,
                    response=cached[1],
                ), False
            handle = _RunHandle(
                run_id=run_id,
                fingerprint=fingerprint,
                submitted_at=now,
                deadline=now + timeout_seconds,
                runner=runner,
                future=loop.create_future(),
                job=job,
                cache_result=cache_result,
                ephemeral=ephemeral,
                redactions=redactions,
            )
            self._runs[run_id] = handle
            if self._active < self.max_active:
                self._start_handle_locked(handle)
            elif len(self._queued) < self.max_queued:
                handle.queued = True
                self._queued.append(handle)
                handle.expiry_task = create_inherited_task(
                    self._expire_queued(handle), name=f"expire-run-{run_id}"
                )
            else:
                self._runs.pop(run_id, None)
                raise HTTPException(
                    status_code=429,
                    detail="Function executor queue is full",
                    headers={"Retry-After": "1"},
                )
        return handle, True

    @staticmethod
    def _run_id_conflict(run_id: UUID) -> HTTPException:
        return HTTPException(
            status_code=409,
            detail={
                "code": "run_id_conflict",
                "message": "run_id was already submitted with a different request",
                "run_id": str(run_id),
            },
        )

    @staticmethod
    def _result_evicted(run_id: UUID, tombstone: _RunTombstone) -> HTTPException:
        return HTTPException(
            status_code=409,
            detail={
                "code": "run_result_evicted",
                "message": (
                    "This run already reached a terminal state; its response payload "
                    "is no longer retained and the function was not re-executed."
                ),
                "run_id": str(run_id),
                "terminal_status": tombstone.status,
                "request_fingerprint": tombstone.fingerprint,
                "code_hash": tombstone.code_hash,
                "duration_ms": tombstone.duration_ms,
                "completed_at": tombstone.completed_at,
            },
        )

    @staticmethod
    def _completed_handle(
        *,
        run_id: UUID,
        fingerprint: str,
        runner: Callable[[_RunHandle], Awaitable[Any]],
        response: FunctionInvokeResponse | None,
        job: StoredJob | None = None,
    ) -> _RunHandle:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        future.set_result(response)
        return _RunHandle(
            run_id=run_id,
            fingerprint=fingerprint,
            submitted_at=time.monotonic(),
            deadline=time.monotonic(),
            runner=runner,
            future=future,
            job=job,
        )

    def _start_handle_locked(self, handle: _RunHandle) -> None:
        handle.queued = False
        if handle.expiry_task is not None:
            handle.expiry_task.cancel()
            handle.expiry_task = None
        self._active += 1
        if handle.job is not None:
            handle.job.status = "running"
            handle.job.started_at = utc_timestamp()
        handle.task = create_inherited_task(
            self._drive_handle(handle), name=f"drive-run-{handle.run_id}"
        )

    async def _expire_queued(self, handle: _RunHandle) -> None:
        await asyncio.sleep(max(0.0, handle.deadline - time.monotonic()))
        async with self._registry_lock:
            if not handle.queued or handle.future.done():
                return
            with contextlib.suppress(ValueError):
                self._queued.remove(handle)
            handle.queued = False
            if handle.ephemeral:
                if not handle.future.done():
                    handle.future.set_exception(
                        TimeoutError("Function schema extraction timed out in queue")
                    )
                self._runs.pop(handle.run_id, None)
            else:
                response = self._timeout_response(handle.submitted_at)
                self._complete_handle_locked(handle, response)

    async def _drive_handle(self, handle: _RunHandle) -> None:
        try:
            if handle.cancel_requested:
                result: Any = self._cancelled_response(handle.submitted_at)
            elif time.monotonic() >= handle.deadline:
                result = self._timeout_response(handle.submitted_at)
            else:
                result = await handle.runner(handle)
        except asyncio.CancelledError:
            result = self._cancelled_response(handle.submitted_at)
        except Exception as exc:
            if handle.cancel_requested and not handle.ephemeral:
                result = self._cancelled_response(handle.submitted_at)
            elif handle.ephemeral:
                if not handle.future.done():
                    handle.future.set_exception(
                        self._redacted_exception(exc, handle.redactions)
                    )
                result = None
            else:
                result = self._failure_response(
                    handle.submitted_at, exc, redactions=handle.redactions
                )
        async with self._registry_lock:
            if handle.cancel_requested and not handle.ephemeral:
                result = self._cancelled_response(handle.submitted_at)
            if result is not None:
                self._complete_handle_locked(handle, result)
            self._active = max(0, self._active - 1)
            self._start_queued_locked()

    def _start_queued_locked(self) -> None:
        while self._queued and self._active < self.max_active:
            next_handle = self._queued.popleft()
            if next_handle.future.done() or next_handle.cancel_requested:
                continue
            self._start_handle_locked(next_handle)

    def _complete_handle_locked(self, handle: _RunHandle, result: Any) -> None:
        if not handle.future.done():
            handle.future.set_result(result)
        if isinstance(result, FunctionInvokeResponse) and not handle.ephemeral:
            if handle.job is not None:
                self._update_job(handle.job, result)
            completed_at = utc_timestamp()
            self._tombstones[handle.run_id] = _RunTombstone(
                fingerprint=handle.fingerprint,
                async_job=handle.job is not None,
                status=result.status,
                code_hash=result.code_hash[:128],
                duration_ms=result.duration_ms,
                completed_at=completed_at,
            )
            self._completed[handle.run_id] = (time.monotonic(), result)
            self._completed.move_to_end(handle.run_id)
            self._sweep_expired_locked(time.monotonic())
        if handle.ephemeral:
            self._runs.pop(handle.run_id, None)

    @staticmethod
    def _update_job(job: StoredJob, result: FunctionInvokeResponse) -> None:
        job.logs = result.logs
        job.output_data = result.output_data
        job.error = result.error
        job.code_hash = result.code_hash
        job.duration_ms = result.duration_ms
        job.completed_at = utc_timestamp()
        job.status = result.status

    def _sweep_expired_locked(self, now: float) -> None:
        while self._completed:
            run_id, (completed_at, _response) = next(iter(self._completed.items()))
            if (
                len(self._completed) <= _MAX_COMPLETED_RESULTS
                and now - completed_at <= _RESULT_TTL_SECONDS
            ):
                break
            self._completed.popitem(last=False)
            handle = self._runs.get(run_id)
            if handle is not None and handle.future.done():
                self._runs.pop(run_id, None)
            self.jobs.pop(run_id, None)

    @classmethod
    def _job_from_tombstone(
        cls,
        run_id: UUID,
        tombstone: _RunTombstone,
        response: FunctionInvokeResponse | None = None,
    ) -> StoredJob:
        job = StoredJob(
            run_id=run_id,
            job_id=f"function:{run_id}",
            status=tombstone.status,
            code_hash=tombstone.code_hash,
            completed_at=tombstone.completed_at,
            duration_ms=tombstone.duration_ms,
        )
        if response is not None:
            cls._update_job(job, response)
            job.completed_at = tombstone.completed_at
        else:
            message = (
                "This run already reached a terminal state; its detailed result "
                "and logs are no longer retained."
            )
            job.error = RuntimeErrorInfo(
                name="ResultNotRetained",
                message=message,
            )
            job.logs = [log_entry("system", message)]
        return job

    async def _execute_isolated(
        self,
        handle: _RunHandle,
        *,
        pod_id: UUID,
        function_name: str,
        request: FunctionExecuteRequest,
        token: str,
    ) -> FunctionInvokeResponse:
        try:
            remaining = handle.deadline - time.monotonic()
            if remaining <= 0:
                return self._timeout_response(handle.submitted_at)
            (
                verified,
                metadata,
                cache_dir,
                manifest,
                dependency_dir,
            ) = await asyncio.wait_for(
                self._prepare_invocation(pod_id, function_name, token),
                timeout=remaining,
            )
            remaining = handle.deadline - time.monotonic()
            if remaining <= 0:
                return self._timeout_response(handle.submitted_at)
            worker_result, logs = await self._run_worker(
                handle,
                {
                    "mode": "execute",
                    "cache_dir": str(cache_dir),
                    "dependency_dir": str(dependency_dir),
                    "manifest": manifest,
                    "metadata": metadata.model_dump(mode="json"),
                    "verified": verified.model_dump(mode="json"),
                    "request": request.model_dump(mode="json"),
                    "token": token,
                    "lemma_base_url": self.lemma_base_url,
                    "workspace_root": str(self.workspace_root),
                },
                timeout_seconds=remaining,
                token=token,
            )
            if handle.cancel_requested:
                return self._cancelled_response(handle.submitted_at, logs=logs)
            if worker_result.get("ok"):
                return FunctionInvokeResponse(
                    status="completed",
                    output_data=worker_result.get("output_data"),
                    logs=logs,
                    code_hash=manifest["code_hash"],
                    duration_ms=self._duration_ms(handle.submitted_at),
                )
            error = worker_result.get("error") or {}
            return FunctionInvokeResponse(
                status="failed",
                error=RuntimeErrorInfo(
                    name=str(error.get("name") or "RuntimeError"),
                    message=self._redact(
                        str(error.get("message") or "Function failed"), token
                    ),
                    traceback=[
                        self._redact(str(line), token)
                        for line in error.get("traceback") or []
                    ],
                ),
                logs=logs,
                code_hash=manifest["code_hash"],
                duration_ms=self._duration_ms(handle.submitted_at),
            )
        except TimeoutError:
            await self._terminate_handle_process(handle)
            return self._timeout_response(handle.submitted_at)
        except asyncio.CancelledError:
            await self._terminate_handle_process(handle)
            raise

    async def _prepare_invocation(
        self, pod_id: UUID, function_name: str, token: str
    ) -> tuple[VerifiedToken, FunctionMetadata, Path, dict[str, Any], Path]:
        verified, metadata = await asyncio.to_thread(
            self._authorize_and_fetch,
            pod_id=pod_id,
            function_name=function_name,
            token=token,
        )
        code_hash = metadata.code_hash or function_code_hash(metadata.code)
        lock = await self._cache_lock_for(code_hash)
        async with lock:
            cache_task = self._cache_tasks.get(code_hash)
            if cache_task is None:
                cache_task = create_inherited_task(
                    asyncio.to_thread(self.ensure_cached, metadata)
                )
                self._cache_tasks[code_hash] = cache_task

                def discard(done: asyncio.Task, *, key: str = code_hash) -> None:
                    with contextlib.suppress(asyncio.CancelledError):
                        done.exception()
                    if self._cache_tasks.get(key) is done:
                        self._cache_tasks.pop(key, None)

                cache_task.add_done_callback(discard)
        cache_dir, manifest, dependency_dir = await asyncio.shield(cache_task)
        return verified, metadata, cache_dir, manifest, dependency_dir

    async def _cache_lock_for(self, code_hash: str) -> asyncio.Lock:
        async with self._cache_registry_lock:
            return self._cache_locks.setdefault(code_hash, asyncio.Lock())

    async def _run_worker(
        self,
        handle: _RunHandle,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        token: str,
    ) -> tuple[dict[str, Any], list[FunctionLogEntry]]:
        read_fd, write_fd = os.pipe()
        os.set_inheritable(write_fd, True)
        env = self._worker_environment(write_fd)
        process: asyncio.subprocess.Process | None = None
        result_task: asyncio.Task[bytes] | None = None
        stdout_task: asyncio.Task[bytes] | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        process_task: asyncio.Task[int] | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "agentbox.function_executor_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                pass_fds=(write_fd,),
                env=env,
            )
            handle.process = process
            os.close(write_fd)
            write_fd = -1
            result_task = create_inherited_task(
                asyncio.to_thread(self._read_fd_limited, read_fd, self.max_result_bytes)
            )
            if (
                process.stdout is None
                or process.stderr is None
                or process.stdin is None
            ):
                raise RuntimeError("Function worker pipes were not created")
            stdout_task = create_inherited_task(
                self._read_stream_limited(
                    process.stdout, self.max_stdout_bytes, stream_name="stdout"
                )
            )
            stderr_task = create_inherited_task(
                self._read_stream_limited(
                    process.stderr, self.max_stderr_bytes, stream_name="stderr"
                )
            )
            process_task = create_inherited_task(
                process.wait(), name=f"function-process-{handle.run_id}"
            )

            async def exchange() -> tuple[bytes, bytes, bytes, int]:
                encoded_payload = json.dumps(
                    payload, separators=(",", ":"), default=str
                ).encode("utf-8")
                process.stdin.write(encoded_payload)
                await process.stdin.drain()
                process.stdin.close()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await process.stdin.wait_closed()
                raw_result, stdout, stderr, return_code = await asyncio.gather(
                    result_task, stdout_task, stderr_task, process_task
                )
                return raw_result, stdout, stderr, return_code

            raw_result, stdout, stderr, return_code = await asyncio.wait_for(
                exchange(), timeout=timeout_seconds
            )
            logs: list[FunctionLogEntry] = []
            stdout_text = self._redact(stdout.decode("utf-8", errors="replace"), token)
            stderr_text = self._redact(stderr.decode("utf-8", errors="replace"), token)
            if stdout_text:
                logs.append(log_entry("stdout", stdout_text))
            if stderr_text:
                logs.append(log_entry("stderr", stderr_text))
            if return_code != 0 and not raw_result:
                raise RuntimeError(f"Function worker exited with status {return_code}")
            try:
                result = json.loads(raw_result.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("Function worker returned malformed data") from exc
            if not isinstance(result, dict):
                raise RuntimeError("Function worker returned a non-object result")
            return result, logs
        except BaseException:
            if process is not None:
                await self._terminate_process(process)
            raise
        finally:
            handle.process = None
            if write_fd >= 0:
                os.close(write_fd)
            tasks = [
                task
                for task in (result_task, stdout_task, stderr_task, process_task)
                if task is not None
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if result_task is None:
                with contextlib.suppress(OSError):
                    os.close(read_fd)

    def _worker_environment(self, result_fd: int) -> dict[str, str]:
        allowed = {
            "PATH",
            "HOME",
            "PYTHONPATH",
            "TMPDIR",
            "TZ",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed}
        package_root = str(Path(__file__).resolve().parents[1])
        inherited_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (package_root, inherited_pythonpath) if part
        )
        env["LEMMA_FUNCTION_RESULT_FD"] = str(result_fd)
        env["LEMMA_FUNCTION_MAX_RESULT_BYTES"] = str(self.max_result_bytes)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    @staticmethod
    def _read_fd_limited(fd: int, limit: int) -> bytes:
        retained = bytearray()
        try:
            while True:
                chunk = os.read(fd, min(65536, limit - len(retained) + 1))
                if not chunk:
                    return bytes(retained)
                retained.extend(chunk)
                if len(retained) > limit:
                    raise ResultPayloadTooLargeError(
                        f"Function result exceeded the {limit}-byte limit"
                    )
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    @staticmethod
    async def _read_stream_limited(
        stream: asyncio.StreamReader, limit: int, *, stream_name: str
    ) -> bytes:
        retained = bytearray()
        truncated = False
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            remaining = limit - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        if truncated:
            marker = (f"\n... [{stream_name} truncated after {limit} bytes]\n").encode(
                "utf-8"
            )
            keep = max(0, limit - len(marker))
            del retained[keep:]
            retained.extend(marker[:limit])
        return bytes(retained)

    async def _terminate_handle_process(self, handle: _RunHandle) -> None:
        process = handle.process
        if process is not None:
            await self._terminate_process(process)

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        process_group = process.pid
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process_group, signal.SIGTERM)
        deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
        while FunctionExecutor._process_group_exists(process_group):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if process.returncode is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=min(0.05, remaining))
            else:
                await asyncio.sleep(min(0.05, remaining))
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process_group, signal.SIGKILL)
        if process.returncode is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    process.wait(), timeout=_TERMINATION_GRACE_SECONDS
                )

    @staticmethod
    def _process_group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def cancel_run(self, run_id: UUID) -> bool:
        task: asyncio.Task[None] | None = None
        process: asyncio.subprocess.Process | None = None
        async with self._registry_lock:
            handle = self._runs.get(run_id)
            if handle is None:
                return False
            if handle.future.done():
                result = handle.future.result()
                return (
                    isinstance(result, FunctionInvokeResponse)
                    and result.status == "cancelled"
                )
            handle.cancel_requested = True
            if handle.queued:
                with contextlib.suppress(ValueError):
                    self._queued.remove(handle)
                handle.queued = False
                if handle.expiry_task is not None:
                    handle.expiry_task.cancel()
                    handle.expiry_task = None
                self._complete_handle_locked(
                    handle, self._cancelled_response(handle.submitted_at)
                )
                return True
            task = handle.task
            process = handle.process
            if process is None and task is not None:
                task.cancel()
        if process is not None:
            await self._terminate_process(process)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return True

    async def delete_job(self, run_id: UUID) -> bool:
        await self.cancel_run(run_id)
        async with self._registry_lock:
            deleted = self.jobs.pop(run_id, None) is not None
            handle = self._runs.get(run_id)
            if handle is not None and (handle.future.done() or handle.cancel_requested):
                self._runs.pop(run_id, None)
            self._completed.pop(run_id, None)
            return deleted

    async def cancel_status(self, run_id: UUID) -> FunctionJobStatusResponse:
        async with self._registry_lock:
            if (
                run_id not in self._runs
                and run_id not in self.jobs
                and run_id not in self._tombstones
            ):
                raise HTTPException(status_code=404, detail="Function run not found")
        await self.cancel_run(run_id)
        return self.run_status(run_id)

    def run_status(self, run_id: UUID) -> FunctionJobStatusResponse:
        if run_id in self.jobs:
            return self.job_status(run_id)
        handle = self._runs.get(run_id)
        if handle is None:
            tombstone = self._tombstones.get(run_id)
            if tombstone is not None:
                cached = self._completed.get(run_id)
                job = self._job_from_tombstone(
                    run_id,
                    tombstone,
                    cached[1] if cached is not None else None,
                )
                return self._status_from_job(job)
            raise HTTPException(status_code=404, detail="Function run not found")
        status: Literal[
            "queued", "running", "completed", "failed", "cancelled", "timeout"
        ] = "queued" if handle.queued else "running"
        output_data = None
        error_info = None
        code_hash = None
        duration_ms = None
        completed_at = None
        if handle.future.done():
            try:
                result = handle.future.result()
            except Exception as exc:
                status = "failed"
                error_info = RuntimeErrorInfo(name=type(exc).__name__, message=str(exc))
            else:
                if isinstance(result, FunctionInvokeResponse):
                    status = result.status
                    output_data = result.output_data
                    error_info = result.error
                    code_hash = result.code_hash
                    duration_ms = result.duration_ms
                    completed_at = utc_timestamp()
        return FunctionJobStatusResponse(
            run_id=run_id,
            job_id=f"function:{run_id}",
            status=status,
            output_data=output_data,
            error=error_info,
            code_hash=code_hash,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

    def job_status(self, run_id: UUID) -> FunctionJobStatusResponse:
        job = self._get_job(run_id)
        return self._status_from_job(job)

    @staticmethod
    def _status_from_job(job: StoredJob) -> FunctionJobStatusResponse:
        return FunctionJobStatusResponse(
            run_id=job.run_id,
            job_id=job.job_id,
            status=job.status,
            output_data=job.output_data,
            error=job.error,
            code_hash=job.code_hash,
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_ms=job.duration_ms,
        )

    def job_logs(self, run_id: UUID) -> FunctionLogsResponse:
        job = self._get_job(run_id)
        return FunctionLogsResponse(run_id=run_id, logs=job.logs)

    def _get_job(self, run_id: UUID) -> StoredJob:
        job = self.jobs.get(run_id)
        if job is None:
            tombstone = self._tombstones.get(run_id)
            if tombstone is not None and tombstone.async_job:
                cached = self._completed.get(run_id)
                return self._job_from_tombstone(
                    run_id, tombstone, cached[1] if cached is not None else None
                )
        if job is None:
            raise HTTPException(status_code=404, detail="Function job not found")
        return job

    @staticmethod
    def _fingerprint(
        pod_id: UUID, function_name: str, request: FunctionExecuteRequest
    ) -> str:
        value = json.dumps(
            {
                "pod_id": str(pod_id),
                "function_name": function_name,
                "request": request.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return int((time.monotonic() - started_at) * 1000)

    @classmethod
    def _timeout_response(cls, started_at: float) -> FunctionInvokeResponse:
        return FunctionInvokeResponse(
            status="timeout",
            error=RuntimeErrorInfo(name="TimeoutError", message="Function timed out"),
            code_hash="",
            duration_ms=cls._duration_ms(started_at),
        )

    @classmethod
    def _cancelled_response(
        cls, started_at: float, *, logs: list[FunctionLogEntry] | None = None
    ) -> FunctionInvokeResponse:
        return FunctionInvokeResponse(
            status="cancelled",
            error=RuntimeErrorInfo(name="CancelledError", message="Function cancelled"),
            logs=logs or [],
            code_hash="",
            duration_ms=cls._duration_ms(started_at),
        )

    @classmethod
    def _failure_response(
        cls,
        started_at: float,
        exc: Exception,
        *,
        redactions: tuple[str, ...] = (),
    ) -> FunctionInvokeResponse:
        message = cls._redact_many(str(exc), redactions)
        return FunctionInvokeResponse(
            status="failed",
            error=RuntimeErrorInfo(
                name=type(exc).__name__,
                message=message,
                traceback=[
                    cls._redact_many(line, redactions)
                    for line in traceback.format_exc().splitlines()
                ],
            ),
            logs=[log_entry("system", message)],
            code_hash="",
            duration_ms=cls._duration_ms(started_at),
        )

    @staticmethod
    def _redact(value: str, token: str) -> str:
        return value.replace(token, "[REDACTED]") if token else value

    @classmethod
    def _redact_many(cls, value: str, secrets: tuple[str, ...]) -> str:
        for secret in secrets:
            value = cls._redact(value, secret)
        return value

    @classmethod
    def _redacted_exception(cls, exc: Exception, secrets: tuple[str, ...]) -> Exception:
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, str):
                detail = cls._redact_many(detail, secrets)
            return HTTPException(
                status_code=exc.status_code,
                detail=detail,
                headers=exc.headers,
            )
        return RuntimeError(cls._redact_many(str(exc), secrets))

    def _authorize_and_fetch(
        self,
        *,
        pod_id: UUID,
        function_name: str,
        token: str,
    ) -> tuple[VerifiedToken, FunctionMetadata]:
        client = self.api_client(token)
        verified = client.verify_token()
        if verified.pod_id is not None and verified.pod_id != pod_id:
            raise HTTPException(
                status_code=403, detail="Token is not delegated to this pod"
            )
        if (
            verified.function_name is not None
            and verified.function_name != function_name
        ):
            raise HTTPException(
                status_code=403, detail="Token is not delegated to this function"
            )
        metadata = client.get_function(pod_id, function_name)
        if metadata.pod_id != pod_id or metadata.name != function_name:
            raise HTTPException(
                status_code=409, detail="Function metadata does not match request path"
            )
        if verified.function_id is not None and verified.function_id != metadata.id:
            raise HTTPException(
                status_code=403, detail="Token is not delegated to this function"
            )
        return verified, metadata

    def cache_dir(self, metadata: FunctionMetadata, code_hash: str) -> Path:
        return (
            self.workspace_root
            / "pods"
            / str(metadata.pod_id)
            / "functions"
            / metadata.name
            / code_hash
        )

    def ensure_packages(self, packages: list[str], dependency_dir: Path) -> None:
        """Install dependencies into this code hash's private import directory."""

        for spec in packages:
            if not is_valid_python_package(spec):
                raise RuntimeError(f"Invalid python package specifier: {spec!r}")
        if len(packages) > MAX_PYTHON_PACKAGES:
            raise RuntimeError(
                f"Too many python packages declared ({len(packages)} > {MAX_PYTHON_PACKAGES})."
            )
        ready_path = dependency_dir.parent / DEPENDENCIES_READY_NAME
        expected_marker = sorted(packages)
        if ready_path.exists() and dependency_dir.is_dir():
            try:
                if json.loads(ready_path.read_text()) == expected_marker:
                    return
            except (json.JSONDecodeError, OSError):
                pass
        ready_path.unlink(missing_ok=True)
        temporary_dir = dependency_dir.parent / (
            f"{DEPENDENCIES_DIR_NAME}.tmp-{os.getpid()}-{uuid4().hex}"
        )
        temporary_dir.mkdir(parents=True, exist_ok=False)
        try:
            if packages:
                try:
                    proc = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pip",
                            "install",
                            "--target",
                            str(temporary_dir),
                            "--no-input",
                            "--disable-pip-version-check",
                            "-q",
                            *packages,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=PACKAGE_INSTALL_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        f"Installing python package(s) {packages} timed out after "
                        f"{PACKAGE_INSTALL_TIMEOUT_SECONDS}s."
                    ) from exc
                if proc.returncode != 0:
                    tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
                    raise RuntimeError(
                        f"Failed to install python package(s) {packages}: {tail}"
                    )
            if dependency_dir.exists():
                shutil.rmtree(dependency_dir)
            os.replace(temporary_dir, dependency_dir)
            self._atomic_write(ready_path, json.dumps(expected_marker))
        finally:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir, ignore_errors=True)

    def ensure_cached(
        self, metadata: FunctionMetadata
    ) -> tuple[Path, dict[str, Any], Path]:
        code_hash = metadata.code_hash or function_code_hash(metadata.code)
        packages = parse_python_packages(metadata.code)
        cache_dir = self.cache_dir(metadata, code_hash)
        cache_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = cache_dir / MANIFEST_NAME
        function_path = cache_dir / FUNCTION_FILE_NAME
        ready_path = cache_dir / CACHE_READY_NAME
        dependency_dir = cache_dir / DEPENDENCIES_DIR_NAME
        self.ensure_packages(packages, dependency_dir)
        if ready_path.exists() and manifest_path.exists() and function_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                if (
                    ready_path.read_text() == code_hash
                    and manifest.get("code_hash") == code_hash
                ):
                    return cache_dir, manifest, dependency_dir
            except (json.JSONDecodeError, OSError):
                pass
        input_model, output_model, entrypoint, config_model = parse_code_headers(
            metadata.code
        )
        manifest = {
            "code_hash": code_hash,
            "function": metadata.model_dump(mode="json", exclude={"code"}),
            "python_packages": packages,
            "runtime": {
                "input_model": input_model,
                "output_model": output_model,
                "function_name": entrypoint,
                "config_model": config_model,
            },
        }
        ready_path.unlink(missing_ok=True)
        self._atomic_write(function_path, metadata.code)
        self._atomic_write(manifest_path, json.dumps(manifest, sort_keys=True))
        self._atomic_write(ready_path, code_hash)
        return cache_dir, manifest, dependency_dir

    @staticmethod
    def _atomic_write(path: Path, value: str) -> None:
        temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid4().hex}")
        try:
            temporary.write_text(value)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token


def build_app(executor: FunctionExecutor | None = None) -> FastAPI:
    app = FastAPI(title="AgentBox Function Executor", version="0.1.0")
    app.state.executor = executor or FunctionExecutor()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readiness")
    async def readiness() -> dict[str, str]:
        # Returns 200 only once this app is bound and serving, i.e. it can
        # accept an execute request. The executor is constructed eagerly in
        # build_app() with no async warm-up, so a served response is the
        # readiness signal callers probe before POSTing /execute (avoids the
        # cold-start 502 while the app's port is still binding).
        return {"status": "ready"}

    @app.post("/pods/{pod_id}/functions/{function_name}/execute")
    async def execute_function(
        pod_id: UUID,
        function_name: str,
        request: FunctionExecuteRequest,
        authorization: str | None = Header(default=None),
    ):
        return await app.state.executor.execute(
            pod_id=pod_id,
            function_name=function_name,
            request=request,
            token=bearer_token(authorization),
        )

    @app.post(
        "/pods/{pod_id}/functions/{function_name}/schemas",
        response_model=FunctionSchemaResponse,
    )
    async def extract_schemas(
        pod_id: UUID,
        function_name: str,
        request: FunctionSchemaRequest,
        authorization: str | None = Header(default=None),
    ) -> FunctionSchemaResponse:
        return await app.state.executor.schemas(
            pod_id=pod_id,
            function_name=function_name,
            request=request,
            token=bearer_token(authorization),
        )

    @app.get("/runs/{run_id}", response_model=FunctionJobStatusResponse)
    async def get_run(run_id: UUID) -> FunctionJobStatusResponse:
        return app.state.executor.job_status(run_id)

    @app.get("/runs/{run_id}/logs", response_model=FunctionLogsResponse)
    async def get_run_logs(run_id: UUID) -> FunctionLogsResponse:
        return app.state.executor.job_logs(run_id)

    @app.delete("/runs/{run_id}")
    async def delete_run(run_id: UUID) -> dict[str, bool | str]:
        return {
            "run_id": str(run_id),
            "deleted": await app.state.executor.delete_job(run_id),
        }

    @app.post("/runs/{run_id}/cancel", response_model=FunctionJobStatusResponse)
    async def cancel_run(run_id: UUID) -> FunctionJobStatusResponse:
        return await app.state.executor.cancel_status(run_id)

    return app


app = build_app()
