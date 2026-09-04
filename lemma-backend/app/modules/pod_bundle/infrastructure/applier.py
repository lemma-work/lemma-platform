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
from app.modules.pod_bundle.domain.state import PlanStep, StepKind
from app.modules.pod_bundle.infrastructure.grants import (
    GrantInput as _GrantInput,
    apply_grants,
    grants_from_payload as _grants_from_payload,
    has_grants as _has_grants,
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
        warnings: list[str] | None = None,
    ):
        self._uow = uow
        self._ctx = ctx
        self._pod_id = pod_id
        self._user_id = user_id
        self._root = bundle_root
        self._replacements = replacements or {}
        # The import's own warning list, so a best-effort fallback the apply
        # takes (an unreadable file manifest, say) reaches the person who
        # imported rather than only a debug log.
        self._warnings = warnings if warnings is not None else []

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
        from app.modules.datastore.contracts import ColumnSchema
        from app.modules.datastore.contracts.provisioning import (
            add_table_column,
            create_table,
            get_table,
            remove_table_column,
        )

        payload = self._load("tables", step.name)
        columns = [
            ColumnSchema.model_validate(c)
            for c in payload.get("columns") or []
            if not _is_system_column(c)
        ]
        existing = await get_table(
            self._uow, pod_id=self._pod_id, name=step.name, ctx=self._ctx
        )
        if existing is None:
            await create_table(
                self._uow,
                pod_id=self._pod_id,
                name=step.name,
                primary_key_column=str(payload.get("primary_key_column") or "id"),
                columns=columns,
                config=payload.get("config"),
                enable_rls=bool(payload.get("enable_rls", True)),
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
                await add_table_column(
                    self._uow,
                    pod_id=self._pod_id,
                    table_name=step.name,
                    column=column,
                    ctx=self._ctx,
                )
        if step.destructive:
            pk = existing.primary_key_column
            for name in existing_names - desired_names:
                if name == pk or _is_system_column({"name": name}):
                    continue
                await remove_table_column(
                    self._uow,
                    pod_id=self._pod_id,
                    table_name=step.name,
                    column_name=name,
                    ctx=self._ctx,
                )

    async def _apply_table_data(self, step: PlanStep) -> None:
        from app.modules.datastore.contracts.provisioning import (
            get_table,
            seed_table_rows,
        )

        data_path = self._root / "tables" / step.name / TABLE_DATA_FILE
        if not data_path.is_file():
            return
        rows = _read_csv(data_path)
        if not rows:
            return
        table = await get_table(
            self._uow, pod_id=self._pod_id, name=step.name, ctx=self._ctx
        )
        if table is None:
            raise PodBundleDomainError(
                f"Table '{step.name}' must exist before seeding its data.",
                code="POD_BUNDLE_STEP_ORDER",
            )
        await seed_table_rows(
            self._uow,
            pod_id=self._pod_id,
            table=table,
            rows=rows,
            user_id=self._user_id,
        )

    # --- files -----------------------------------------------------------

    async def _apply_file(self, step: PlanStep) -> None:
        """Create a bundled folder or file. Idempotent: an existing path is left
        as-is (create-once by path), so a replayed step converges. Folders are
        planned parent-first, so the parent exists by the time a child runs."""
        from app.modules.datastore.contracts.provisioning import (
            create_file,
            create_folder,
            file_exists,
        )

        parts = [p for p in str(step.name or "").split("/") if p]
        if not parts:
            return
        pod_path = "/" + "/".join(parts)
        files_root = self._root / "files"

        if await file_exists(
            self._uow, pod_id=self._pod_id, path=pod_path, ctx=self._ctx
        ):
            return

        if step.detail.get("is_folder"):
            meta = _read_json_file(
                files_root.joinpath(*parts) / ".folder.json",
                label=f".folder.json for '{pod_path}'",
                warnings=self._warnings,
            )
            await create_folder(
                self._uow,
                pod_id=self._pod_id,
                path=pod_path,
                ctx=self._ctx,
                description=meta.get("description"),
                visibility=meta.get("visibility") or "POD",
            )
            return

        source = files_root.joinpath(*parts)
        if not source.is_file():
            return
        meta = _file_manifest_entry(files_root, pod_path, self._warnings)
        directory_path = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        file_content = await run_blocking(source.read_bytes, limiter="cpu_bound")
        await create_file(
            self._uow,
            pod_id=self._pod_id,
            name=parts[-1],
            content=file_content,
            ctx=self._ctx,
            description=meta.get("description"),
            directory_path=directory_path,
            search_enabled=bool(meta.get("search_enabled", True)),
            visibility=meta.get("visibility") or "POD",
        )

    # --- agents ----------------------------------------------------------

    async def _apply_agent(self, step: PlanStep) -> None:
        from app.modules.agent.contracts.provisioning import (
            create_agent,
            get_agent,
            update_agent,
        )

        payload = self._load("agents", step.name)
        runtime = _agent_runtime(payload)
        # Toolsets are what let an imported agent actually *use* tools (POD,
        # WEB_SEARCH, …). Without them, a granted agent still can't act — so they
        # travel with the agent, not the deferred grants step.
        toolsets = _agent_toolsets(payload)
        common = {
            "description": payload.get("description"),
            "icon_url": payload.get("icon_url"),
            "agent_runtime": runtime,
            "toolsets": toolsets,
            "input_schema": payload.get("input_schema"),
            "output_schema": payload.get("output_schema"),
            "metadata": payload.get("metadata"),
        }
        existing = await get_agent(
            self._uow, pod_id=self._pod_id, name=step.name, ctx=self._ctx
        )
        if existing is None:
            await create_agent(
                self._uow,
                pod_id=self._pod_id,
                user_id=self._user_id,
                name=step.name,
                instruction=str(payload.get("instruction") or ""),
                visibility=payload.get("visibility"),
                ctx=self._ctx,
                **common,
            )
        else:
            await update_agent(
                self._uow,
                pod_id=self._pod_id,
                name=step.name,
                instruction=payload.get("instruction"),
                user_id=self._user_id,
                ctx=self._ctx,
                **common,
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
        always deferred; this makes the two behave the same.

        Gated on ``has_grants``, not on the parsed list: a manifest that says
        ``{"grants": []}`` means "holds nothing" and clears the target's grants,
        while one with no ``permissions`` key leaves them alone."""
        from app.modules.function.contracts.provisioning import get_function

        payload = self._load("functions", step.name)
        if not _has_grants(payload):
            return
        grants = _grants_from_payload(payload)
        function = await get_function(
            self._uow,
            pod_id=self._pod_id,
            name=step.name,
            user_id=self._user_id,
            ctx=self._ctx,
            include_code=False,
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
        every resource it references (tables, functions) has been applied. An
        explicitly empty grant list clears them — see `_apply_function_grants`."""
        from app.modules.agent.contracts.provisioning import (
            get_agent,
            sync_agent_memory_grant,
        )

        payload = self._load("agents", step.name)
        if not _has_grants(payload):
            return
        grants = _grants_from_payload(payload)
        agent = await get_agent(
            self._uow, pod_id=self._pod_id, name=step.name, ctx=self._ctx
        )
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
        await sync_agent_memory_grant(
            self._uow,
            pod_id=self._pod_id,
            agent_id=agent.id,
            toolsets=getattr(agent, "toolsets", None),
            ctx=self._ctx,
            created_by_user_id=self._user_id,
        )

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
        from app.modules.schedule.contracts import ScheduleCreateEntity, ScheduleType
        from app.modules.schedule.contracts.provisioning import (
            create_schedule,
            get_schedule_by_name,
        )

        payload = self._load("schedules", step.name)
        existing = await get_schedule_by_name(
            self._uow, pod_id=self._pod_id, name=step.name, ctx=self._ctx
        )
        if existing is not None:
            # Create-once by name. The plan says SKIP for this case, so reaching
            # here means the pod grew the schedule between plan and apply.
            return
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
        await create_schedule(self._uow, entity, ctx=self._ctx)

    # --- workflows (best-effort) -----------------------------------------

    async def _apply_workflow(self, step: PlanStep) -> None:
        from app.modules.workflow.contracts.provisioning import (
            create_workflow,
            workflow_exists,
        )

        payload = self._load("workflows", step.name)
        if await workflow_exists(
            self._uow, pod_id=self._pod_id, name=step.name, ctx=self._ctx
        ):
            # Create-once by name, like schedules; the plan says SKIP for it.
            return
        await create_workflow(
            self._uow,
            pod_id=self._pod_id,
            name=step.name,
            description=payload.get("description"),
            icon_url=payload.get("icon_url"),
            start=payload.get("start"),
            mode=payload.get("mode") or "USER",
            visibility=payload.get("visibility"),
            nodes=payload.get("nodes"),
            edges=payload.get("edges"),
            user_id=self._user_id,
            ctx=self._ctx,
        )


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


def _read_json_file(path: Path, *, label: str, warnings: list[str]) -> dict[str, Any]:
    """Read a bundle's file-layout metadata (``.folder.json``, ``.files.json``).

    An absent file is normal — a bundle need not carry either — and reads as "no
    metadata". A present-but-unreadable one is not: falling back to defaults
    lands every file as ``visibility="POD"`` with no description and search on,
    which is a silent default on a visibility field. So it is reported on the
    import instead of returning ``{}`` mutely."""
    import json

    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        _note_unreadable_metadata(label, warnings, exc_info=True)
        return {}
    if not isinstance(parsed, dict):
        _note_unreadable_metadata(label, warnings, exc_info=False)
        return {}
    return parsed


def _note_unreadable_metadata(
    label: str, warnings: list[str], *, exc_info: bool
) -> None:
    """Report unreadable layout metadata once per label, not once per file:
    ``.files.json`` is re-read for every FILE step, so a broken manifest would
    otherwise emit one log line and one user-visible warning per bundled file."""
    message = (
        f"'{label}' could not be read; the files it describes were imported with "
        f"default description, visibility and search settings."
    )
    if message in warnings:
        return
    warnings.append(message)
    logger.warning(
        "pod_bundle.applier.file_metadata_unreadable.degraded",
        metadata_file=label,
        exc_info=exc_info,
    )


def _file_manifest_entry(
    files_root: Path, pod_path: str, warnings: list[str]
) -> dict[str, Any]:
    """The ``.files.json`` entry for a file path (description/visibility/
    search_enabled), or an empty dict when there is no manifest/entry."""
    from lemma_pod_bundle.layout import FILES_MANIFEST

    manifest = _read_json_file(
        files_root / FILES_MANIFEST, label=FILES_MANIFEST, warnings=warnings
    )
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
