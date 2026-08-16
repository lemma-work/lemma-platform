"""Function service."""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.authorization.context import (
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.delegation_revocation import revoke_delegation
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.modules.icon.contracts import IconCleanupPort
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
    FunctionUpdateEntity,
)
from app.modules.function.domain.identities import function_run_job_id
from app.modules.function.domain.errors import (
    FunctionConflictError,
    FunctionNotFoundError,
    FunctionRunNotFoundError,
    FunctionValidationError,
)
from app.modules.function.domain.ports import (
    FunctionStorageFactoryPort,
    FunctionRepositoryPort,
    FunctionRunRepositoryPort,
)
from app.modules.function.domain.types import JsonObject

from app.modules.pod.contracts import PodRole
from app.core.log.log import get_logger

logger = get_logger(__name__)

# A function's `#python_packages:` header declares pip dependencies that the
# immutable artifact builder resolves before the revision becomes READY.
_MAX_PYTHON_PACKAGES = 30
_MAX_PACKAGE_SPEC_LENGTH = 128
_PYTHON_PACKAGE_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"  # distribution name
    r"(\[[A-Za-z0-9._,-]+\])?"  # optional extras
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


class LegacyFunctionRevisionRequired(Exception):
    """Internal control flow for a pre-artifact function definition."""

    def __init__(self, function: FunctionEntity):
        super().__init__(function.name)
        self.function = function


@dataclass(slots=True)
class FunctionUpdatePlan:
    """In-memory-mutated function plus what the sandbox/persist phases need: the
    new ``code`` to write+extract (or None), its ``code_path``, and the prior
    icon url for post-persist cleanup."""

    function: FunctionEntity
    old_icon_url: str | None
    code: str | None


class FunctionService:
    """Application service for function use-cases."""

    def __init__(
        self,
        function_repository: FunctionRepositoryPort,
        run_repository: FunctionRunRepositoryPort,
        storage_factory: FunctionStorageFactoryPort,
        icon_service: IconCleanupPort | None = None,
    ):
        # Bound mode only: real repositories + authorization. The use-case layer
        # builds one of these per short UoW; long-running sandbox work lives in
        # FunctionDefinitionCompiler / FunctionUseCases, never here.
        self.repository = function_repository
        self.run_repository = run_repository
        self.storage_factory = storage_factory
        self.icon_service = icon_service

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

    async def activate_revision_if_missing(
        self,
        function_id: UUID,
        *,
        expected_code_path: str,
        revision_hash: str,
        code_path: str,
    ) -> FunctionEntity | None:
        return await self.repository.activate_revision_if_missing(
            function_id,
            expected_code_path=expected_code_path,
            revision_hash=revision_hash,
            code_path=code_path,
        )

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
        # Revoke any in-flight delegated token minted for this function so it
        # stops working immediately rather than lingering until the token expires.
        await revoke_delegation(actor_id=function.id)
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
        if function.id is None:
            raise FunctionValidationError(
                "Function must be persisted before reading code"
            )
        storage = self.storage_factory(function.id)
        # Object storage, not the database. Reached from `get_function_by_name`
        # on the permission paths, so without this the request-scoped connection
        # is checked out for the length of a bucket read that never touches it.
        async with connection_released(getattr(self.repository, "session", None)):
            code = await storage.read_file(function.code_path)
        if isinstance(code, bytes):
            code = code.decode("utf-8")
        function.code = code
        return code

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
            function.visibility = _normalize_function_visibility(
                update_entity.visibility
            )

        code = update_entity.code or None

        if update_entity.description is not None:
            function.description = update_entity.description
        if "icon_url" in update_entity.model_fields_set:
            function.icon_url = update_entity.icon_url
        if (
            "config" in update_entity.model_fields_set
            and update_entity.config is not None
        ):
            function.config = update_entity.config
        if update_entity.type is not None:
            function.type = update_entity.type

        return FunctionUpdatePlan(
            function=function,
            old_icon_url=old_icon_url,
            code=code,
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
        input_data: JsonObject,
        user_id: UUID,
        user_email: str | None,
        *,
        ctx: Context,
        dispatch_mode: FunctionDispatchMode | None = None,
    ) -> ResolvedExecution:
        """Authorize FUNCTION_EXECUTE and persist its one PENDING run. DB only.

        Queue publication is deliberately owned by ``FunctionUseCases`` after
        this transaction commits and releases its connection.
        """
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

        if function.status != FunctionStatus.READY:
            raise FunctionValidationError("Function has no ready executable revision")
        if function.revision_hash is None:
            if function.code_path is not None:
                raise LegacyFunctionRevisionRequired(function)
            raise FunctionValidationError("Function has no ready executable revision")

        run_entity = FunctionRunEntity(
            id=uuid7(),
            function_id=function.id,
            revision_hash=function.revision_hash,
            user_id=user_id,
            user_email=user_email,
            input_data=input_data,
            status=FunctionRunStatus.PENDING,
            deadline_at=datetime.now(timezone.utc)
            + timedelta(
                seconds=(
                    settings.function_job_deadline_seconds
                    if function.type == FunctionType.JOB
                    else settings.function_api_deadline_seconds
                )
            ),
        )
        assert run_entity.id is not None
        effective_mode = dispatch_mode or (
            FunctionDispatchMode.ASYNCHRONOUS
            if function.type == FunctionType.JOB
            else FunctionDispatchMode.SYNCHRONOUS
        )
        if effective_mode == FunctionDispatchMode.ASYNCHRONOUS:
            run_entity.job_id = function_run_job_id(run_entity.id)
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
