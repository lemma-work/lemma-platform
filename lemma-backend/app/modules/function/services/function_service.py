"""Function service."""

from __future__ import annotations

import hashlib
import contextlib
import re
from dataclasses import dataclass
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
)
from app.modules.function.domain.errors import (
    FunctionConflictError,
    FunctionNotFoundError,
    FunctionRunNotFoundError,
    FunctionValidationError,
)
from app.modules.function.domain.events import (
    FunctionRunExecutionRequestedEvent,
)
from app.modules.function.domain.ports import (
    FunctionStorageFactoryPort,
    FunctionRepositoryPort,
    FunctionRunRepositoryPort,
    WorkspaceSessionPort,
)

from app.modules.pod.domain.pod_entities import PodRole
from app.core.log.log import get_logger

logger = get_logger(__name__)

# The execution engine owns the sandbox machinery + its run-status writers.
from app.modules.function.application.function_run_executor import (  # noqa: E402
    FunctionRunExecutor,
)

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


def _normalize_function_visibility(value: ResourceVisibility | str | None) -> str:
    if value is None:
        return ResourceVisibility.POD.value
    raw = value.value if isinstance(value, ResourceVisibility) else str(value)
    try:
        visibility = ResourceVisibility(raw.upper())
    except ValueError as exc:
        raise FunctionValidationError(f"Invalid visibility: {value}") from exc
    return visibility.value


def parse_python_packages(code: str) -> list[str]:
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


@dataclass(frozen=True, slots=True)
class ResolvedExecution:
    """A resolved + authorized function and its freshly-created PENDING run,
    handed from the DB resolve phase to the sandbox execution phase."""

    function: FunctionEntity
    run: FunctionRunEntity


@dataclass(slots=True)
class FunctionUpdatePlan:
    """In-memory-mutated function plus what the sandbox/persist phases need: the
    new ``code`` to write+extract (or None), its ``code_path``, and the prior
    icon url for post-persist cleanup."""

    function: FunctionEntity
    old_icon_url: str | None
    code: str | None
    code_path: str | None


