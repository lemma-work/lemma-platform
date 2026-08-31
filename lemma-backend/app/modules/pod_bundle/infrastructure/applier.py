"""Apply one plan step to the pod.

Every applier is an **idempotent upsert against the pod's current state, not the
plan**: it re-checks existence by name at apply time and creates or updates
accordingly. That is what makes a crash between a step's DB commit and its Redis
checkpoint safe to replay — re-running the step converges instead of duplicating.

Each ``apply_step`` runs inside a short UoW + ``Context`` opened by the job; the
applier never opens its own transaction.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any
from uuid import UUID

from lemma_pod_bundle import load_resource_payload
from lemma_pod_bundle.apply_fields import SCHEDULE_APPLY_FIELDS
from lemma_pod_bundle.layout import TABLE_DATA_FILE

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.errors import PodBundleDomainError
from app.modules.pod_bundle.infrastructure.account_binding import (
    validate_account_binding,
)
from app.modules.pod_bundle.infrastructure.surface_apply import apply_surface
from app.modules.pod_bundle.infrastructure.existing_resources import (
    _flow_exists,
    _get_agent,
    _get_function,
    _get_schedule,
    _get_table,
)
from app.modules.pod_bundle.domain.state import PlanStep, StepKind
from app.modules.pod_bundle.infrastructure.grants import (
    GrantInput as _GrantInput,
    apply_grants,
    grants_from_payload as _grants_from_payload,
)

logger = get_logger(__name__)


class StepNotApplicableError(PodBundleDomainError):
    """A step kind this slice does not yet apply (app/surface/grants). Marked
    SKIPPED with a reason rather than failing the whole import."""

    def __init__(self, message: str):
        super().__init__(message, code="POD_BUNDLE_STEP_UNSUPPORTED", status_code=422)


class BundleApplier:
    def __init__(
        self,
        *,
        uow,
        ctx,
        pod_id: UUID,
        user_id: UUID,
        bundle_root: Path,
        replacements: dict[str, str] | None = None,
    ):
        self._uow = uow
        self._ctx = ctx
        self._pod_id = pod_id
        self._user_id = user_id
        self._root = bundle_root
        self._replacements = replacements or {}

    async def apply_step(self, step: PlanStep) -> None:
        handler = {
            StepKind.TABLE: self._apply_table,
            StepKind.TABLE_DATA: self._apply_table_data,
            StepKind.AGENT: self._apply_agent,
            StepKind.AGENT_GRANTS: self._apply_agent_grants,
            StepKind.FUNCTION_GRANTS: self._apply_function_grants,
            StepKind.SCHEDULE: self._apply_schedule,
            StepKind.WORKFLOW: self._apply_workflow,
            StepKind.SURFACE: self._surface_step,
            StepKind.FILE: self._apply_file,
        }.get(step.kind)
        if handler is None:
            # APP and FUNCTION are applied by self-scoped runners because they
            # perform the sandbox runtime/storage I/O with no pooled connection held.
            raise StepNotApplicableError(
                f"{step.kind.value} import is not supported yet; skipped."
            )
        await handler(step)

    # --- helpers ---------------------------------------------------------

    def _load(self, resource_type: str, name: str) -> dict[str, Any]:
        """Load a resource manifest with ``$file`` refs resolved and ``${var}``
        placeholders substituted with the importer-provided values."""
        resource_dir = self._root / resource_type / name
        payload = load_resource_payload(resource_dir, name, resource_type=resource_type)
        return _substitute(payload, self._replacements)

    # --- tables ----------------------------------------------------------

    async def _apply_table(self, step: PlanStep) -> None:
        from app.composition.pod_bundle_resources import build_table_service
        from app.modules.datastore.contracts import ColumnSchema

        service = build_table_service(self._uow)
        payload = self._load("tables", step.name)
        columns = [
            ColumnSchema.model_validate(c)
            for c in payload.get("columns") or []
            if not _is_system_column(c)
        ]
        existing = await _get_table(service, self._pod_id, step.name, self._ctx)
        if existing is None:
            await service.create_table(
                self._pod_id,
                step.name,
                str(payload.get("primary_key_column") or "id"),
                columns,
                payload.get("config"),
                bool(payload.get("enable_rls", True)),
                visibility=payload.get("visibility"),
                ctx=self._ctx,
            )
            return
        # Update: add any new columns; drop removed columns only when the plan
        # marked this step destructive (i.e. the importer confirmed).
        existing_names = {c.name for c in existing.columns}
        desired_names = {c.name for c in columns}
        for column in columns:
            if column.name not in existing_names:
                await service.add_column(self._pod_id, step.name, column, self._ctx)
        if step.destructive:
            pk = existing.primary_key_column
            for name in existing_names - desired_names:
                if name == pk or _is_system_column({"name": name}):
                    continue
                await service.remove_column(self._pod_id, step.name, name, self._ctx)

    async def _apply_table_data(self, step: PlanStep) -> None:
        from app.composition.pod_bundle_resources import (
            build_record_service,
            build_table_service,
        )
        from app.modules.datastore.contracts import TableContext

        data_path = self._root / "tables" / step.name / TABLE_DATA_FILE
        if not data_path.is_file():
            return
        rows = _read_csv(data_path)
        if not rows:
            return
        table_service = build_table_service(self._uow)
        table = await _get_table(table_service, self._pod_id, step.name, self._ctx)
        if table is None:
            raise PodBundleDomainError(
                f"Table '{step.name}' must exist before seeding its data.",
                code="POD_BUNDLE_STEP_ORDER",
            )
        schema_name = table_service.schema_manager.get_schema_name(self._pod_id)
        record_service = build_record_service(self._uow)
        table_context = TableContext.from_table_entity(
            table, schema_name, events_enabled=False
        )
        # Upsert so re-running the seed step (crash/retry) converges by primary
        # key instead of raising on duplicates.
        await record_service.bulk_create_records(
            table_context, rows, self._user_id, upsert=True
        )

    # --- files -----------------------------------------------------------

    async def _apply_file(self, step: PlanStep) -> None:
        """Create a bundled folder or file. Idempotent: an existing path is left
        as-is (create-once by path), so a replayed step converges. Folders are
        planned parent-first, so the parent exists by the time a child runs."""
        from app.composition.pod_bundle_resources import build_file_service

        parts = [p for p in str(step.name or "").split("/") if p]
        if not parts:
            return
        pod_path = "/" + "/".join(parts)
        files_root = self._root / "files"
        service = build_file_service(self._uow)

        if await _file_exists(service, self._pod_id, pod_path, self._ctx):
            return

        if step.detail.get("is_folder"):
            meta = _read_json_file(files_root.joinpath(*parts) / ".folder.json")
            await service.create_folder(
                self._pod_id,
                pod_path,
                self._ctx,
                description=meta.get("description"),
                visibility=meta.get("visibility") or "POD",
            )
            return

        source = files_root.joinpath(*parts)
        if not source.is_file():
            return
        meta = _file_manifest_entry(files_root, pod_path)
        directory_path = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        file_content = await run_blocking(source.read_bytes, limiter="cpu_bound")
        await service.create_file(
            self._pod_id,
            parts[-1],
            file_content,
            self._ctx,
            description=meta.get("description"),
            directory_path=directory_path,
            search_enabled=bool(meta.get("search_enabled", True)),
            visibility=meta.get("visibility") or "POD",
        )

    # --- agents ----------------------------------------------------------

    async def _apply_agent(self, step: PlanStep) -> None:
        from app.composition.pod_bundle_resources import get_agent_service

        service = get_agent_service(self._uow)
        payload = self._load("agents", step.name)
        runtime = _agent_runtime(payload)
        # Toolsets are what let an imported agent actually *use* tools (POD,
        # WEB_SEARCH, …). Without them, a granted agent still can't act — so they
        # travel with the agent, not the deferred grants step.
        toolsets = _agent_toolsets(payload)
        existing = await _get_agent(service, self._pod_id, step.name, self._ctx)
        if existing is None:
            await service.create_agent(
                pod_id=self._pod_id,
                user_id=self._user_id,
                name=step.name,
                instruction=str(payload.get("instruction") or ""),
                description=payload.get("description"),
                icon_url=payload.get("icon_url"),
                agent_runtime=runtime,
                toolsets=toolsets,
                input_schema=payload.get("input_schema"),
                output_schema=payload.get("output_schema"),
                visibility=payload.get("visibility"),
                metadata=payload.get("metadata"),
                ctx=self._ctx,
            )
        else:
            await service.update_agent(
                pod_id=self._pod_id,
                name=step.name,
                instruction=payload.get("instruction"),
                description=payload.get("description"),
                icon_url=payload.get("icon_url"),
                agent_runtime=runtime,
                toolsets=toolsets,
                input_schema=payload.get("input_schema"),
                output_schema=payload.get("output_schema"),
                metadata=payload.get("metadata"),
                requester_user_id=self._user_id,
                ctx=self._ctx,
            )

    async def _sync_memory_grant(self, agent: Any, toolsets: Any) -> None:
        """Derive the `/memory` folder and grant the MEMORY toolset implies.

        Why an imported agent needs this, and why it runs after the grants step
        rather than before it, is on
        `app.composition.pod_bundle_resources.sync_agent_memory_grant`.
        """
        from app.composition.pod_bundle_resources import sync_agent_memory_grant

        if agent is None or getattr(agent, "id", None) is None:
            return
        await sync_agent_memory_grant(
            self._uow,
            pod_id=self._pod_id,
            agent_id=agent.id,
            toolsets=toolsets,
            ctx=self._ctx,
            created_by_user_id=self._user_id,
        )

    async def _surface_step(self, step: PlanStep) -> None:
        """The dispatch table's uniform shape over `surface_apply.apply_surface`."""
        await apply_surface(
            step,
            uow=self._uow,
            ctx=self._ctx,
            pod_id=self._pod_id,
            load=self._load,
        )

    async def _apply_function_grants(self, step: PlanStep) -> None:
        """Deferred grant step: replace a function's resource permission grants
        once every resource it references has been applied.

        Functions used to have their grants written inline by the FUNCTION step's
        own runner, which runs before agents, apps, and files. A function granted
        `/knowledge` or `function:write_lesson:execute` therefore tried to
        resolve a name that did not exist yet and lost the grant. Agents were
        always deferred; this makes the two behave the same."""
        from app.composition.pod_bundle_resources import build_function_service

        payload = self._load("functions", step.name)
        grants = _grants_from_payload(payload)
        if not grants:
            return
        service = build_function_service(self._uow)
        function = await _get_function(
            service, self._pod_id, step.name, self._user_id, self._ctx
        )
        if function is None or function.id is None:
            raise PodBundleDomainError(
                f"Function '{step.name}' must exist before applying its grants.",
                code="POD_BUNDLE_STEP_ORDER",
            )
        await self._apply_grants(
            grantee_type="FUNCTION", grantee_id=function.id, grants=grants
        )
        # A function's grants feed the env its workspace tools run with, so the
        # cached env must be dropped whenever they change — same as the
        # permissions-replace controller does.
        from app.composition.pod_bundle_apps import (
            invalidate_function_workspace_env_cache,
        )

        await invalidate_function_workspace_env_cache(
            pod_id=self._pod_id, function_id=function.id
        )

    async def _apply_agent_grants(self, step: PlanStep) -> None:
        """Deferred grant step: replace an agent's resource permission grants once
        every resource it references (tables, functions) has been applied."""
        from app.composition.pod_bundle_resources import get_agent_service

        payload = self._load("agents", step.name)
        grants = _grants_from_payload(payload)
        if not grants:
            return
        service = get_agent_service(self._uow)
        agent = await _get_agent(service, self._pod_id, step.name, self._ctx)
        if agent is None or agent.id is None:
            raise PodBundleDomainError(
                f"Agent '{step.name}' must exist before applying its grants.",
                code="POD_BUNDLE_STEP_ORDER",
            )
        await self._apply_grants(
            grantee_type="AGENT", grantee_id=agent.id, grants=grants
        )
        # Replace semantics: whatever the bundle listed is now the whole set, so
        # the toolset-derived grant has to be put back. From the agent as saved,
        # not from the bundle -- the same rule the permissions endpoint follows.
        await self._sync_memory_grant(agent, getattr(agent, "toolsets", None))

    # --- grants ----------------------------------------------------------

    async def _apply_grants(
        self, *, grantee_type: str, grantee_id: UUID, grants: list[_GrantInput]
    ) -> None:
        """Replace the grantee's resource grants on the step's own short UoW
        session (see infrastructure/grants.py)."""
        await apply_grants(
            self._uow.session,
            pod_id=self._pod_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            grants=grants,
            created_by_user_id=self._user_id,
        )

    # --- schedules -------------------------------------------------------

    async def _apply_schedule(self, step: PlanStep) -> None:
        from app.composition.pod_bundle_resources import get_schedule_service
        from app.modules.schedule.contracts import (
            ScheduleCreateEntity,
            ScheduleType,
        )

        service = get_schedule_service(self._uow)
        payload = self._load("schedules", step.name)
        existing = await _get_schedule(service, self._pod_id, step.name, self._ctx)
        if existing is not None:
            return  # schedules are treated as create-once by name for this slice
        # Build from the shared allow-list (also used by lemma-cli's direct
        # import) so the two importers can't silently drift on which exported
        # fields survive — this is what previously dropped account_id,
        # connector_trigger_id, filter_instruction and filter_output_schema.
        fields = {
            key: value for key, value in payload.items() if key in SCHEDULE_APPLY_FIELDS
        }
        fields["name"] = step.name
        fields["schedule_type"] = ScheduleType(str(payload.get("schedule_type")))
        fields["config"] = payload.get("config") or {}
        await validate_account_binding(
            self._uow,
            account_id=fields.get("account_id"),
            expected_connector=payload.get("connector_id"),
            expected_kind=payload.get("connector_kind") or payload.get("provider"),
            resource_label=f"Schedule '{step.name}'",
        )
        entity = ScheduleCreateEntity(
            user_id=self._user_id,
            pod_id=self._pod_id,
            **fields,
        )
        await service.create_schedule(entity, self._ctx)

    # --- workflows (best-effort) -----------------------------------------

    async def _apply_workflow(self, step: PlanStep) -> None:
        from app.composition.pod_bundle_resources import get_workflow_service

        service = get_workflow_service(self._uow)
        payload = self._load("workflows", step.name)
        if await _flow_exists(service, self._pod_id, step.name, self._ctx):
            return
        await service.create_workflow(
            pod_id=self._pod_id,
            name=step.name,
            description=payload.get("description"),
            icon_url=payload.get("icon_url"),
            start=payload.get("start"),
            mode=payload.get("mode") or "USER",
            visibility=payload.get("visibility"),
            nodes=payload.get("nodes"),
            edges=payload.get("edges"),
            requester_user_id=self._user_id,
            ctx=self._ctx,
        )

    # --- surfaces (connectors) -------------------------------------------


