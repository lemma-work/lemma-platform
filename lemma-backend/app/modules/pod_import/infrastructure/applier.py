"""Resource applier — realizes one import step against the target pod.

The integration boundary between the (pure, tested) import engine and the
backend's own resource services. The engine hands it a step; it reads that
resource's manifest from the staged bundle and dispatches to the matching
service (TableService, AgentService, …) by resource type.

Every resource type has a handler. e2e round-trip-verified: tables (schema +
seed data), agents (toolsets, schemas, grants), functions (code), workflows.
Wired but not exercisable in the e2e harness because their create path calls an
external service: schedules (scheduler API), surfaces (connector account), apps
(asset build) — app asset upload itself is still deferred (metadata only). An
unwired/failed step is recorded as a resumable failure, never a half-built pod.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID

from lemma_pod_bundle import read_manifest, read_table_data, resolve_placeholders

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_import.domain.value_objects import ImportStep

# Audit columns Lemma materializes itself — a bundle must not declare them.
_RESERVED_COLUMNS = frozenset({"created_at", "updated_at", "user_id"})

# Grants are applied in a final pass, AFTER every resource exists, because a
# grant can target a resource created later (an agent granted a workflow, or a
# peer agent) — applying them inline would fail to resolve the target. Each
# entry maps a grant-step type to (manifest dir, grantee_type). The plan appends
# one such step per grantee that carries grants.
_GRANT_STEP_KINDS: dict[str, tuple[str, str]] = {
    "agent_grants": ("agents", "AGENT"),
    "function_grants": ("functions", "FUNCTION"),
}


class ImportApplyContext:
    """Per-apply context handed to the applier (satisfies the engine's port)."""

    def __init__(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        bundle_path: Path,
        ctx: Context,
        variables: dict[str, str] | None = None,
    ):
        self.pod_id = pod_id
        self.user_id = user_id
        self.bundle_path = bundle_path
        self.ctx = ctx
        # ${var} -> value map: portable account/member ids resolved at apply time.
        self.variables = variables or {}


class ResourceApplyNotWired(NotImplementedError):
    """Raised for a resource type whose service binding isn't wired yet."""


ResourceHandler = Callable[[dict[str, Any], "ImportApplyContext"], Awaitable[None]]


class BackendResourceApplier:
    """Dispatches import steps to the backend's resource services."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self._handlers: dict[str, ResourceHandler] = {
            "tables": self._apply_table,
            "agents": self._apply_agent,
            "functions": self._apply_function,
            "workflows": self._apply_workflow,
            "schedules": self._apply_schedule,
            "surfaces": self._apply_surface,
            "apps": self._apply_app,
        }

    async def apply_step(self, step: ImportStep, ctx: ImportApplyContext) -> None:
        # A grant step replays a grantee's grants; its manifest is the
        # agent/function it grants for, read from that resource's dir.
        grant_spec = _GRANT_STEP_KINDS.get(step.resource_type)
        read_kind = grant_spec[0] if grant_spec else step.resource_type
        manifest = read_manifest(ctx.bundle_path, read_kind, step.resource_name)
        # The resource name is the directory name; a manifest may omit it, so
        # make it canonical before any handler reads manifest["name"].
        manifest.setdefault("name", step.resource_name)
        # Resolve ${var} placeholders (account/member ids) before the handler
        # constructs entities; unsupplied ones drop their field.
        manifest = resolve_placeholders(manifest, ctx.variables)
        try:
            if grant_spec is not None:
                await self._apply_grants_phase(grant_spec[1], step.resource_name, manifest, ctx)
                return
            handler = self._handlers.get(step.resource_type)
            if handler is None:
                raise ResourceApplyNotWired(
                    f"Applying '{step.resource_type}' is not wired to a backend service yet "
                    f"(resource '{step.resource_name}')."
                )
            await handler(manifest, ctx)
        except Exception as exc:
            # Idempotent apply: a resource that already exists (from a prior or
            # partial import) is treated as done, not a failure — so re-imports
            # and resumes don't break on what's already there.
            if _is_already_exists(exc):
                return
            raise

    # -- handlers -------------------------------------------------------------

    async def _apply_table(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.datastore.api.dependencies import build_table_service
        from app.modules.datastore.domain.datastore_entities import ColumnSchema

        columns = [
            ColumnSchema(**_column_kwargs(column))
            for column in manifest.get("columns") or []
            if str(column.get("name") or "") not in _RESERVED_COLUMNS
            and not column.get("system")
        ]
        table_name = str(manifest["name"])
        service = build_table_service(self.uow)
        await service.create_table(
            pod_id=ctx.pod_id,
            table_name=table_name,
            primary_key_column=str(manifest.get("primary_key_column") or "id"),
            columns=columns,
            config=manifest.get("config"),
            enable_rls=bool(manifest.get("enable_rls", False)),
            visibility=manifest.get("visibility"),
            ctx=ctx.ctx,
        )
        await self._seed_table(table_name, service, ctx)

    async def _seed_table(self, table_name, table_service, ctx: ImportApplyContext) -> None:
        """Seed bundled rows. The RecordService validator strips system/auto
        columns, so source ids/timestamps don't conflict."""
        from app.modules.datastore.api.dependencies import build_record_service
        from app.modules.datastore.services.table_context import TableContext

        rows = read_table_data(ctx.bundle_path, table_name)
        if not rows:
            return
        table = await table_service.get_table(ctx.pod_id, table_name, ctx.ctx)
        schema_name = table_service.schema_manager.get_schema_name(ctx.pod_id)
        table_ctx = TableContext.from_table_entity(table, schema_name, events_enabled=True)
        record_service = build_record_service(self.uow)
        await record_service.bulk_create_records(
            table_ctx, rows, ctx.user_id, upsert=True
        )

    async def _apply_agent(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.agent.infrastructure.repositories import AgentRepository
        from app.modules.agent.services.agent_service import AgentService
        from app.modules.pod.services.authorization_factory import (
            create_authorization_service,
        )

        instruction = manifest.get("instruction")
        if not isinstance(instruction, str):
            # A bundle may carry the instruction in a sidecar file ($file ref);
            # resolving those is a follow-up. Fail clearly rather than guess.
            raise ResourceApplyNotWired(
                f"Agent '{manifest.get('name')}' has a non-inline instruction "
                "(sidecar file); $file resolution isn't wired yet."
            )
        service = AgentService(
            agent_repository=AgentRepository(self.uow),
            authorization_service=create_authorization_service(self.uow),
        )
        await service.create_agent(
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            name=str(manifest["name"]),
            instruction=instruction,
            description=manifest.get("description"),
            icon_url=manifest.get("icon_url"),
            agent_runtime=_agent_runtime_config(manifest.get("agent_runtime")),
            toolsets=manifest.get("toolsets") or None,
            input_schema=manifest.get("input_schema"),
            output_schema=manifest.get("output_schema"),
            visibility=manifest.get("visibility"),
            metadata=manifest.get("metadata"),
            ctx=ctx.ctx,
        )
        # Grants are replayed in the deferred grant pass (see _GRANT_STEP_KINDS),
        # after every resource the grants might target has been created.


    async def _apply_function(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.function.api.dependencies import build_function_service
        from app.modules.function.domain.entities import FunctionEntity

        code = manifest.get("code")
        if code is not None and not isinstance(code, str):
            raise ResourceApplyNotWired(
                f"Function '{manifest.get('name')}' has an unresolved code reference."
            )
        from app.modules.function.domain.entities import FunctionType

        entity = FunctionEntity(
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            name=str(manifest["name"]),
            description=manifest.get("description"),
            icon_url=manifest.get("icon_url"),
            config=manifest.get("config"),
            visibility=str(manifest.get("visibility") or "POD"),
            input_schema=manifest.get("input_schema") or {},
            output_schema=manifest.get("output_schema") or {},
            config_schema=manifest.get("config_schema"),
            type=FunctionType(manifest["type"]) if manifest.get("type") else FunctionType.API,
            python_packages=list(manifest.get("python_packages") or []),
        )
        service = build_function_service(self.uow)
        await service.create_function(entity, ctx.user_id, code=code, ctx=ctx.ctx)
        # Grants are replayed in the deferred grant pass (see _GRANT_STEP_KINDS),
        # after every resource the grants might target has been created.


    # Org-scoped auth config is not a pod-level grant, so it never resolves in a
    # pod import — skip it. (connector / connector_account DO traverse: the
    # connector slug resolves against the global catalog, and a connector_account
    # is re-pointed to the importing user's own account below.)
    _UNRESOLVABLE_GRANT_TYPES = frozenset({"connector_auth_config"})

    async def _apply_grants_phase(
        self,
        grantee_type: str,
        grantee_name: str,
        manifest: dict[str, Any],
        ctx: ImportApplyContext,
    ) -> None:
        """Deferred grant pass: look up the (already-created) grantee by name and
        replay its grants now that every resource a grant could target exists."""
        grantee_id = await self._resolve_grantee_id(grantee_type, grantee_name, ctx)
        await self._apply_grants(grantee_type, grantee_id, manifest, ctx)

    async def _resolve_grantee_id(
        self, grantee_type: str, name: str, ctx: ImportApplyContext
    ) -> UUID:
        if grantee_type == "AGENT":
            from app.modules.agent.infrastructure.repositories import AgentRepository
            from app.modules.agent.services.agent_service import AgentService
            from app.modules.pod.services.authorization_factory import (
                create_authorization_service,
            )

            service = AgentService(
                agent_repository=AgentRepository(self.uow),
                authorization_service=create_authorization_service(self.uow),
            )
            agent = await service.get_agent_by_name(
                pod_id=ctx.pod_id, name=name, requester_user_id=ctx.user_id, ctx=ctx.ctx
            )
            return agent.id
        if grantee_type == "FUNCTION":
            from app.modules.function.api.dependencies import build_function_service

            service = build_function_service(self.uow)
            function = await service.get_function_by_name(
                ctx.pod_id, name, ctx.user_id, raise_not_found=True, ctx=ctx.ctx
            )
            return function.id
        raise ResourceApplyNotWired(f"Grant grantee type '{grantee_type}' is not wired.")

    async def _apply_grants(
        self, grantee_type: str, grantee_id: UUID, manifest: dict[str, Any], ctx: ImportApplyContext
    ) -> None:
        """Replay a resource's grants (table/folder/agent/function/connector
        access) onto the grantee. Grants are name-based, so they resolve in the
        target pod as long as the referenced resource is in the bundle or, for
        connectors, in the importing user's connected accounts."""
        from types import SimpleNamespace

        from app.core.authorization.context import ResourceType
        from app.core.authorization.grants import (
            normalize_pod_resource_grants,
            replace_grantee_resource_grants,
            validate_pod_resource_grant_permissions,
        )

        raw = (manifest.get("permissions") or {}).get("grants") or []
        grant_inputs = []
        for g in raw:
            rtype = str(g.get("resource_type") or "")
            rname = g.get("resource_name")
            if not rname or rtype in self._UNRESOLVABLE_GRANT_TYPES:
                continue
            if rtype == "connector_account":
                # Exported as a provider slug; re-point to the importing user's
                # own account for that provider. Skip if they haven't connected
                # it — the requirements/consent flow surfaces that separately.
                account_id = await self._resolve_user_connector_account(str(rname), ctx)
                if account_id is None:
                    continue
                rname = str(account_id)
            grant_inputs.append(
                SimpleNamespace(
                    resource_type=ResourceType(rtype),
                    resource_name=rname,
                    permission_ids=list(g.get("permission_ids") or []),
                )
            )
        if not grant_inputs:
            return
        validate_pod_resource_grant_permissions(grant_inputs)
        normalized = await normalize_pod_resource_grants(
            self.uow.session, pod_id=ctx.pod_id, grants=grant_inputs
        )
        await replace_grantee_resource_grants(
            self.uow.session,
            pod_id=ctx.pod_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            grants=normalized,
            created_by_user_id=ctx.user_id,
        )

    async def _resolve_user_connector_account(
        self, provider: str, ctx: ImportApplyContext
    ) -> UUID | None:
        """The importing user's connected account id for a connector provider
        slug (e.g. 'slack'), or None if they have no such connection."""
        org_id = getattr(ctx.ctx, "organization_id", None)
        if org_id is None:
            return None
        from app.core.crypto import get_secret_cipher
        from app.modules.connectors.infrastructure.repositories.account_repository import (
            AccountRepository,
        )

        repo = AccountRepository(self.uow, encryption=get_secret_cipher())
        account = await repo.get_by_user_org_and_app(ctx.user_id, org_id, provider)
        return account.id if account else None

    async def _apply_workflow(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.icon.services.icon_service import IconService
        from app.modules.workflow.domain.graph import WorkflowEdge
        from app.modules.workflow.domain.nodes import WORKFLOW_NODE_ADAPTER
        from app.modules.workflow.domain.start import FlowStart
        from app.modules.workflow.services.flow_service import FlowService

        nodes = [WORKFLOW_NODE_ADAPTER.validate_python(n) for n in manifest.get("nodes") or []]
        edges = [WorkflowEdge.model_validate(e) for e in manifest.get("edges") or []]
        start = FlowStart.model_validate(manifest["start"]) if manifest.get("start") else None
        service = FlowService(self.uow, icon_service=IconService())
        await service.create_flow(
            ctx.pod_id,
            str(manifest["name"]),
            manifest.get("description"),
            manifest.get("icon_url"),
            start,
            nodes=nodes,
            edges=edges,
            visibility=manifest.get("visibility"),
            requester_user_id=ctx.user_id,
            ctx=ctx.ctx,
        )

    async def _resolve_agent_id(self, agent_name: str | None, ctx: ImportApplyContext):
        if not agent_name:
            return None
        from app.modules.agent.infrastructure.repositories import AgentRepository
        from app.modules.agent.services.agent_service import AgentService
        from app.modules.pod.services.authorization_factory import (
            create_authorization_service,
        )

        service = AgentService(
            agent_repository=AgentRepository(self.uow),
            authorization_service=create_authorization_service(self.uow),
        )
        agent = await service.get_agent_by_name(
            pod_id=ctx.pod_id, name=agent_name, requester_user_id=ctx.user_id, ctx=ctx.ctx
        )
        return agent.id if agent else None

    async def _apply_surface(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.agent_surfaces.api.dependencies import get_surface_service
        from app.modules.agent_surfaces.domain.entities import (
            SurfaceConfig,
            SurfaceCredentialMode,
            SurfacePlatform,
        )

        agent_id = await self._resolve_agent_id(
            manifest.get("default_agent_name") or manifest.get("agent_name"), ctx
        )
        config = SurfaceConfig.model_validate(manifest["config"]) if manifest.get("config") else None
        credential_mode = (
            SurfaceCredentialMode(manifest["credential_mode"])
            if manifest.get("credential_mode")
            else None
        )
        account_id = manifest.get("account_id")
        await get_surface_service(self.uow).create_surface(
            pod_id=ctx.pod_id,
            agent_id=agent_id,
            platform=SurfacePlatform(str(manifest["platform"]).upper()),
            config=config,
            credential_mode=credential_mode,
            account_id=UUID(account_id) if isinstance(account_id, str) else account_id,
            ctx=ctx.ctx,
        )

    async def _apply_app(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.core.helpers.slug import normalize_public_slug
        from app.modules.apps.api.dependencies import build_app_service
        from app.modules.apps.domain.entities import AppEntity

        service = build_app_service(self.uow)
        # Prefer the bundle's clean slug for a readable app URL. public_slug is
        # globally unique, so fall back to a pod-scoped slug only when the clean
        # one is already taken (e.g. the same bundle imported into a second pod).
        base_slug = normalize_public_slug(str(manifest.get("public_slug") or manifest["name"]))
        taken = bool(base_slug) and (
            await service.repository.get_by_public_slug(base_slug) is not None
        )
        if taken:
            pod_suffix = str(ctx.pod_id).replace("-", "")[:8]
            public_slug = f"{base_slug}-{pod_suffix}"
        else:
            public_slug = base_slug
        entity = AppEntity(
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            name=str(manifest["name"]),
            public_slug=public_slug,
            description=manifest.get("description"),
            visibility=manifest.get("visibility") or "POD",
        )
        await service.create_app_with_context(entity, ctx.user_id, ctx=ctx.ctx)
        # Upload the prebuilt assets if the bundle carries them (no build needed —
        # a dist archive uploads straight to READY).
        app_dir = ctx.bundle_path / "apps" / entity.name
        source_bytes = _read_bytes(app_dir / "source.zip")
        dist_bytes = _read_bytes(app_dir / "dist.zip")
        if source_bytes or dist_bytes:
            await service.upload_bundle(
                ctx.pod_id,
                entity.name,
                ctx.user_id,
                source_archive_bytes=source_bytes,
                dist_archive_bytes=dist_bytes,
                ctx=ctx.ctx,
            )

    async def _apply_schedule(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.schedule.domain.schedule import ScheduleCreateEntity
        from app.modules.schedule.services.schedule_service import ScheduleService

        # agent/workflow targets are referenced by name (portable); the service
        # resolves them to ids in the target pod.
        create = ScheduleCreateEntity(
            user_id=ctx.user_id,
            pod_id=ctx.pod_id,
            name=manifest.get("name"),
            schedule_type=manifest["schedule_type"],
            agent_name=manifest.get("agent_name"),
            workflow_name=manifest.get("workflow_name"),
            config=manifest.get("config") or {},
            filter_instruction=manifest.get("filter_instruction"),
            filter_output_schema=manifest.get("filter_output_schema"),
            visibility=manifest.get("visibility"),
        )
        await ScheduleService(uow=self.uow).create_schedule(create, ctx=ctx.ctx)


def _is_already_exists(exc: BaseException) -> bool:
    """True if the error means the resource is already present — the services
    raise *AlreadyExistsError / *ConflictError, or say so in the message."""
    name = type(exc).__name__
    if "AlreadyExists" in name or "Conflict" in name:
        return True
    return "already exists" in str(exc).lower()


def _read_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _agent_runtime_config(data: Any):
    """Rebuild an AgentRuntimeConfig from its serialized manifest form, or None."""
    if not data:
        return None
    from app.modules.agent.domain.value_objects import AgentRuntimeConfig

    return AgentRuntimeConfig(**data)


def _column_kwargs(column: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields ColumnSchema accepts (the manifest may carry extras)."""
    from app.modules.datastore.domain.datastore_entities import ColumnSchema

    allowed = set(ColumnSchema.model_fields)
    return {key: value for key, value in column.items() if key in allowed}
