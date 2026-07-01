"""Bundle exporter — the inverse of the appliers.

Walks the pod's resources through the same backend services the appliers write
to, serializes them into the on-disk bundle format, runs the shared
``extract_requirements`` pass (so an exported bundle carries the same
requirements/capabilities an imported one does), and returns a zip archive.

v1 exports the resource types the import side wires: tables (schema + data),
agents, and functions (metadata). Function code and the remaining resource types
follow the same per-type pattern.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from lemma_pod_bundle import extract_portable_variables, extract_requirements

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

FORMAT_VERSION = 2
_RECORD_EXPORT_CAP = 10000


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


class BundleExporter:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def export(
        self, *, pod_id: UUID, user_id: UUID, ctx: Context, with_data: bool = True
    ) -> tuple[str, bytes]:
        """Return ``(pod_name, zip_bytes)`` for the pod's bundle."""
        from app.modules.pod.infrastructure.pod_repositories import PodRepository

        pod = await PodRepository(self.uow).get(pod_id)
        pod_name = (pod.name if pod else None) or str(pod_id)

        with TemporaryDirectory() as tmp:
            root = Path(tmp) / pod_name
            root.mkdir(parents=True, exist_ok=True)
            _write_json(
                root / "pod.json",
                {
                    "format_version": FORMAT_VERSION,
                    "name": pod_name,
                    "description": getattr(pod, "description", None),
                    "icon_url": getattr(pod, "icon_url", None),
                },
            )

            await self._export_tables(root, pod_id, user_id, ctx, with_data)
            await self._export_agents(root, pod_id, user_id, ctx)
            await self._export_functions(root, pod_id, user_id, ctx)
            await self._export_workflows(root, pod_id, user_id, ctx)
            await self._export_schedules(root, pod_id, ctx)
            await self._export_surfaces(root, pod_id, ctx)
            await self._export_apps(root, pod_id, user_id, ctx)

            # Template non-portable ids (account/member) into ${var} placeholders
            # and record them under pod.json -> variables, THEN derive the
            # requirements that read those variables. Order matters.
            extract_portable_variables(root)
            extract_requirements(root)
            return pod_name, _zip_dir(root)

    async def _export_tables(self, root, pod_id, user_id, ctx, with_data) -> None:
        from app.modules.datastore.api.dependencies import (
            build_record_service,
            build_table_service,
        )
        from app.modules.datastore.services.table_context import TableContext

        table_service = build_table_service(self.uow)
        tables, _ = await table_service.list_tables(pod_id, ctx, limit=1000)
        record_service = build_record_service(self.uow) if with_data else None

        for table in tables:
            name = table.table_name
            resource_dir = root / "tables" / name
            _write_json(
                resource_dir / f"{name}.json",
                {
                    "name": name,
                    "primary_key_column": table.primary_key_column,
                    "columns": [
                        column.model_dump(exclude_none=True)
                        for column in table.columns
                        if not column.system
                    ],
                    "config": table.config,
                    "enable_rls": table.enable_rls,
                    "visibility": table.visibility,
                },
            )
            if record_service is not None:
                schema_name = table_service.schema_manager.get_schema_name(pod_id)
                table_ctx = TableContext.from_table_entity(
                    table, schema_name, events_enabled=False
                )
                items, _ = await record_service.list_records(
                    table_ctx, user_id, limit=_RECORD_EXPORT_CAP, offset=0, admin_mode=True
                )
                # RecordEntity.data is the row payload (what the records API
                # returns); the import seeder strips system/auto columns.
                rows = [record.data for record in items]
                if rows:
                    _write_json(resource_dir / "data.json", rows)

    async def _export_agents(self, root, pod_id, user_id, ctx) -> None:
        from app.modules.agent.infrastructure.repositories import AgentRepository
        from app.modules.agent.services.agent_service import AgentService
        from app.modules.pod.services.authorization_factory import (
            create_authorization_service,
        )

        service = AgentService(
            agent_repository=AgentRepository(self.uow),
            authorization_service=create_authorization_service(self.uow),
        )
        agents, _ = await service.list_agents(
            pod_id=pod_id, requester_user_id=user_id, ctx=ctx, limit=1000
        )
        for agent in agents:
            payload = {
                "name": agent.name,
                "instruction": agent.instruction,
                "description": agent.description,
                "icon_url": agent.icon_url,
                "visibility": agent.visibility,
                "toolsets": [str(getattr(t, "value", t)) for t in (agent.toolsets or [])],
                "input_schema": agent.input_schema,
                "output_schema": agent.output_schema,
                # Model/profile selection + freeform metadata, so the agent runs
                # the same way on re-import instead of falling back to defaults.
                "agent_runtime": (
                    agent.agent_runtime.model_dump(mode="json") if agent.agent_runtime else None
                ),
                "metadata": agent.metadata,
            }
            grants = await self._grants_manifest("AGENT", agent.id, pod_id)
            if grants:
                payload["permissions"] = grants
            _write_json(root / "agents" / agent.name / f"{agent.name}.json", payload)

    async def _grants_manifest(self, grantee_type, grantee_id, pod_id) -> dict | None:
        from app.core.authorization.grants import list_grantee_resource_grants

        grants_map = await list_grantee_resource_grants(
            self.uow.session, pod_id=pod_id, grantee_type=grantee_type, grantee_id=grantee_id
        )
        grants = []
        for (resource_type, name), permission_ids in grants_map.items():
            rtype = getattr(resource_type, "value", str(resource_type))
            resource_name = name
            # connector_account grants resolve to a source-user account UUID,
            # which means nothing in another install. Re-key them to the
            # connector provider slug so import can re-point them to the
            # importing user's own account for that provider.
            if rtype == "connector_account":
                provider = await self._connector_provider_for_account(name)
                if provider is None:
                    continue  # account gone / unresolvable — drop the dangling grant
                resource_name = provider
            grants.append(
                {
                    "resource_type": rtype,
                    "resource_name": resource_name,
                    "permission_ids": list(permission_ids),
                }
            )
        return {"grants": grants} if grants else None

    async def _connector_provider_for_account(self, account_name: str) -> str | None:
        """Map a connector_account grant's account UUID to its provider slug."""
        from uuid import UUID as _UUID

        from app.core.crypto import get_secret_cipher
        from app.modules.connectors.infrastructure.repositories.account_repository import (
            AccountRepository,
        )

        try:
            account_id = _UUID(str(account_name))
        except (ValueError, TypeError):
            return None
        repo = AccountRepository(self.uow, encryption=get_secret_cipher())
        account = await repo.get(account_id)
        return account.connector_id if account else None

    async def _export_functions(self, root, pod_id, user_id, ctx) -> None:
        from app.modules.function.api.dependencies import build_function_service

        service = build_function_service(self.uow)
        functions, _ = await service.list_functions(pod_id, user_id, limit=1000, ctx=ctx)
        for summary in functions:
            # Re-fetch with code so the bundle is runnable on re-import.
            function = await service.get_function_by_name(
                pod_id, summary.name, user_id, include_code=True, ctx=ctx
            )
            payload = {
                "name": function.name,
                "description": function.description,
                "icon_url": function.icon_url,
                "config": function.config,
                "visibility": function.visibility,
                "code": function.code,
                # Schemas, type and pip deps, so the function is runnable on
                # re-import without re-deriving everything from the code.
                "type": getattr(function.type, "value", function.type),
                "input_schema": function.input_schema,
                "output_schema": function.output_schema,
                "config_schema": function.config_schema,
                "python_packages": list(function.python_packages or []),
            }
            grants = await self._grants_manifest("FUNCTION", function.id, pod_id)
            if grants:
                payload["permissions"] = grants
            _write_json(root / "functions" / function.name / f"{function.name}.json", payload)


    async def _export_workflows(self, root, pod_id, user_id, ctx) -> None:
        from app.modules.icon.services.icon_service import IconService
        from app.modules.workflow.services.flow_service import FlowService

        service = FlowService(self.uow, icon_service=IconService())
        flows, _ = await service.list_flows(pod_id, requester_user_id=user_id, ctx=ctx)
        for flow in flows:
            _write_json(
                root / "workflows" / flow.name / f"{flow.name}.json",
                {
                    "name": flow.name,
                    "description": flow.description,
                    "icon_url": flow.icon_url,
                    "visibility": flow.visibility,
                    "nodes": [node.model_dump(mode="json") for node in (flow.nodes or [])],
                    "edges": [edge.model_dump(mode="json") for edge in (flow.edges or [])],
                    "start": flow.start.model_dump(mode="json") if flow.start else None,
                },
            )

    async def _export_schedules(self, root, pod_id, ctx) -> None:
        from app.modules.schedule.services.schedule_service import ScheduleService

        service = ScheduleService(uow=self.uow)
        schedules, _ = await service.list_schedules(pod_id=pod_id, ctx=ctx)
        for schedule in schedules:
            name = schedule.name or str(schedule.id)
            _write_json(
                root / "schedules" / name / f"{name}.json",
                {
                    "name": schedule.name,
                    "schedule_type": str(getattr(schedule.schedule_type, "value", schedule.schedule_type)),
                    "agent_name": schedule.agent_name,
                    "workflow_name": schedule.workflow_name,
                    "config": schedule.config,
                    "filter_instruction": schedule.filter_instruction,
                    "filter_output_schema": schedule.filter_output_schema,
                    "visibility": schedule.visibility,
                },
            )


    async def _export_surfaces(self, root, pod_id, ctx) -> None:
        from app.modules.agent_surfaces.api.dependencies import get_surface_service

        service = get_surface_service(self.uow)
        surfaces, _ = await service.list_surfaces_by_pod(pod_id)
        for surface in surfaces:
            platform = str(getattr(surface.surface_type, "value", surface.surface_type))
            name = platform.lower()
            _write_json(
                root / "surfaces" / name / f"{name}.json",
                {
                    "platform": platform,
                    "credential_mode": str(
                        getattr(surface.credential_mode, "value", surface.credential_mode)
                    ) if surface.credential_mode else None,
                    "account_id": str(surface.account_id) if surface.account_id else None,
                    "config": surface.config.model_dump(mode="json") if surface.config else None,
                },
            )

    async def _export_apps(self, root, pod_id, user_id, ctx) -> None:
        from app.modules.apps.api.dependencies import build_app_service

        service = build_app_service(self.uow)
        apps, _ = await service.list_apps(pod_id, user_id, ctx=ctx)
        for app in apps:
            app_dir = root / "apps" / app.name
            _write_json(
                app_dir / f"{app.name}.json",
                {
                    "name": app.name,
                    "public_slug": app.public_slug,
                    "description": app.description,
                    "visibility": app.visibility,
                },
            )
            await self._export_app_archive(service, pod_id, user_id, app.name, ctx, app_dir)

    async def _export_app_archive(self, service, pod_id, user_id, name, ctx, app_dir) -> None:
        """Download the prebuilt dist (and source, if any) so the app re-imports
        runnable without a build step."""
        from app.modules.apps.domain.errors import AppNotFoundError

        for kind, resolver in (
            ("dist.zip", service.resolve_dist_archive),
            ("source.zip", service.resolve_source_archive),
        ):
            try:
                app_id, archive_path = await resolver(pod_id, name, user_id, ctx=ctx)
                data = await service.read_archive(app_id, archive_path)
            except AppNotFoundError:
                continue
            app_dir.mkdir(parents=True, exist_ok=True)
            (app_dir / kind).write_bytes(data)


def _zip_dir(root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(root.parent))
    return buffer.getvalue()