# --- module helpers ----------------------------------------------------------


def _is_system_column(column: dict[str, Any]) -> bool:
    from lemma_pod_bundle.diff import _is_system_table_column

    return _is_system_table_column(column)


def _agent_runtime(payload: dict[str, Any]):
    from app.core.domain.runtime import AgentRuntimeConfig

    raw = payload.get("agent_runtime")
    if isinstance(raw, dict) and raw.get("profile_id"):
        return AgentRuntimeConfig.model_validate(raw)
    return None


def _agent_toolsets(payload: dict[str, Any]) -> list[Any]:
    """Map the manifest's ``toolsets`` list to :class:`AgentToolset` values,
    dropping any the runtime doesn't recognize (forward-compat) and the reserved
    ``VIEW_IMAGE`` toolset, which is never persisted."""
    from app.modules.agent.contracts import AgentToolset

    toolsets: list[AgentToolset] = []
    for raw in payload.get("toolsets") or []:
        try:
            toolset = AgentToolset(str(raw))
        except ValueError:
            continue
        if toolset is AgentToolset.VIEW_IMAGE:
            continue
        toolsets.append(toolset)
    return toolsets


async def _file_exists(service, pod_id, path, ctx) -> bool:
    # get_file_by_path raises when the path is absent; treat that as "create".
    try:
        return await service.get_file_by_path(pod_id, path, ctx) is not None
    except Exception:
        return False


def _read_json_file(path: Path) -> dict[str, Any]:
    import json

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _file_manifest_entry(files_root: Path, pod_path: str) -> dict[str, Any]:
    """The ``.files.json`` entry for a file path (description/visibility/
    search_enabled), or an empty dict when there is no manifest/entry."""
    from lemma_pod_bundle.layout import FILES_MANIFEST

    manifest = _read_json_file(files_root / FILES_MANIFEST)
    for entry in manifest.get("files") or []:
        if isinstance(entry, dict) and str(entry.get("path") or "") == pod_path:
            return entry
    return {}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        row = {k: _decode_cell(v) for k, v in raw.items() if k}
        rows.append(row)
    return rows


def _decode_cell(value: Any) -> Any:
    if value is None or value == "":
        return None
    return value


def _substitute(node: Any, replacements: dict[str, str]) -> Any:
    """Replace ``${var}`` placeholders anywhere in a manifest with resolved
    values; unresolved placeholders are left for the service layer to drop."""
    if not replacements:
        return node
    if isinstance(node, str):
        out = node
        for name, value in replacements.items():
            out = out.replace("${" + name + "}", value)
        return out
    if isinstance(node, dict):
        return {k: _substitute(v, replacements) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, replacements) for v in node]
    return node
