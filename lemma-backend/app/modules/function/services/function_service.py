"""Function service."""

from __future__ import annotations

import hashlib
import asyncio
import contextlib
import json
import os
import re
import time
import traceback
from datetime import datetime
from typing import Any
from uuid import UUID, uuid7

from app.core.authorization.context import (
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.permissions import Permissions
from app.core.helpers.slug import slugify
from app.core.domain.job_queue import JobQueuePort
from app.modules.icon.services.icon_service import IconService
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
    FunctionUpdateEntity,
    RunAsWorkload,
)
from app.modules.function.domain.errors import (
    FunctionConflictError,
    FunctionNotFoundError,
    FunctionRunNotFoundError,
    FunctionValidationError,
)
from app.core.domain.events import DomainEvent
from app.modules.function.domain.events import (
    FunctionRunCompletedEvent,
    FunctionRunExecutionRequestedEvent,
    FunctionRunFailedEvent,
)
from app.modules.workspace.agentbox_retry import (
    RETRYABLE_HTTP_STATUS_CODES,
    retry_on_transient_agentbox_error,
)
from app.modules.function.domain.ports import (
    FunctionStorageFactoryPort,
    FunctionRepositoryPort,
    FunctionRunRepositoryPort,
    WorkspaceSessionPort,
)
from app.modules.function.services.function_runtime_command import (
    function_workspace_cwd,
)
import httpx

from agentbox_client.apps.function_executor import (
    FunctionExecuteRequest,
    FunctionExecutorClient,
    FunctionInvokeResponse,
    FunctionJobAcceptedResponse,
    RuntimeErrorInfo,
)
from app.modules.workspace.services.agentbox_manager import agentbox_sandbox_id
from app.core.config import settings
from app.modules.pod.domain.pod_entities import PodRole
from app.core.log.log import get_logger

logger = get_logger(__name__)

_API_FUNCTION_TIMEOUT_SECONDS = int(os.getenv("LEMMA_API_FUNCTION_TIMEOUT_SECONDS", "120"))
_JOB_FUNCTION_TIMEOUT_SECONDS = 600
_JOB_FUNCTION_MAX_OUTPUT_TOKENS = 4000

# A function's `#python_packages:` header declares pip dependencies that the
# agentbox executor installs before running. The values are passed to `pip
# install`, so each must be a PEP 508-ish spec (name + optional [extras] +
# optional version specifier) — never a flag, URL, path, space, or shell
# metacharacter. Mirrors agentbox/agentbox/function_executor.py.
_MAX_PYTHON_PACKAGES = 30
_MAX_PACKAGE_SPEC_LENGTH = 128
_PYTHON_PACKAGE_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"          # distribution name
    r"(\[[A-Za-z0-9._,-]+\])?"               # optional extras
    r"([<>=!~]=?[A-Za-z0-9._*+!,<>=~-]*)?$"  # optional version specifier(s)
)
# How long to wait for the in-sandbox function_executor app to become ready
# before posting an execute request (the app starts lazily after the VM is up).
_FUNCTION_EXECUTOR_READY_TIMEOUT_SECONDS = 30.0
# Retry budgets for transient (proxy not-ready / 5xx / connection-refused)
# errors. The execute call gets the full readiness window; per-poll calls get a
# small budget because the outer poll deadline provides the macro retry budget.
_FUNCTION_EXECUTE_RETRY_MAX_ATTEMPTS = 12
_FUNCTION_POLL_RETRY_MAX_ATTEMPTS = 4
# How often to poll a JOB function's status while it runs. Kept coarse (the run
# is async/background) so we don't hammer the manager proxy + in-sandbox app.
_FUNCTION_POLL_INTERVAL_SECONDS = int(
    os.getenv("LEMMA_FUNCTION_POLL_INTERVAL_SECONDS", "5")
)
# How often to heartbeat the sandbox while a JOB runs. A JOB occupies the
# sandbox through the function_executor app and holds no runtime session, so
# without this the idle reaper deletes the pod mid-run once it exceeds the
# sandbox idle timeout (default 300s). Must stay comfortably below that timeout.
_SANDBOX_HEARTBEAT_INTERVAL_SECONDS = int(
    os.getenv("LEMMA_SANDBOX_HEARTBEAT_INTERVAL_SECONDS", "30")
)
# A function run must execute despite transient/internal sandbox churn -- the pod
# being idle-reaped, evicted, or restarted mid-run, or a manager proxy blip. On a
# recoverable sandbox error we reprovision the sandbox (the next attempt's
# get_session recreates a missing/dead pod) and re-run, bounded by these attempts.
# Only a sandbox that cannot be provisioned (error persists across attempts) is
# allowed to surface as a run failure.
_SANDBOX_RECOVERY_MAX_ATTEMPTS = int(
    os.getenv("LEMMA_SANDBOX_RECOVERY_MAX_ATTEMPTS", "3")
)
_SANDBOX_RECOVERY_BACKOFF_SECONDS = 2.0
_SANDBOX_RECOVERY_MAX_BACKOFF_SECONDS = 10.0
# Manager HTTP statuses meaning "the sandbox/pod is missing or not usable right
# now" (as opposed to a real client error like 400/401/403) -- reprovision+retry.
_RECOVERABLE_SANDBOX_STATUS_CODES = frozenset({404, 409, 500, 502, 503, 504})
# httpx transport failures worth recovering from. Deliberately NOT bare
# OSError/TimeoutError: the poll's own "job did not finish before timeout"
# (a real function timeout) is a builtin TimeoutError and must stay terminal.
_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.WriteTimeout,
)


