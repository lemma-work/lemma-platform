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
from lemma_pod_bundle.layout import TABLE_DATA_FILE

from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.errors import PodBundleDomainError
from app.modules.pod_bundle.domain.state import PlanStep, StepKind

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
            StepKind.FUNCTION: self._apply_function,
            StepKind.AGENT: self._apply_agent,
            StepKind.SCHEDULE: self._apply_schedule,
            StepKind.WORKFLOW: self._apply_workflow,
        }.get(step.kind)
        if handler is None:
            # app / surface / agent_grants are deferred: the connector/runtime
            # dependencies they need are out of scope for this slice. Mark the
            # step skipped-with-reason instead of failing the import.
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
        from app.modules.datastore.api.dependencies import build_table_service
        from app.modules.datastore.domain.datastore_entities import ColumnSchema

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
        from app.modules.datastore.api.dependencies import (
            build_record_service,
            build_table_service,
        )
        from app.modules.datastore.services.table_context import TableContext

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

    # --- functions -------------------------------------------------------

    async def _apply_function(self, step: PlanStep) -> None:
        from app.modules.function.api.dependencies import build_function_service
        from app.modules.function.domain.entities import FunctionEntity

        service = build_function_service(self._uow)
        payload = self._load("functions", step.name)
        code = payload.get("code")
        code = code if isinstance(code, str) else None
        existing = await service.get_function_by_name(
            self._pod_id, step.name, self._user_id, raise_not_found=False, ctx=self._ctx
        )
        if existing is None:
            entity = FunctionEntity(
                pod_id=self._pod_id,
                user_id=self._user_id,
                name=step.name,
                description=payload.get("description"),
                icon_url=payload.get("icon_url"),
                config=payload.get("config"),
                visibility=payload.get("visibility") or "POD",
            )
            await service.create_function(
                entity, self._user_id, code=code, ctx=self._ctx
            )
        else:
            await service.update_function(
                pod_id=self._pod_id,
                name=step.name,
                code=code,
                description=payload.get("description"),
                requester_user_id=self._user_id,
                ctx=self._ctx,
            )

    # --- agents ----------------------------------------------------------

    async def _apply_agent(self, step: PlanStep) -> None:
        from app.modules.agent.api.dependencies import get_agent_service

        service = get_agent_service(self._uow)
        payload = self._load("agents", step.name)
        runtime = _agent_runtime(payload)
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
                input_schema=payload.get("input_schema"),
                output_schema=payload.get("output_schema"),
                metadata=payload.get("metadata"),
                requester_user_id=self._user_id,
                ctx=self._ctx,
            )

    # --- schedules -------------------------------------------------------

    async def _apply_schedule(self, step: PlanStep) -> None:
        from app.modules.schedule.api.dependencies import get_schedule_service
        from app.modules.schedule.domain.schedule import (
            ScheduleCreateEntity,
            ScheduleType,
        )

        service = get_schedule_service(self._uow)
        payload = self._load("schedules", step.name)
        existing = await _get_schedule(service, self._pod_id, step.name, self._ctx)
        if existing is not None:
            return  # schedules are treated as create-once by name for this slice
        entity = ScheduleCreateEntity(
            user_id=self._user_id,
            pod_id=self._pod_id,
            name=step.name,
            schedule_type=ScheduleType(str(payload.get("schedule_type"))),
            config=payload.get("config") or {},
            workflow_name=payload.get("workflow_name"),
            agent_name=payload.get("agent_name"),
            visibility=payload.get("visibility"),
        )
        await service.create_schedule(entity, self._ctx)

    # --- workflows (best-effort) -----------------------------------------

    async def _apply_workflow(self, step: PlanStep) -> None:
        from app.modules.workflow.api.dependencies import get_flow_service

        service = get_flow_service(self._uow)
        payload = self._load("workflows", step.name)
        if await _flow_exists(service, self._pod_id, step.name, self._ctx):
            return
        await service.create_flow(
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


# --- module helpers ----------------------------------------------------------


def _is_system_column(column: dict[str, Any]) -> bool:
    from lemma_pod_bundle.diff import _is_system_table_column

    return _is_system_table_column(column)


def _agent_runtime(payload: dict[str, Any]):
    from app.modules.agent.domain.value_objects import AgentRuntimeConfig

    raw = payload.get("agent_runtime")
    if isinstance(raw, dict) and raw.get("profile_id"):
        return AgentRuntimeConfig.model_validate(raw)
    return None


async def _get_table(service, pod_id, name, ctx):
    # get_table raises DatastoreTableNotFoundError when absent; treat as "create".
    try:
        return await service.get_table(pod_id, name, ctx)
    except Exception:
        return None


async def _get_agent(service, pod_id, name, ctx):
    try:
        return await service.get_agent_by_name(pod_id=pod_id, name=name, ctx=ctx)
    except Exception:
        return None


async def _get_schedule(service, pod_id, name, ctx):
    # No get-by-name on the schedule service; list with a name filter.
    try:
        schedules, *_ = await service.list_schedules(pod_id=pod_id, name=name, ctx=ctx)
        return schedules[0] if schedules else None
    except Exception:
        return None


async def _flow_exists(service, pod_id, name, ctx) -> bool:
    try:
        await service.get_flow_by_name(pod_id, name, ctx=ctx)
        return True
    except Exception:
        return False


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