class FunctionService:
    """Application service for function use-cases."""

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
    ):
        # Bound mode only: real repositories + authorization. The use-case layer
        # builds one of these per short UoW; the long-running sandbox sagas live
        # in FunctionRunExecutor / FunctionUseCases, never here.
        self.repository = function_repository
        self.run_repository = run_repository
        self.workspace_service = workspace_service
        self.storage_factory = storage_factory
        self.job_queue = job_queue
        self.icon_service = icon_service
        self.authorization_service = authorization_service
        self.function_executor_client_factory = function_executor_client_factory
        # A bound execution engine (status writes go through the bound
        # run_repository). The leak-safe production path uses a factory-mode engine
        # built by FunctionUseCases instead.
        self._executor = FunctionRunExecutor(
            uow_factory=None,
            run_repository=run_repository,
            workspace_service=workspace_service,
            storage_factory=storage_factory,
            function_executor_client_factory=function_executor_client_factory,
        )

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

    # -- Bound DB helper ---------------------------------------------------
    #
    # FunctionService is bound mode only: every DB step runs against the bound
    # repositories (within the caller's short UoW). The leak-safe orchestration
    # across short UoWs lives in FunctionUseCases.

    @contextlib.asynccontextmanager
    async def _repos(self):
        """Yield ``(function_repository, run_repository)`` for one DB step."""
        yield self.repository, self.run_repository

    async def _load_function_by_name(
        self, pod_id: UUID, name: str, *, ctx: Context | None = None
    ) -> FunctionEntity | None:
        async with self._repos() as (function_repository, _run_repository):
            return await function_repository.get_by_name(pod_id, name, ctx=ctx)

    async def _create_run(self, run_entity: FunctionRunEntity) -> FunctionRunEntity:
        async with self._repos() as (_function_repository, run_repository):
            return await run_repository.create_run(run_entity)

    async def _create_function_checked(self, entity: FunctionEntity) -> FunctionEntity:
        async with self._repos() as (function_repository, _run_repository):
            existing = await function_repository.get_by_name(entity.pod_id, entity.name)
            if existing:
                raise FunctionConflictError(
                    f"Function with name '{entity.name}' already exists "
                    f"in pod {entity.pod_id}"
                )
            return await function_repository.create(entity)

    async def _update_function_row(self, function: FunctionEntity) -> FunctionEntity:
        async with self._repos() as (function_repository, _run_repository):
            return await function_repository.update(function)

    async def _delete_function_row(self, function_id: UUID) -> bool:
        async with self._repos() as (function_repository, _run_repository):
            return await function_repository.delete(function_id)

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

        entity.user_id = user_id
        entity.visibility = _normalize_function_visibility(entity.visibility)
        await self._validate_resources(entity)
        # Conflict check + insert in one short UoW (released before schema work).
        created = await self._create_function_checked(entity)
        assert created.id is not None

        if not code:
            return created

        # storage write + schema extraction provision/run a sandbox — keep them
        # OUT of any DB session so a pooled connection is not held for the
        # (multi-second) round-trip. Persist the extracted schemas in a fresh
        # short UoW afterwards.
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
        return await self._update_function_row(created)

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
        async with self._repos() as (function_repository, _run_repository):
            function = await function_repository.get_by_name(pod_id, name, ctx=ctx)
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

        updated = await self._update_function_row(function)
        if self.icon_service and old_icon_url != updated.icon_url:
            await self.icon_service.delete_by_url(old_icon_url)
        if ctx is not None:
            async with self._repos() as (function_repository, _run_repository):
                refreshed = await function_repository.get_by_name(pod_id, name, ctx=ctx)
            return refreshed or updated
        return updated

    async def delete_function(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> bool:
        function = await self._load_function_by_name(pod_id, name, ctx=ctx)
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

        deleted = await self._delete_function_row(function.id)
        if not deleted:
            raise FunctionNotFoundError(f"Function {name} not found")
        # Icon cleanup is a storage call — run it after the DB session closes so
        # no pooled connection is held across it.
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

    def _parse_python_packages(self, code: str) -> list[str]:
        return parse_python_packages(code)

    # -- Per-phase methods for the use-case layer (bound mode, no sandbox) -----
    #
    # Each runs a single DB step against the bound repositories (within the
    # caller's short pod_context_scope) and returns plain entities/plans. The
    # use case sequences these around the sandbox phases the executor owns, so a
    # pooled connection is never held across the sandbox round-trip.

    async def resolve_create(
        self, entity: FunctionEntity, user_id: UUID, *, ctx: Context
    ) -> FunctionEntity:
        """Authorize FUNCTION_CREATE + normalize + conflict-check + insert the
        PENDING/DRAFT row. DB only."""
        await ctx.require(Permissions.FUNCTION_CREATE, ResourceRef.pod(entity.pod_id))
        entity.user_id = user_id
        entity.visibility = _normalize_function_visibility(entity.visibility)
        await self._validate_resources(entity)
        return await self._create_function_checked(entity)

    async def persist_create(self, function: FunctionEntity) -> FunctionEntity:
        """Persist the schema/code fields onto the created row. DB only."""
        return await self._update_function_row(function)

    async def resolve_update(
        self,
        pod_id: UUID,
        name: str,
        update_entity: FunctionUpdateEntity,
        user_id: UUID,
        *,
        ctx: Context,
    ) -> FunctionUpdatePlan:
        """Load + authorize FUNCTION_UPDATE + apply the non-code in-memory
        mutations, returning a plan. The code write + schema extraction happen
        outside (sandbox); ``persist_update`` then writes the row."""
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

        code = update_entity.code or None
        code_path = function.code_path
        if code and not code_path:
            code_path = f"{slugify(function.name)}.py"

        if update_entity.description is not None:
            function.description = update_entity.description
        if "icon_url" in update_entity.model_fields_set:
            function.icon_url = update_entity.icon_url
        if "config" in update_entity.model_fields_set and update_entity.config is not None:
            function.config = update_entity.config
        if update_entity.type is not None:
            function.type = update_entity.type

        return FunctionUpdatePlan(
            function=function,
            old_icon_url=old_icon_url,
            code=code,
            code_path=code_path if code else None,
        )

    async def persist_update(
        self,
        plan: FunctionUpdatePlan,
        *,
        pod_id: UUID,
        name: str,
        ctx: Context,
    ) -> FunctionEntity:
        """Persist the mutated row and re-read it (with ctx, for allowed_actions).
        DB only."""
        updated = await self._update_function_row(plan.function)
        async with self._repos() as (function_repository, _run_repository):
            refreshed = await function_repository.get_by_name(pod_id, name, ctx=ctx)
        return refreshed or updated

    async def resolve_delete(
        self, pod_id: UUID, name: str, user_id: UUID, *, ctx: Context
    ) -> FunctionEntity:
        """Authorize FUNCTION_DELETE + delete the row (+grants). DB only. Returns
        the deleted entity so the caller can purge its icon afterwards."""
        function = await self._load_function_by_name(pod_id, name, ctx=ctx)
        if function is None:
            raise FunctionNotFoundError(f"Function {name} not found")
        assert function.id is not None
        await ctx.require(
            Permissions.FUNCTION_DELETE,
            ResourceRef(
                resource_type=ResourceType.FUNCTION,
                resource_id=function.id,
                pod_id=pod_id,
            ),
        )
        deleted = await self._delete_function_row(function.id)
        if not deleted:
            raise FunctionNotFoundError(f"Function {name} not found")
        return function

    async def resolve_execute(
        self,
        pod_id: UUID,
        name: str,
        input_data: dict,
        user_id: UUID,
        user_email: str | None,
        *,
        ctx: Context,
    ) -> ResolvedExecution:
        """Authorize FUNCTION_EXECUTE + create the PENDING run (+ JOB enqueue
        event on the same UoW). DB only. The function is loaded directly (execute
        needs only FUNCTION_EXECUTE, not FUNCTION_READ)."""
        function = await self._load_function_by_name(pod_id, name, ctx=ctx)
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
        run = await self._create_run(run_entity)
        return ResolvedExecution(function=function, run=run)

    async def load_run_and_function(
        self, run_id: UUID
    ) -> tuple[FunctionEntity, FunctionRunEntity]:
        """Load a run + its function for the worker path. NO ctx — the worker
        trusts a run that was authorized + persisted at enqueue time. DB only."""
        run = await self.run_repository.get_run(run_id)
        if run is None:
            raise FunctionRunNotFoundError(f"Run {run_id} not found")
        function = await self.repository.get(run.function_id)
        if function is None:
            raise FunctionNotFoundError(f"Function {run.function_id} not found")
        return function, run

    async def delete_old_icon(
        self, old_icon_url: str | None, new_icon_url: str | None
    ) -> None:
        """Best-effort icon cleanup (storage only, no DB) for an update that
        changed the icon. Safe to call after the persist UoW closed."""
        if self.icon_service and old_icon_url and old_icon_url != new_icon_url:
            await self.icon_service.delete_by_url(old_icon_url)

    async def delete_icon(self, icon_url: str | None) -> None:
        """Best-effort icon cleanup (storage only, no DB) after a delete."""
        if self.icon_service and icon_url:
            await self.icon_service.delete_by_url(icon_url)

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
        # Schema extraction is a sandbox round-trip — delegated to the execution
        # engine. Kept as a thin method so create/update can call it and tests can
        # patch it on the service instance.
        return await self._executor.extract_schemas(
            user_id, code, code_path, pod_id, function_id
        )