def _normalize_function_visibility(value: ResourceVisibility | str | None) -> str:
    if value is None:
        return ResourceVisibility.POD.value
    raw = value.value if isinstance(value, ResourceVisibility) else str(value)
    try:
        visibility = ResourceVisibility(raw.upper())
    except ValueError as exc:
        raise FunctionValidationError(f"Invalid visibility: {value}") from exc
    return visibility.value


class FunctionService:
    """Application service for function use-cases."""

    _SCHEMA_OUTPUT_MARKER = "__LEMMA_FUNCTION_SCHEMAS__"
    def __init__(
        self,
        function_repository: FunctionRepositoryPort,
        run_repository: FunctionRunRepositoryPort,
        workspace_service: WorkspaceSessionPort,
        storage_factory: FunctionStorageFactoryPort,
        authorization_service: object,
        job_queue: JobQueuePort | None = None,
        icon_service: IconService | None = None,
        function_executor_client_factory=None,
        uow_factory=None,
    ):
        self.repository = function_repository
        self.run_repository = run_repository
        self.workspace_service = workspace_service
        self.storage_factory = storage_factory
        self.job_queue = job_queue
        self.icon_service = icon_service
        self.authorization_service = authorization_service
        self.function_executor_client_factory = function_executor_client_factory
        self._uow_factory = uow_factory

    async def _require_pod_permission(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        required_role: PodRole,
        message: str,
        function_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> None:
        _ = message
        action = {
            PodRole.VIEWER: Permissions.FUNCTION_READ,
            PodRole.USER: Permissions.FUNCTION_EXECUTE,
            PodRole.EDITOR: Permissions.FUNCTION_UPDATE,
            PodRole.ADMIN: Permissions.FUNCTION_DELETE,
        }[required_role]
        if ctx is not None:
            await ctx.require(
                action,
                ResourceRef(
                    resource_type=ResourceType.FUNCTION
                    if function_id
                    else ResourceType.POD,
                    resource_id=function_id or pod_id,
                    pod_id=pod_id,
                ),
            )
            return
        if user_id is not None:
            raise RuntimeError("Context is required for function authorization")

    async def _validate_resources(self, function: FunctionEntity) -> None:
        _ = function

    async def create_function(
        self,
        entity: FunctionEntity,
        user_id: UUID,
        code: str | None = None,
        ctx: Context | None = None,
    ) -> FunctionEntity:
        if ctx is not None:
            await ctx.require(Permissions.FUNCTION_CREATE, ResourceRef.pod(entity.pod_id))
        else:
            raise RuntimeError("Context is required for function authorization")

        existing = await self.repository.get_by_name(entity.pod_id, entity.name)
        if existing:
            raise FunctionConflictError(
                f"Function with name '{entity.name}' already exists in pod {entity.pod_id}"
            )

        entity.user_id = user_id
        entity.visibility = _normalize_function_visibility(entity.visibility)
        await self._validate_resources(entity)
        created = await self.repository.create(entity)
        assert created.id is not None

        if not code:
            return created

        path = f"{slugify(created.name)}.py"
        storage = self.storage_factory(created.id)
        await storage.write_file(path, code)

        # Fail fast on a bad dependency spec before the heavier schema extraction.
        created.python_packages = self._parse_python_packages(code)
        input_schema, output_schema, config_schema = await self._extract_schemas(
            user_id, code, path, created.pod_id, created.id
        )
        created.input_schema = input_schema
        created.output_schema = output_schema
        created.config_schema = config_schema
        created.code_path = path
        created.code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        created.status = FunctionStatus.READY
        return await self.repository.update(created)

    async def get_function_by_name(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        *,
        raise_not_found: bool = False,
        include_code: bool = True,
        ctx: Context | None = None,
    ) -> FunctionEntity | None:
        function = await self.repository.get_by_name(pod_id, name, ctx=ctx)
        if not function:
            if raise_not_found:
                raise FunctionNotFoundError(f"Function {name} not found")
            return None

        await self._require_pod_permission(
            pod_id=function.pod_id,
            user_id=user_id,
            required_role=PodRole.VIEWER,
            message=f"User {user_id} does not have access to pod {function.pod_id}",
            function_id=function.id,
            ctx=ctx,
        )

        if include_code and function.code_path:
            function.code = await self._get_code(function)
        return function

    async def update_function(
        self,
        pod_id: UUID,
        name: str,
        update_entity: FunctionUpdateEntity,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> FunctionEntity:
        function = await self.get_function_by_name(
            pod_id, name, user_id, raise_not_found=True, include_code=False, ctx=ctx
        )
        assert function is not None
        assert function.id is not None
        old_icon_url = function.icon_url

        await self._require_pod_permission(
            pod_id=function.pod_id,
            user_id=user_id,
            required_role=PodRole.EDITOR,
            message=f"User {user_id} does not have editor access to pod {function.pod_id}",
            function_id=function.id,
            ctx=ctx,
        )

        if update_entity.visibility is not None:
            function.visibility = _normalize_function_visibility(update_entity.visibility)

        code_path = function.code_path
        if update_entity.code:
            if not code_path:
                code_path = f"{slugify(function.name)}.py"

            storage = self.storage_factory(function.id)
            await storage.write_file(code_path, update_entity.code)

            function.python_packages = self._parse_python_packages(update_entity.code)
            input_schema, output_schema, config_schema = await self._extract_schemas(
                user_id, update_entity.code, code_path, function.pod_id, function.id
            )
            function.input_schema = input_schema
            function.output_schema = output_schema
            function.config_schema = config_schema
            function.code_path = code_path
            function.code_hash = hashlib.sha256(update_entity.code.encode("utf-8")).hexdigest()
            function.status = FunctionStatus.READY

        if update_entity.description is not None:
            function.description = update_entity.description
        if "icon_url" in update_entity.model_fields_set:
            function.icon_url = update_entity.icon_url
        if "config" in update_entity.model_fields_set and update_entity.config is not None:
            function.config = update_entity.config
        if update_entity.type is not None:
            function.type = update_entity.type

        updated = await self.repository.update(function)
        if self.icon_service and old_icon_url != updated.icon_url:
            await self.icon_service.delete_by_url(old_icon_url)
        if ctx is not None:
            refreshed = await self.repository.get_by_name(pod_id, name, ctx=ctx)
            return refreshed or updated
        return updated

    async def delete_function(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> bool:
        function = await self.repository.get_by_name(pod_id, name, ctx=ctx)
        if function is None:
            raise FunctionNotFoundError(f"Function {name} not found")
        assert function.id is not None

        if ctx is not None:
            await ctx.require(
                Permissions.FUNCTION_DELETE,
                ResourceRef(
                    resource_type=ResourceType.FUNCTION,
                    resource_id=function.id,
                    pod_id=pod_id,
                ),
            )
        else:
            await self._require_pod_permission(
                pod_id=function.pod_id,
                user_id=user_id,
                required_role=PodRole.ADMIN,
                message=f"User {user_id} does not have admin access to pod {function.pod_id}",
                function_id=function.id,
            )

        deleted = await self.repository.delete(function.id)
        if not deleted:
            raise FunctionNotFoundError(f"Function {name} not found")
        if self.icon_service:
            await self.icon_service.delete_by_url(function.icon_url)
        return True

    async def list_functions(
        self,
        pod_id: UUID,
        user_id: UUID,
        limit: int = 100,
        cursor: str | None = None,
        ctx: Context | None = None,
    ) -> tuple[list[FunctionEntity], str | None]:
        if ctx is None:
            raise RuntimeError("Context is required for function listing")
        await self._require_pod_permission(
            pod_id=pod_id,
            user_id=user_id,
            required_role=PodRole.VIEWER,
            message=f"User {user_id} does not have access to pod {pod_id}",
            ctx=ctx,
        )
        return await self.repository.list_visible_by_pod(
            pod_id,
            ctx,
            limit,
            cursor,
        )

    async def _get_code(self, function: FunctionEntity) -> str:
        if function.code is not None:
            return function.code
        if not function.code_path:
            raise FunctionValidationError(f"Function {function.name} has no code")
        storage = self.storage_factory(function.id)
        code = await storage.read_file(function.code_path)
        if isinstance(code, bytes):
            code = code.decode("utf-8")
        function.code = code
        return code

    def _parse_code_headers(self, code: str) -> tuple[str, str, str, str | None]:
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
        function_name_in_code = headers.get("function_name")
        if not input_model or not output_model or not function_name_in_code:
            raise FunctionValidationError(
                "Function code must begin with header lines for input type, output type, and function name.",
                details={
                    "expected_header_lines": [
                        "#input_type_name: CreateExpenseInput",
                        "#output_type_name: CreateExpenseResult",
                        "#function_name: run_function",
                        "#config_type_name: ExpenseFunctionConfig  # optional",
                    ]
                },
            )
        return (
            input_model,
            output_model,
            function_name_in_code,
            headers.get("config_type_name") or None,
        )

    def _parse_python_packages(self, code: str) -> list[str]:
        """Extract + validate the `#python_packages:` pip requirements from code.

        Entries are whitespace-separated; a leading/trailing comma is tolerated
        (so `pandas, numpy` works) while commas inside a token are preserved
        (`numpy>=1.0,<2.0`, `requests[socks,security]`). Raises
        ``FunctionValidationError`` on an unsafe/invalid specifier.
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

        packages: list[str] = []
        for token in headers.get("python_packages", "").split():
            spec = token.strip().strip(",")
            if not spec or spec in packages:
                continue
            if (
                len(spec) > _MAX_PACKAGE_SPEC_LENGTH
                or _PYTHON_PACKAGE_SPEC_RE.match(spec) is None
            ):
                raise FunctionValidationError(
                    f"Invalid python package specifier: {spec!r}",
                    details={
                        "rule": (
                            "Each #python_packages entry must be a PyPI name with an "
                            "optional [extras] and version specifier (e.g. 'pandas', "
                            "'pandas==2.2', 'requests[socks]'). No URLs, paths, "
                            "flags, or spaces."
                        )
                    },
                )
            packages.append(spec)
        if len(packages) > _MAX_PYTHON_PACKAGES:
            raise FunctionValidationError(
                f"Too many python packages declared ({len(packages)} > "
                f"{_MAX_PYTHON_PACKAGES})."
            )
        return packages

    def _build_execution_error_details(self, result: Any, *, stage: str) -> dict[str, Any]:
        details: dict[str, Any] = {"stage": stage}

        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        if stdout:
            details["stdout"] = stdout
        if stderr:
            details["stderr"] = stderr

        error = getattr(result, "error", None)
        if error:
            details["error"] = error

        error_in_exec = getattr(result, "error_in_exec", None)
        if error_in_exec:
            details["execution_error"] = error_in_exec

        return details

    def _build_execution_error_message(self, result: Any, *, stage: str) -> str:
        error_in_exec = getattr(result, "error_in_exec", None)
        if isinstance(error_in_exec, dict):
            evalue = error_in_exec.get("evalue")
            ename = error_in_exec.get("ename")
            if ename and evalue:
                return f"Function {stage} failed: {ename}: {evalue}"
            if evalue:
                return f"Function {stage} failed: {evalue}"

        stderr = getattr(result, "stderr", None)
        if isinstance(stderr, str) and stderr.strip():
            first_line = stderr.strip().splitlines()[0]
            return f"Function {stage} failed: {first_line}"

        error = getattr(result, "error", None)
        if isinstance(error, str) and error.strip():
            return f"Function {stage} failed: {error.strip()}"

        return f"Function {stage} failed."

    def _build_execution_logs(self, result: Any) -> str | None:
        parts: list[str] = []
        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        if isinstance(stdout, str) and stdout:
            parts.append(stdout)
        if isinstance(stderr, str) and stderr:
            parts.append(stderr)

        error = getattr(result, "error", None)
        if isinstance(error, str) and error:
            parts.append(error)
        elif error:
            parts.append(json.dumps(error, default=str))

        error_in_exec = getattr(result, "error_in_exec", None)
        if error_in_exec:
            parts.append(json.dumps(error_in_exec, default=str))

        return "\n".join(part for part in parts if part) or None

    def _format_execution_exception(self, exc: Exception) -> tuple[str, str | None]:
        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            body = getattr(response, "text", None) or getattr(response, "content", b"")
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="replace")
            body = str(body).strip()
            request = getattr(response, "request", None)
            request_label = ""
            if request is not None:
                request_label = f" {getattr(request, 'method', '')} {getattr(request, 'url', '')}".strip()
            error = (
                f"Function sandbox request failed with HTTP {status_code}"
                if status_code is not None
                else "Function sandbox request failed"
            )
            if body:
                first_line = body.splitlines()[0]
                error = f"{error}: {first_line}"
            logs = "\n".join(
                part
                for part in [
                    f"Sandbox request failed{': ' + request_label if request_label else ''}",
                    f"HTTP status: {status_code}" if status_code is not None else None,
                    body,
                ]
                if part
            )
            return error, logs or None

        error = str(exc) or exc.__class__.__name__
        logs = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return error, logs

    def _job_workspace_session_id(self, run_id: UUID) -> str:
        return f"function-run-{run_id}"

    def _api_workspace_session_id(self, function_id: UUID) -> str:
        return f"function-api-{function_id}"

    def _api_workspace_cwd(self, function: FunctionEntity) -> str:
        return function_workspace_cwd(function)

    def _extract_marked_json(self, stdout: str | None, marker: str) -> Any | None:
        if not stdout:
            return None
        for line in reversed(stdout.splitlines()):
            if line.startswith(marker):
                payload = line[len(marker) :].strip()
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning("Failed to decode marked JSON for marker %s", marker)
                    return None
        return None

    async def _execute_api_run(
        self,
        *,
        function: FunctionEntity,
        run: FunctionRunEntity,
        user_email: str | None,
        timeout_seconds: int,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        assert function.id is not None
        assert run.id is not None

        run.started_at = datetime.now()
        run.status = FunctionRunStatus.RUNNING
        await self.run_repository.update_run(
            run.id,
            status=run.status,
            started_at=run.started_at,
            user_email=user_email,
        )

        # When called from an agent tool, reuse the agent's cached delegation token
        # (keyed by workload_type/workload_id) instead of minting a separate function token.
        effective_workload_type = run_as_workload.workload_type if run_as_workload else "function"
        effective_workload_id = run_as_workload.workload_id if run_as_workload else function.id
        effective_workload_name = run_as_workload.workload_name if run_as_workload else function.name

        async def _attempt() -> FunctionInvokeResponse:
            session = await self.workspace_service.get_session(
                user_id=run.user_id,
                pod_id=function.pod_id,
                session_id=self._api_workspace_session_id(function.id),
                initial_cwd=self._api_workspace_cwd(function),
                close_on_exit=False,
                workload_type=effective_workload_type,
                workload_id=effective_workload_id,
                workload_name=effective_workload_name,
            )
            try:
                run.workspace_session_id = session.session_id
                await self.run_repository.update_run(
                    run.id,
                    workspace_session_id=session.session_id,
                    workspace_process_id=None,
                )
                return await self._execute_via_function_executor(
                    function=function,
                    run=run,
                    session=session,
                    timeout_seconds=timeout_seconds,
                    async_job=False,
                )
            finally:
                close = getattr(session, "close", None)
                if close is not None:
                    await close()

        executor_response = await self._execute_with_sandbox_recovery(
            run=run, make_attempt=_attempt
        )
        self._apply_executor_response_to_run(run, executor_response)

    async def execute_function(
        self,
        pod_id: UUID,
        name: str,
        input_data: dict,
        user_id: UUID,
        user_email: str | None = None,
        ctx: Context | None = None,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        if ctx is None:
            raise RuntimeError("Context is required for function authorization")
        # Executing a function requires only FUNCTION_EXECUTE — mirroring
        # AGENT_EXECUTE for agent-as-tool. The right to execute implies loading the
        # definition to run it, so a workload (or user) granted just
        # function.execute can run it without also holding function.read. We load
        # the entity directly here instead of via get_function_by_name, which would
        # additionally enforce the viewer/read permission. (Inspecting a function
        # through the read API still requires function.read.)
        function = await self.repository.get_by_name(pod_id, name, ctx=ctx)
        if function is None:
            raise FunctionNotFoundError(f"Function {name} not found")
        assert function.id is not None
        await ctx.require(
            Permissions.FUNCTION_EXECUTE,
            ResourceRef(
                resource_type=ResourceType.FUNCTION,
                resource_id=function.id,
                pod_id=function.pod_id,
            ),
        )

        run_entity = FunctionRunEntity(
            id=uuid7(),
            function_id=function.id,
            user_id=user_id,
            user_email=user_email,
            input_data=input_data,
            status=FunctionRunStatus.PENDING,
        )

        if function.type == FunctionType.JOB:
            run_entity.job_id = self._run_job_id(run_entity.id)
            run_entity.add_event(
                FunctionRunExecutionRequestedEvent(
                    run_id=run_entity.id,
                    function_id=function.id,
                )
            )

        run = await self.run_repository.create_run(run_entity)
        assert run.id is not None

        if function.type == FunctionType.JOB:
            return run

        return await self._execute_run(
            function=function,
            run=run,
            user_email=user_email,
            timeout_seconds=_API_FUNCTION_TIMEOUT_SECONDS,
            run_as_workload=run_as_workload,
        )

    async def execute_run_by_id(
        self,
        run_id: UUID,
        *,
        timeout_seconds: int = _JOB_FUNCTION_TIMEOUT_SECONDS,
    ) -> FunctionRunEntity:
        if self._uow_factory is not None:
            async with self._uow_factory() as uow:
                from app.modules.function.infrastructure.repositories import (
                    FunctionRepository,
                    FunctionRunRepository,
                )
                from app.core.infrastructure.events.message_bus import get_message_bus

                message_bus = get_message_bus()
                run_repo = FunctionRunRepository(uow, message_bus=message_bus)
                fn_repo = FunctionRepository(uow, message_bus=message_bus)
                run = await run_repo.get_run(run_id)
                if run is None:
                    raise FunctionRunNotFoundError(f"Run {run_id} not found")
                function = await fn_repo.get(run.function_id)
                if function is None:
                    raise FunctionNotFoundError(f"Function {run.function_id} not found")

            return await self._execute_run(
                function=function,
                run=run,
                user_email=run.user_email,
                timeout_seconds=timeout_seconds,
            )

        run = await self.run_repository.get_run(run_id)
        if run is None:
            raise FunctionRunNotFoundError(f"Run {run_id} not found")

        function = await self.repository.get(run.function_id)
        if function is None:
            raise FunctionNotFoundError(f"Function {run.function_id} not found")

        return await self._execute_run(
            function=function,
            run=run,
            user_email=run.user_email,
            timeout_seconds=timeout_seconds,
        )

    async def _execute_run(
        self,
        *,
        function: FunctionEntity,
        run: FunctionRunEntity,
        user_email: str | None,
        timeout_seconds: int,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        if function.type == FunctionType.JOB:
            try:
                return await self._execute_job_run(
                    function=function,
                    run=run,
                    user_email=user_email,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                run.status = FunctionRunStatus.FAILED
                run.error, run.logs = self._format_execution_exception(exc)
                run.completed_at = datetime.now()
                await self._persist_terminal_run(function, run)
                logger.exception("Function job run %s failed during execution", run.id)
                return run

        try:
            await self._execute_api_run(
                function=function,
                run=run,
                user_email=user_email,
                timeout_seconds=timeout_seconds,
                run_as_workload=run_as_workload,
            )
        except Exception as exc:
            run.status = FunctionRunStatus.FAILED
            run.error, exception_logs = self._format_execution_exception(exc)
            run.logs = "\n".join(
                part for part in [run.logs, exception_logs] if part
            ) or None
            logger.exception("Function run %s failed during execution", run.id)

        run.completed_at = datetime.now()
        await self._persist_terminal_run(function, run)
        return run

    async def _execute_job_run(
        self,
        *,
        function: FunctionEntity,
        run: FunctionRunEntity,
        user_email: str | None,
        timeout_seconds: int,
    ) -> FunctionRunEntity:
        started_at = run.started_at or datetime.now()
        if run.status != FunctionRunStatus.RUNNING:
            run.status = FunctionRunStatus.RUNNING
            run.started_at = started_at

        async def _update_run_status(**kwargs):
            if self._uow_factory is not None:
                async with self._uow_factory() as uow:
                    from app.modules.function.infrastructure.repositories import (
                        FunctionRunRepository,
                    )
                    from app.core.infrastructure.events.message_bus import get_message_bus

                    await FunctionRunRepository(
                        uow, message_bus=get_message_bus()
                    ).update_run(run.id, **kwargs)
            else:
                await self.run_repository.update_run(run.id, **kwargs)

        async def _attempt() -> FunctionInvokeResponse:
            session = await self.workspace_service.get_session(
                user_id=run.user_id,
                pod_id=function.pod_id,
                session_id=run.workspace_session_id
                or self._job_workspace_session_id(run.id),
                initial_cwd=function_workspace_cwd(function),
                close_on_exit=False,
                workload_type="function",
                workload_id=function.id,
                workload_name=function.name,
            )
            try:
                await _update_run_status(
                    status=run.status,
                    started_at=run.started_at,
                    user_email=user_email,
                    workspace_session_id=session.session_id,
                    workspace_process_id=None,
                )
                run.workspace_session_id = session.session_id
                async with self._keep_sandbox_alive(session):
                    executor_response = await self._execute_via_function_executor(
                        function=function,
                        run=run,
                        session=session,
                        timeout_seconds=timeout_seconds,
                        async_job=True,
                    )
                    if isinstance(executor_response, FunctionJobAcceptedResponse):
                        executor_response = await self._poll_executor_job(
                            session=session,
                            run_id=run.id,
                            timeout_seconds=timeout_seconds,
                        )
                return executor_response
            finally:
                close = getattr(session, "close", None)
                if close is not None:
                    await close()

        executor_response = await self._execute_with_sandbox_recovery(
            run=run, make_attempt=_attempt
        )
        self._apply_executor_response_to_run(run, executor_response)

        run.completed_at = datetime.now()
        await self._persist_terminal_run(function, run)
        return run

    @staticmethod
    def _is_recoverable_sandbox_error(exc: BaseException) -> bool:
        """True for errors that mean "the sandbox is internally unavailable right
        now" -- a missing/not-running pod or a manager/transport blip -- as
        opposed to a real function failure (which comes back as a 200 response,
        never an exception) or a genuine function timeout (a builtin TimeoutError
        from the poll, deliberately excluded so it stays terminal)."""
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in _RECOVERABLE_SANDBOX_STATUS_CODES
        return isinstance(exc, _RECOVERABLE_SANDBOX_TRANSPORT_ERRORS)

    async def _execute_with_sandbox_recovery(
        self,
        *,
        run: FunctionRunEntity,
        make_attempt,
    ) -> FunctionInvokeResponse:
        """Run an execution attempt, recovering from transient sandbox failures.

        A function run must execute despite internal sandbox churn: the pod being
        idle-reaped, evicted, or restarted mid-run, or a manager proxy blip. On a
        recoverable sandbox error we reprovision (the next attempt's get_session
        recreates a missing/dead pod) and re-run, bounded by
        ``_SANDBOX_RECOVERY_MAX_ATTEMPTS``. A real function failure comes back as a
        200 response (status ``failed``/``timeout``) -- never an exception -- so it
        is returned and never retried. Only a sandbox that cannot be provisioned
        (the error persists across every attempt) surfaces as a run failure.
        """
        last_exc: BaseException | None = None
        for attempt in range(1, _SANDBOX_RECOVERY_MAX_ATTEMPTS + 1):
            try:
                return await make_attempt()
            except Exception as exc:
                if (
                    not self._is_recoverable_sandbox_error(exc)
                    or attempt == _SANDBOX_RECOVERY_MAX_ATTEMPTS
                ):
                    raise
                last_exc = exc
                logger.warning(
                    "Function run %s hit a transient sandbox error on attempt "
                    "%s/%s; reprovisioning the sandbox and retrying: %s",
                    run.id,
                    attempt,
                    _SANDBOX_RECOVERY_MAX_ATTEMPTS,
                    exc,
                )
                await asyncio.sleep(
                    min(
                        _SANDBOX_RECOVERY_BACKOFF_SECONDS * attempt,
                        _SANDBOX_RECOVERY_MAX_BACKOFF_SECONDS,
                    )
                )
        # Unreachable: the final attempt returns or re-raises above.
        raise last_exc if last_exc is not None else RuntimeError(
            "sandbox recovery exhausted without result"
        )

    @contextlib.asynccontextmanager
    async def _keep_sandbox_alive(self, session):
        """Heartbeat the session's sandbox so the manager's idle reaper does not
        delete it while a JOB function runs.

        JOB functions occupy the sandbox through the function_executor app and
        never create a runtime session, so nothing else resets the sandbox idle
        clock. Best-effort: heartbeat failures are swallowed and retried on the
        next tick; a genuinely dead sandbox is surfaced by the run's own
        execute/poll retry + deadline logic. No-ops cleanly when the session has
        no manager client (e.g. in tests).
        """
        sandbox_id = getattr(session, "sandbox_id", None)
        client = getattr(session, "client", None)
        heartbeat = getattr(client, "heartbeat_sandbox", None)
        if not sandbox_id or heartbeat is None:
            yield
            return

        async def _loop() -> None:
            # Heartbeat immediately so a reused, near-idle sandbox is kept alive
            # from the first moment of the run -- waiting a full interval first
            # leaves a window where the idle reaper can delete the pod mid-run.
            first = True
            while True:
                if not first:
                    await asyncio.sleep(_SANDBOX_HEARTBEAT_INTERVAL_SECONDS)
                first = False
                try:
                    await heartbeat(sandbox_id)
                except Exception as exc:  # best-effort keepalive
                    logger.debug(
                        "sandbox heartbeat failed sandbox=%s: %s", sandbox_id, exc
                    )

        task = asyncio.create_task(_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _execute_via_function_executor(
        self,
        *,
        function: FunctionEntity,
        run: FunctionRunEntity,
        session,
        timeout_seconds: int,
        async_job: bool,
    ) -> FunctionInvokeResponse | FunctionJobAcceptedResponse:
        assert function.id is not None
        assert run.id is not None
        env_vars = getattr(session, "env_vars", {}) or {}
        lemma_token = env_vars.get("LEMMA_TOKEN")
        if not lemma_token:
            raise FunctionValidationError("Workspace session did not include LEMMA_TOKEN")
        sandbox_id = getattr(session, "sandbox_id", agentbox_sandbox_id(run.user_id))
        client = self._build_function_executor_client(lemma_token)
        try:
            # The in-sandbox function_executor app starts lazily after the VM is
            # RUNNING, so wait for it to be serving before posting, then retry the
            # execute on transient proxy errors as a backstop for the manager's
            # cached-ready race. A 200 response carrying status="failed" is a real
            # function failure and is NOT retried.
            await client.wait_until_ready(
                sandbox_id=sandbox_id,
                timeout_seconds=_FUNCTION_EXECUTOR_READY_TIMEOUT_SECONDS,
            )

            async def _do_execute():
                return await client.execute(
                    sandbox_id=sandbox_id,
                    pod_id=function.pod_id,
                    function_name=function.name,
                    request=FunctionExecuteRequest(
                        run_id=run.id,
                        input_data=run.input_data or {},
                        async_job=async_job,
                        timeout_seconds=timeout_seconds,
                    ),
                )

            # A synchronous execute is non-idempotent: a 504 means the request
            # reached the in-sandbox app and it ran past its budget without
            # responding, so re-sending could run the function again. Drop 504
            # from the retryable set for sync and surface a timeout instead. An
            # async_job execute returns immediately, so it keeps the full set
            # (its retries only ever cover the cold-start "app not ready" race).
            retryable_status_codes = (
                RETRYABLE_HTTP_STATUS_CODES
                if async_job
                else RETRYABLE_HTTP_STATUS_CODES - {504}
            )
            try:
                return await retry_on_transient_agentbox_error(
                    _do_execute,
                    max_attempts=_FUNCTION_EXECUTE_RETRY_MAX_ATTEMPTS,
                    retryable_status_codes=retryable_status_codes,
                    on_retry=lambda attempt, message: logger.info(
                        "function_executor execute not ready sandbox=%s run=%s attempt=%s: %s",
                        sandbox_id,
                        run.id,
                        attempt,
                        message,
                    ),
                )
            except httpx.HTTPStatusError as exc:
                if not async_job and exc.response.status_code == 504:
                    logger.warning(
                        "function_executor sync execute timed out at proxy "
                        "sandbox=%s run=%s",
                        sandbox_id,
                        run.id,
                    )
                    return FunctionInvokeResponse(
                        status="timeout",
                        output_data=None,
                        error=RuntimeErrorInfo(
                            name="GatewayTimeout",
                            message=(
                                "Function did not return before the execution "
                                "timeout."
                            ),
                        ),
                        logs=[],
                        code_hash="",
                        duration_ms=0,
                    )
                raise
        finally:
            await client.close()

    async def _poll_executor_job(
        self,
        *,
        session,
        run_id: UUID,
        timeout_seconds: int,
    ) -> FunctionInvokeResponse:
        env_vars = getattr(session, "env_vars", {}) or {}
        lemma_token = env_vars.get("LEMMA_TOKEN")
        if not lemma_token:
            raise FunctionValidationError("Workspace session did not include LEMMA_TOKEN")
        sandbox_id = getattr(session, "sandbox_id", None)
        if not sandbox_id:
            raise FunctionValidationError("Workspace session did not include sandbox_id")
        client = self._build_function_executor_client(lemma_token)
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                # Absorb a transient blip (the outer deadline loop provides the
                # macro retry budget); a real TimeoutError below is not retried.
                status = await retry_on_transient_agentbox_error(
                    lambda: client.get_status(sandbox_id=sandbox_id, run_id=run_id),
                    max_attempts=_FUNCTION_POLL_RETRY_MAX_ATTEMPTS,
                )
                if status.status in {"completed", "failed", "cancelled", "timeout"}:
                    logs = await retry_on_transient_agentbox_error(
                        lambda: client.get_logs(sandbox_id=sandbox_id, run_id=run_id),
                        max_attempts=_FUNCTION_POLL_RETRY_MAX_ATTEMPTS,
                    )
                    mapped_status = {
                        "completed": "completed",
                        "failed": "failed",
                        "cancelled": "cancelled",
                        "timeout": "timeout",
                    }[status.status]
                    return FunctionInvokeResponse(
                        status=mapped_status,
                        output_data=status.output_data,
                        error=status.error,
                        logs=logs.logs,
                        code_hash=status.code_hash or "",
                        duration_ms=status.duration_ms or 0,
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("Function job did not finish before timeout")
                await asyncio.sleep(_FUNCTION_POLL_INTERVAL_SECONDS)
        finally:
            await client.close()

    def _build_function_executor_client(self, lemma_token: str):
        if self.function_executor_client_factory is not None:
            return self.function_executor_client_factory(lemma_token)
        api_key = settings.agentbox_api_key
        if not api_key:
            raise RuntimeError("AGENTBOX_API_KEY is required")
        return FunctionExecutorClient(
            manager_base_url=settings.agentbox_api_url,
            manager_api_key=api_key,
            lemma_token=lemma_token,
            timeout_seconds=300.0,
        )

    def _apply_executor_response_to_run(
        self,
        run: FunctionRunEntity,
        response: FunctionInvokeResponse,
    ) -> None:
        run.logs = "\n".join(
            entry.message for entry in response.logs if entry.message
        ) or None
        if response.status == "completed":
            run.status = FunctionRunStatus.COMPLETED
            run.output_data = response.output_data
            return
        run.status = FunctionRunStatus.FAILED
        if response.error is not None:
            run.error = response.error.message
        else:
            run.error = f"Function executor returned status {response.status}"

    def _terminal_run_event(
        self, function: FunctionEntity, run: FunctionRunEntity
    ) -> DomainEvent:
        if run.status == FunctionRunStatus.COMPLETED:
            return FunctionRunCompletedEvent(
                run_id=run.id,
                function_id=function.id,
                output_data=run.output_data,
                logs=run.logs,
                completed_at=run.completed_at or datetime.now(),
                workspace_session_id=run.workspace_session_id,
                workspace_process_id=run.workspace_process_id,
            )
        return FunctionRunFailedEvent(
            run_id=run.id,
            function_id=function.id,
            error=run.error,
            logs=run.logs,
            completed_at=run.completed_at or datetime.now(),
            workspace_session_id=run.workspace_session_id,
            workspace_process_id=run.workspace_process_id,
        )

    async def _persist_terminal_run(
        self, function: FunctionEntity, run: FunctionRunEntity
    ) -> FunctionRunEntity:
        """Persist a run's terminal state AND emit its completion/failure event.

        The event wakes any workflow suspended on this function run and feeds the
        function self-projector. Use only for terminal (COMPLETED/FAILED)
        transitions; non-terminal status updates stay on plain ``update_run``.
        """
        run.add_event(self._terminal_run_event(function, run))
        if self._uow_factory is not None:
            async with self._uow_factory() as uow:
                from app.modules.function.infrastructure.repositories import (
                    FunctionRunRepository,
                )
                from app.core.infrastructure.events.message_bus import get_message_bus

                return await FunctionRunRepository(
                    uow, message_bus=get_message_bus()
                ).update_run_and_collect(
                    run,
                    status=run.status,
                    output_data=run.output_data,
                    error=run.error,
                    logs=run.logs,
                    completed_at=run.completed_at,
                    workspace_session_id=run.workspace_session_id,
                    workspace_process_id=run.workspace_process_id,
                )
        return await self.run_repository.update_run_and_collect(
            run,
            status=run.status,
            output_data=run.output_data,
            error=run.error,
            logs=run.logs,
            completed_at=run.completed_at,
            workspace_session_id=run.workspace_session_id,
            workspace_process_id=run.workspace_process_id,
        )

    def _run_job_id(self, run_id: UUID) -> str:
        return f"function:{run_id}"

    async def list_runs(
        self,
        pod_id: UUID,
        function_name: str,
        user_id: UUID,
        limit: int = 100,
        cursor: str | None = None,
        ctx: Context | None = None,
    ) -> tuple[list[FunctionRunEntity], str | None]:
        function = await self.get_function_by_name(
            pod_id,
            function_name,
            user_id,
            raise_not_found=True,
            include_code=False,
            ctx=ctx,
        )
        assert function is not None
        assert function.id is not None
        return await self.run_repository.list_runs_by_function(
            function.id, limit, cursor
        )

    async def get_run(
        self,
        pod_id: UUID,
        function_name: str,
        run_id: UUID,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> FunctionRunEntity:
        run = await self.run_repository.get_run(run_id)
        if not run:
            raise FunctionRunNotFoundError(f"Run {run_id} not found")

        function = await self.get_function_by_name(
            pod_id,
            function_name,
            user_id,
            raise_not_found=True,
            include_code=False,
            ctx=ctx,
        )
        assert function is not None
        if run.function_id != function.id:
            raise FunctionValidationError(
                "Run does not belong to the specified function"
            )
        return run

    async def _extract_schemas(
        self, user_id: UUID, code: str, code_path: str, pod_id: UUID, function_id: UUID
    ) -> tuple[dict, dict, dict | None]:
        del code_path  # persisted for future validation/logging parity
        session = await self.workspace_service.get_session(
            user_id=user_id,
            pod_id=pod_id,
            session_id=str(function_id),
            initial_cwd=f"tasks/{function_id}",
            close_on_exit=False,
            workload_type="function",
            workload_id=function_id,
        )

        async with session:
            input_model, output_model, _, config_model = self._parse_code_headers(code)
            config_schema_expr = (
                f"{config_model}.model_json_schema()" if config_model else "None"
            )
            schema_extract_code = (
                f"{code}\n\n"
                "import json\n"
                f"print('{self._SCHEMA_OUTPUT_MARKER}' + json.dumps({{'input': {input_model}.model_json_schema(), 'output': {output_model}.model_json_schema(), 'config': {config_schema_expr}}}))\n"
            )
            result = await session.execute_code(schema_extract_code)
            if not result.success:
                raise FunctionValidationError(
                    self._build_execution_error_message(
                        result, stage="schema extraction"
                    ),
                    details=self._build_execution_error_details(
                        result, stage="schema_extraction"
                    ),
                )

            try:
                payload = self._extract_marked_json(
                    result.stdout,
                    self._SCHEMA_OUTPUT_MARKER,
                )
                if not isinstance(payload, dict):
                    raise FunctionValidationError(
                        "Function code ran but did not emit valid schema output.",
                        details={
                            "stage": "schema_extraction",
                            "stdout": result.stdout,
                            "expected_marker": self._SCHEMA_OUTPUT_MARKER,
                        },
                    )
                input_schema = payload.get("input")
                output_schema = payload.get("output")
                config_schema = payload.get("config")
                if not isinstance(input_schema, dict) or not isinstance(
                    output_schema, dict
                ):
                    raise FunctionValidationError(
                        "Function code emitted invalid input or output schema data.",
                        details={
                            "stage": "schema_extraction",
                            "stdout": result.stdout,
                            "parsed_payload": payload,
                        },
                    )
                if config_schema is not None and not isinstance(config_schema, dict):
                    raise FunctionValidationError(
                        "Function code emitted invalid config schema data.",
                        details={
                            "stage": "schema_extraction",
                            "stdout": result.stdout,
                            "parsed_payload": payload,
                        },
                    )
                return input_schema, output_schema, config_schema
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise FunctionValidationError(
                    "Function code emitted invalid JSON schema output.",
                    details={
                        "stage": "schema_extraction",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "parse_error": str(exc),
                    },
                ) from exc
