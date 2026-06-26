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

import hashlib
from typing import Any, Callable
from uuid import UUID

from fastapi import Request

from app.core.authorization.scope import context_scope, pod_context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.helpers.slug import slugify
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.function.application import function_run_executor as _engine
from app.modules.function.application.function_run_executor import (
    FunctionRunExecutor,
    _JOB_FUNCTION_TIMEOUT_SECONDS,
)
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionStatus,
    FunctionType,
    FunctionUpdateEntity,
    RunAsWorkload,
)
from app.modules.function.services.function_service import (
    FunctionService,
    parse_python_packages,
)


class FunctionUseCases:
    """Owns the function sagas. Built from a uow_factory + a per-phase bound
    service builder + the sandbox execution engine."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        service_builder: Callable[[Any], FunctionService],
        executor: FunctionRunExecutor,
    ):
        self._uow_factory = uow_factory
        self._build = service_builder
        self._executor = executor

    # -- Code-bearing create/update assembly (sandbox, no connection) ---------

    async def _apply_code(
        self, function: FunctionEntity, code: str, code_path: str, user_id: UUID
    ) -> None:
        """Write the code, parse packages (fail-fast), extract schemas in the
        sandbox, and stamp the results onto the entity. Holds NO DB connection."""
        await self._executor.write_code(function.id, code_path, code)
        # Fail fast on a bad dependency spec before the heavier schema extraction.
        function.python_packages = parse_python_packages(code)
        input_schema, output_schema, config_schema = await self._executor.extract_schemas(
            user_id, code, code_path, function.pod_id, function.id
        )
        function.input_schema = input_schema
        function.output_schema = output_schema
        function.config_schema = config_schema
        function.code_path = code_path
        function.code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        function.status = FunctionStatus.READY

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
        await self._apply_code(created, code, f"{slugify(created.name)}.py", user_id)

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
            await self._apply_code(plan.function, plan.code, plan.code_path, user_id)

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

    async def delete_function(
        self, *, pod_id: UUID, name: str, user_id: UUID, request: Request
    ) -> None:
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            function = await service.resolve_delete(pod_id, name, user_id, ctx=scope.ctx)
        # Icon cleanup is a storage call — no connection held.
        await service.delete_icon(function.icon_url)

    async def execute_function(
        self,
        *,
        pod_id: UUID,
        name: str,
        input_data: dict,
        user_id: UUID,
        user_email: str | None,
        request: Request,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            resolved = await self._build(scope.uow).resolve_execute(
                pod_id, name, input_data, user_id, user_email, ctx=scope.ctx
            )
        return await self._run_resolved(
            resolved, user_email=user_email, run_as_workload=run_as_workload
        )

    # -- Worker path (no ctx) -------------------------------------------------

    async def execute_run_by_id(
        self, run_id: UUID, *, timeout_seconds: int = _JOB_FUNCTION_TIMEOUT_SECONDS
    ) -> FunctionRunEntity:
        async with uow_scope(self._uow_factory) as uow:
            function, run = await self._build(uow).load_run_and_function(run_id)
        return await self._executor.execute(
            function=function,
            run=run,
            user_email=run.user_email,
            timeout_seconds=timeout_seconds,
        )

    # -- Agent-as-tool path (delegated workload ctx) --------------------------

    async def execute_function_as_workload(
        self,
        *,
        pod_id: UUID,
        name: str,
        input_data: dict,
        user_id: UUID,
        principal_type: str,
        principal_id: UUID,
        delegation_scope,
        delegation_actor_name: str | None,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        # Build the delegated ctx AND run resolve_execute inside one live UoW, so
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
                resolved = await self._build(uow).resolve_execute(
                    pod_id, name, input_data, user_id, None, ctx=auth_ctx
                )
        return await self._run_resolved(
            resolved, user_email=None, run_as_workload=run_as_workload
        )

    # -- Workflow path (user ctx) ---------------------------------------------

    async def execute_function_for_user(
        self,
        *,
        pod_id: UUID,
        name: str,
        input_data: dict,
        user_id: UUID,
    ) -> FunctionRunEntity:
        async with uow_scope(self._uow_factory) as uow:
            auth_ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id,
                pod_id=pod_id,
            )
            async with context_scope(auth_ctx):
                resolved = await self._build(uow).resolve_execute(
                    pod_id, name, input_data, user_id, None, ctx=auth_ctx
                )
        return await self._run_resolved(resolved, user_email=None)

    # -- Shared dispatch ------------------------------------------------------

    async def _run_resolved(
        self,
        resolved,
        *,
        user_email: str | None,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        function, run = resolved.function, resolved.run
        # JOB runs are dispatched to the worker via the FunctionRunExecutionRequested
        # event committed in resolve_execute; return the PENDING run immediately.
        if function.type == FunctionType.JOB:
            return run
        return await self._executor.execute(
            function=function,
            run=run,
            user_email=user_email,
            # Read live so the API function timeout stays patchable (e2e).
            timeout_seconds=_engine._API_FUNCTION_TIMEOUT_SECONDS,
            run_as_workload=run_as_workload,
        )
