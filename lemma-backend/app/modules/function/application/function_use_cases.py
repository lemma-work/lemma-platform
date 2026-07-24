"""Application/use-case layer for the function sagas.

Each operation (create / update / delete / execute) has its HOME here: one method
that owns the phase sequencing across SHORT units of work + the sandbox execution
engine, so a pooled DB connection is never held across the multi-second sandbox
round-trip. Authorization (``ctx.require``) always runs inside a short UoW whose
session is live; the engine then runs the sandbox with no ctx and no connection.

The same object serves every caller — the API controller (request ctx), the
worker (no ctx; trusts the persisted run), the agent-as-tool path (delegated
workload ctx, built + used inside one live UoW), and the workflow adapter (user
ctx).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Request
from opentelemetry.trace import SpanKind

from app.core.authorization.scope import context_scope, pod_context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.config import settings
from app.core.helpers.slug import slugify
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.application.function_definition_compiler import (
    FunctionDefinitionCompiler,
)
from app.modules.function.application.function_observability import (
    function_span,
    mark_span_outcome,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionEntity,
    FunctionRunEntity,
    FunctionStatus,
    FunctionType,
    FunctionUpdateEntity,
    RunAsWorkload,
)
from app.modules.function.domain.errors import (
    FunctionRunQueueUnavailable,
    FunctionValidationError,
)
from app.modules.function.domain.ports import FunctionExecutionPort
from app.modules.function.domain.ports import FunctionRunQueuePort
from app.modules.function.domain.types import JsonObject
from app.modules.function.services.function_service import (
    FunctionService,
    LegacyFunctionRevisionRequired,
    ResolvedExecution,
    parse_python_packages,
)
from app.core.log.log import get_logger


logger = get_logger(__name__)


class FunctionUseCases:
    """Owns the function sagas. Built from a uow_factory + a per-phase bound
    service builder + the sandbox execution engine."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        service_builder: Callable[[SqlAlchemyUnitOfWork], FunctionService],
        compiler: FunctionDefinitionCompiler,
        dispatcher: FunctionExecutionPort,
        run_queue: FunctionRunQueuePort,
    ):
        self._uow_factory = uow_factory
        self._build = service_builder
        self._compiler = compiler
        self._dispatcher = dispatcher
        self._run_queue = run_queue

    # -- Code-bearing create/update assembly (sandbox, no connection) ---------

    async def _apply_code(
        self,
        function: FunctionEntity,
        code: str,
        display_path: str,
        user_id: UUID,
    ) -> None:
        """Build and stage one immutable executable revision with no DB connection."""
        if function.id is None:
            raise ValueError("function must be persisted before code is compiled")
        # Fail fast on a bad dependency spec before the heavier schema extraction.
        python_packages = tuple(parse_python_packages(code))
        (
            input_schema,
            output_schema,
            config_schema,
        ) = await self._compiler.extract_schemas(
            user_id, code, display_path, function.pod_id, function.id
        )
        function.input_schema = input_schema
        function.output_schema = output_schema
        function.config_schema = config_schema
        function.pending_artifact = await self._compiler.build_artifact(
            function,
            code,
            python_packages=python_packages,
        )
        function.revision_hash = function.pending_artifact.revision_hash
        function.code_path = (
            f"revisions/{function.revision_hash.removeprefix('sha256:')}/function.py"
        )
        # Source activation is content-addressed. A failed update can leave an
        # unreferenced artifact/source, but it cannot overwrite the code belonging
        # to the still-active database revision.
        await self._compiler.write_code(function.id, function.code_path, code)
        function.status = FunctionStatus.READY

    async def _backfill_legacy_revision(self, function: FunctionEntity) -> None:
        """Compile and atomically activate a pre-artifact function definition."""
        if function.id is None or function.code_path is None:
            raise FunctionValidationError(
                "Function has no source available for revision migration"
            )
        legacy_code_path = function.code_path
        code = await self._compiler.read_code(function.id, legacy_code_path)
        artifact = await self._compiler.build_artifact(
            function,
            code,
            python_packages=tuple(parse_python_packages(code)),
        )
        revision_code_path = (
            f"revisions/{artifact.revision_hash.removeprefix('sha256:')}/function.py"
        )
        await self._compiler.write_code(function.id, revision_code_path, code)

        async with uow_scope(self._uow_factory) as uow:
            activated = await self._build(uow).activate_revision_if_missing(
                function.id,
                expected_code_path=legacy_code_path,
                revision_hash=artifact.revision_hash,
                code_path=revision_code_path,
            )
        if activated is None or activated.revision_hash is None:
            raise FunctionValidationError(
                "Function revision migration did not activate an executable revision"
            )
        logger.info(
            "function.use_cases.legacy_revision_backfilled",
            function_id=str(function.id),
            pod_id=str(function.pod_id),
            revision_hash=activated.revision_hash,
        )

    async def _resolve_with_revision_backfill(
        self,
        resolve_once: Callable[[], Awaitable[ResolvedExecution]],
    ) -> ResolvedExecution:
        try:
            return await resolve_once()
        except LegacyFunctionRevisionRequired as required:
            await self._backfill_legacy_revision(required.function)
        try:
            return await resolve_once()
        except LegacyFunctionRevisionRequired as exc:
            raise FunctionValidationError(
                "Function revision migration did not activate an executable revision"
            ) from exc

    # -- API-path operations (request ctx) ------------------------------------

    async def create_function(
        self,
        *,
        pod_id: UUID,
        entity: FunctionEntity,
        user_id: UUID,
        code: str | None,
        request: Request,
    ) -> FunctionEntity:
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            created = await self._build(scope.uow).resolve_create(
                entity, user_id, ctx=scope.ctx
            )
        if not code:
            return created

        # Sandbox phase — no pooled connection held.
        await self._apply_code(
            created,
            code,
            f"{slugify(created.name)}.py",
            user_id,
        )

        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope2:
            service = self._build(scope2.uow)
            await service.persist_create(created)
            refreshed = await service.get_function_by_name(
                pod_id,
                created.name,
                user_id,
                raise_not_found=True,
                include_code=False,
                ctx=scope2.ctx,
            )
        return refreshed or created

    async def update_function(
        self,
        *,
        pod_id: UUID,
        name: str,
        update_entity: FunctionUpdateEntity,
        user_id: UUID,
        request: Request,
    ) -> FunctionEntity:
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            plan = await self._build(scope.uow).resolve_update(
                pod_id, name, update_entity, user_id, ctx=scope.ctx
            )

        # Sandbox phase — no connection held.
        if plan.code is not None:
            await self._apply_code(
                plan.function,
                plan.code,
                f"{slugify(plan.function.name)}.py",
                user_id,
            )

        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope2:
            service = self._build(scope2.uow)
            refreshed = await service.persist_update(
                plan, pod_id=pod_id, name=name, ctx=scope2.ctx
            )
        # Icon cleanup is a storage call — run it with no connection held.
        await service.delete_old_icon(plan.old_icon_url, refreshed.icon_url)
        return refreshed

    async def upsert_function_for_import(
        self,
        *,
        entity: FunctionEntity,
        update_entity: FunctionUpdateEntity,
        code: str | None,
        user_id: UUID,
    ) -> FunctionEntity:
        """Idempotently apply one bundle function without spanning external I/O.

        Unlike the HTTP methods this has no request object, so it builds the
        importing user's authorization context in each short UoW. Schema
        extraction and artifact construction happen between those UoWs.
        """

        pod_id = entity.pod_id
        async with uow_scope(self._uow_factory) as uow:
            auth_ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(auth_ctx):
                service = self._build(uow)
                existing = await service.get_function_by_name(
                    pod_id,
                    entity.name,
                    user_id,
                    raise_not_found=False,
                    include_code=False,
                    ctx=auth_ctx,
                )
                if existing is None:
                    created = await service.resolve_create(
                        entity, user_id, ctx=auth_ctx
                    )
                    operation = "create"
                    plan = None
                else:
                    plan = await service.resolve_update(
                        pod_id,
                        entity.name,
                        update_entity,
                        user_id,
                        ctx=auth_ctx,
                    )
                    created = None
                    operation = "update"

        if operation == "create":
            assert created is not None
            if code is None:
                return created
            await self._apply_code(
                created,
                code,
                f"{slugify(created.name)}.py",
                user_id,
            )
        else:
            assert plan is not None
            if plan.code is not None:
                await self._apply_code(
                    plan.function,
                    plan.code,
                    f"{slugify(plan.function.name)}.py",
                    user_id,
                )

        async with uow_scope(self._uow_factory) as uow:
            auth_ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(auth_ctx):
                service = self._build(uow)
                if operation == "create":
                    assert created is not None
                    await service.persist_create(created)
                    refreshed = await service.get_function_by_name(
                        pod_id,
                        created.name,
                        user_id,
                        raise_not_found=True,
                        include_code=False,
                        ctx=auth_ctx,
                    )
                    result = refreshed or created
                    old_icon_url = None
                else:
                    assert plan is not None
                    result = await service.persist_update(
                        plan,
                        pod_id=pod_id,
                        name=entity.name,
                        ctx=auth_ctx,
                    )
                    old_icon_url = plan.old_icon_url

        await service.delete_old_icon(old_icon_url, result.icon_url)
        return result

    async def delete_function(
        self, *, pod_id: UUID, name: str, user_id: UUID, request: Request
    ) -> None:
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            function = await service.resolve_delete(
                pod_id, name, user_id, ctx=scope.ctx
            )
        # Icon cleanup is a storage call — no connection held.
        await service.delete_icon(function.icon_url)

    async def execute_function(
        self,
        *,
        pod_id: UUID,
        name: str,
        input_data: JsonObject,
        user_id: UUID,
        user_email: str | None,
        request: Request,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        async def resolve_once() -> ResolvedExecution:
            async with pod_context_scope(
                self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
            ) as scope:
                return await self._build(scope.uow).resolve_execute(
                    pod_id, name, input_data, user_id, user_email, ctx=scope.ctx
                )

        resolved = await self._resolve_with_revision_backfill(resolve_once)
        return await self._run_resolved(
            resolved, user_email=user_email, run_as_workload=run_as_workload
        )

    # -- Worker path (no ctx) -------------------------------------------------

    async def execute_run_by_id(self, run_id: UUID) -> FunctionRunEntity:
        return await self._dispatcher.execute(
            run_id,
            mode=FunctionDispatchMode.ASYNCHRONOUS,
        )

    # -- Agent-as-tool path (delegated workload ctx) --------------------------

    async def execute_function_as_workload(
        self,
        *,
        pod_id: UUID,
        name: str,
        input_data: JsonObject,
        user_id: UUID,
        principal_type: str,
        principal_id: UUID,
        delegation_scope,
        delegation_actor_name: str | None,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        async def resolve_once() -> ResolvedExecution:
            # Build the delegated ctx AND resolve inside one live UoW, so
            # ctx.require's resource hydration never touches a closed session.
            async with uow_scope(self._uow_factory) as uow:
                auth_ctx = await AuthorizationDataService(
                    uow.session
                ).build_delegated_workload_context(
                    user_id=user_id,
                    principal_type=principal_type,
                    principal_id=principal_id,
                    pod_id=pod_id,
                    delegation_scope=delegation_scope,
                    delegation_actor_name=delegation_actor_name,
                )
                async with context_scope(auth_ctx):
                    return await self._build(uow).resolve_execute(
                        pod_id, name, input_data, user_id, None, ctx=auth_ctx
                    )

        resolved = await self._resolve_with_revision_backfill(resolve_once)
        return await self._run_resolved(
            resolved, user_email=None, run_as_workload=run_as_workload
        )

    # -- Workflow path (user ctx) ---------------------------------------------

    async def execute_function_for_user(
        self,
        *,
        pod_id: UUID,
        name: str,
        input_data: JsonObject,
        user_id: UUID,
    ) -> FunctionRunEntity:
        async def resolve_once() -> ResolvedExecution:
            async with uow_scope(self._uow_factory) as uow:
                auth_ctx = await AuthorizationDataService(
                    uow.session
                ).build_user_context(
                    user_id=user_id,
                    pod_id=pod_id,
                )
                async with context_scope(auth_ctx):
                    return await self._build(uow).resolve_execute(
                        pod_id, name, input_data, user_id, None, ctx=auth_ctx
                    )

        resolved = await self._resolve_with_revision_backfill(resolve_once)
        return await self._run_resolved(resolved, user_email=None)

    async def dispatch_function_for_workflow(
        self,
        *,
        pod_id: UUID,
        name: str,
        input_data: JsonObject,
        user_id: UUID,
    ) -> FunctionRunEntity:
        """Create + dispatch a function run for a workflow node WITHOUT running it
        inline. Works for API and JOB functions alike: the run is enqueued to the
        worker with an explicit asynchronous dispatch mode and returned PENDING,
        so the workflow engine
        suspends on the run id and releases its run-row lock instead of pinning it
        across the sandbox round-trip. The FunctionRunCompleted event resumes the
        workflow."""
        async def resolve_once() -> ResolvedExecution:
            async with uow_scope(self._uow_factory) as uow:
                auth_ctx = await AuthorizationDataService(
                    uow.session
                ).build_user_context(
                    user_id=user_id,
                    pod_id=pod_id,
                )
                async with context_scope(auth_ctx):
                    return await self._build(uow).resolve_execute(
                        pod_id,
                        name,
                        input_data,
                        user_id,
                        None,
                        ctx=auth_ctx,
                        dispatch_mode=FunctionDispatchMode.ASYNCHRONOUS,
                    )

        resolved = await self._resolve_with_revision_backfill(resolve_once)
        return await self._enqueue_run(resolved.run)

    # -- Shared dispatch ------------------------------------------------------

    async def _run_resolved(
        self,
        resolved,
        *,
        user_email: str | None,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        function, run = resolved.function, resolved.run
        # JOB runs use the one backend queue. Publication happens only after the
        # run transaction above has committed and released its connection.
        if function.type == FunctionType.JOB:
            return await self._enqueue_run(run)
        del function, user_email, run_as_workload
        with function_span(
            "function.execution.accepted",
            execution_mode="synchronous",
            runtime_profile=settings.agentbox_function_profile_name,
        ) as span:
            try:
                result = await self._dispatcher.execute(
                    run.id,
                    mode=FunctionDispatchMode.SYNCHRONOUS,
                )
            except BaseException as exc:
                mark_span_outcome(
                    span,
                    "failed",
                    error_type=type(exc).__name__,
                )
                raise
            mark_span_outcome(span, "completed")
            return result

    async def _enqueue_run(self, run: FunctionRunEntity) -> FunctionRunEntity:
        """Best-effort fast publish backed by durable pending-run reconciliation.

        The run and its deterministic ``job_id`` dispatch intent are already
        committed when this method is called. If publication has an ambiguous
        outcome, the reconciler safely republishes the same task identity and
        the run claim prevents a second execution.
        """

        if run.id is None:
            raise ValueError("function run must be persisted before enqueue")
        with function_span(
            "function.execution.accepted",
            execution_mode="asynchronous",
            runtime_profile=settings.agentbox_function_profile_name,
            kind=SpanKind.PRODUCER,
        ) as span:
            try:
                job_id = await self._run_queue.enqueue(run.id)
            except FunctionRunQueueUnavailable as exc:
                # Durable reconciliation owns recovery; one state-transition
                # warning is sufficient and the function remains accepted.
                logger.warning(
                    "function.use_cases.run_enqueue_deferred.degraded",
                    run_id=str(run.id),
                    error_type=type(exc).__name__,
                )
                mark_span_outcome(
                    span,
                    "failed",
                    error_type=type(exc).__name__,
                )
                return run
            mark_span_outcome(span, "completed")
        if run.job_id != job_id:
            raise RuntimeError("function run queue returned an unexpected job identity")
        return run
