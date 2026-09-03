"""Build an import plan by diffing a staged bundle against a pod's current state.

The plan is the *resume mechanism* for the whole feature: it is a pure diff
(CREATE / UPDATE / SKIP per resource) recomputed from the bundle + the pod's
current resources, and apply steps are idempotent upserts. So a lost plan (Redis
TTL) is never a problem — re-uploading and re-planning produces a plan that
picks up from reality.

:class:`PlanBuilder` is pure: it reads the staged bundle from disk and asks an
:class:`ExistingResources` port for the pod's current resource names (and, for a
table being updated, its columns). Production wires
:class:`ServiceExistingResources` (module services over a short UoW); unit tests
inject a fake, so the diff logic is tested without a database.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from lemma_pod_bundle import (
    diff_table_columns,
    load_resource_payload,
    require_account_variable_metadata,
)
from lemma_pod_bundle.diff import _order_table_dirs_by_dependency
from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_pod_bundle.layout import FILES_MANIFEST, POD_MANIFEST_FILE, TABLE_DATA_FILE
from lemma_pod_bundle.limits import (
    MAX_IMPORT_PLAN_STEPS,
    MAX_RECORDS_PER_TABLE,
    MAX_RECORDS_TOTAL,
)

from app.modules.pod_bundle.domain.exportable import is_exportable_agent
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.infrastructure.grants import has_grants
from app.modules.pod_bundle.domain.state import (
    ImportPlan,
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
    VariableSpec,
)

logger = get_logger(__name__)


class ExistingResources(Protocol):
    """The pod's current resources, by name — everything the diff needs."""

    async def table_names(self) -> set[str]: ...
    async def table_manifest(self, name: str) -> dict[str, Any] | None: ...
    async def function_names(self) -> set[str]: ...
    async def agent_names(self) -> set[str]: ...
    async def workflow_names(self) -> set[str]: ...
    async def schedule_names(self) -> set[str]: ...
    async def app_names(self) -> set[str]: ...
    async def surface_names(self) -> set[str]: ...


def _resource_subdirs(bundle_root: Path, resource_type: str) -> list[Path]:
    """Every resource directory of ``resource_type`` in the bundle, sorted by
    name — ``<root>/<type>/<name>/``. Missing type dir → empty."""
    type_dir = bundle_root / resource_type
    if not type_dir.is_dir():
        return []
    return sorted(
        (p for p in type_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )


def _file_steps(bundle_root: Path) -> list[PlanStep]:
    """FILE steps for a bundle's ``files/`` tree — folders shallow-first (so a
    parent is created before its children) then file bytes. ``.folder.json`` and
    the ``.files.json`` manifest are layout metadata, not files to recreate."""
    files_root = bundle_root / "files"
    if not files_root.is_dir():
        return []

    steps: list[PlanStep] = []
    folder_dirs = sorted(
        (p for p in files_root.rglob("*") if p.is_dir()),
        key=lambda p: (len(p.relative_to(files_root).parts), str(p)),
    )
    for folder in folder_dirs:
        rel = "/".join(folder.relative_to(files_root).parts)
        steps.append(
            PlanStep(
                index=0,
                kind=StepKind.FILE,
                name=rel,
                action=StepAction.CREATE,
                detail={"is_folder": True},
            )
        )

    file_paths = sorted(
        (p for p in files_root.rglob("*") if p.is_file()),
        key=lambda p: str(p.relative_to(files_root)),
    )
    for path in file_paths:
        parts = path.relative_to(files_root).parts
        if parts[-1] in (FILES_MANIFEST, ".folder.json"):
            continue
        steps.append(
            PlanStep(
                index=0,
                kind=StepKind.FILE,
                name="/".join(parts),
                action=StepAction.CREATE,
                detail={"is_folder": False},
            )
        )
    return steps


def _check_step_count(steps: list[PlanStep]) -> None:
    """Refuse a bundle that declares more apply steps than an import may carry.

    500 MiB uncompressed still allows tens of thousands of tiny files, and every
    one of them was a step the importer would carry out; the export caps bound
    only what *we* write."""
    if len(steps) > MAX_IMPORT_PLAN_STEPS:
        raise BundleInvalidError(
            f"This bundle declares {len(steps)} apply steps, over the "
            f"{MAX_IMPORT_PLAN_STEPS} an import may contain. Split it into "
            f"smaller bundles."
        )


def _check_seed_rows(data_path: Path, table: str, already_seeded: int) -> int:
    """Rows in one table's ``data.csv``, refusing the bundle over either cap.

    The shared record caps were export-side only: they bound what we write, and
    nothing bounded what an uploaded or GitHub-fetched bundle declares. Under
    the uncompressed-byte guard a single CSV could still carry 100k rows, which
    the applier reads whole into memory and hands to one `bulk_create_records`.

    Counting stops one row past the per-table cap, so a hostile file costs the
    cap rather than the file; a bundle that passes it has been counted exactly,
    which is what makes the running total across tables exact too.
    """
    with data_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)  # header
        rows = 0
        for _ in reader:
            rows += 1
            if rows > MAX_RECORDS_PER_TABLE:
                raise BundleInvalidError(
                    f"Table '{table}' seeds more than the {MAX_RECORDS_PER_TABLE} "
                    f"rows a bundle may carry for one table. Export it with less "
                    f"seed data, or load the rest after importing."
                )
    if already_seeded + rows > MAX_RECORDS_TOTAL:
        raise BundleInvalidError(
            f"This bundle seeds more than the {MAX_RECORDS_TOTAL} rows an import "
            f"may carry in total (reached at table '{table}'). Export it with "
            f"less seed data, or load the rest after importing."
        )
    return rows


#: What `app_builder._ensure_app` gives an app whose manifest omits visibility.
_IMPORTED_APP_DEFAULT_VISIBILITY = "PUBLIC"


def _resolved_app_visibility(payload: Mapping[str, object]) -> str:
    """The visibility the APP step will actually create the app with."""
    return str(payload.get("visibility") or _IMPORTED_APP_DEFAULT_VISIBILITY).upper()


def _resolved_surface_name(payload: Mapping[str, object], dir_name: str) -> str:
    """The pod-unique name `surface_apply` upserts by: the manifest's own
    ``name``, else the lowercased platform.

    The diff used to key on **platform**, while both the exporter (one directory
    per surface name) and the applier (upsert by name) key on name. Importing a
    Slack surface named `support-bot` into a pod holding a Slack `sales-bot`
    therefore planned UPDATE and then created a second surface."""
    name = str(payload.get("name") or "").strip()
    if name:
        return name
    return str(payload.get("platform") or dir_name).lower()


def _names_with_grants(bundle_root: Path, resource_type: str) -> list[str]:
    """Resources of this type whose manifest declares grants — the ones that
    need a deferred grants step after every referenced resource exists.

    Uses :func:`grants.has_grants`, the same predicate the applier runs. A local
    loose copy used to live here and treated an explicit ``{"grants": []}`` as a
    no-op, so a bundle saying "this agent holds nothing" planned no grants step
    and left a same-named agent's existing grants in place — the silent access
    change the exporter goes out of its way to avoid."""
    return [
        d.name
        for d in _resource_subdirs(bundle_root, resource_type)
        if has_grants(load_resource_payload(d, d.name, resource_type=resource_type))
    ]


class PlanBuilder:
    def __init__(self, existing: ExistingResources):
        self._existing = existing

    async def build_plan(self, *, bundle_root: Path) -> ImportPlan:
        pod_manifest = self._read_pod_manifest(bundle_root)
        format_version = int(pod_manifest.get("format_version") or 0)
        bundle_name = pod_manifest.get("name")

        steps: list[PlanStep] = []
        warnings: list[str] = []

        # --- tables (FK-ordered) + table_data --------------------------------
        table_dirs = _order_table_dirs_by_dependency(
            _resource_subdirs(bundle_root, "tables")
        )
        existing_tables = await self._existing.table_names()
        data_steps: list[tuple[str, dict[str, Any]]] = []
        seeded_rows = 0
        for table_dir in table_dirs:
            name = table_dir.name
            desired = load_resource_payload(table_dir, name, resource_type="tables")
            is_update = name in existing_tables
            detail: dict[str, Any] = {}
            destructive = False
            if is_update:
                current = await self._existing.table_manifest(name)
                if current is not None:
                    table_diff = diff_table_columns(current, desired)
                    detail = {
                        "columns_to_add": [c.get("name") for c in table_diff.to_add],
                        "columns_to_remove": list(table_diff.to_remove),
                        "columns_incompatible": list(table_diff.incompatible),
                    }
                    if table_diff.to_remove or table_diff.incompatible:
                        destructive = True
                        changed = table_diff.to_remove + table_diff.incompatible
                        warnings.append(
                            f"Table '{name}' would drop or alter columns: "
                            f"{', '.join(changed)}."
                        )
            steps.append(
                PlanStep(
                    index=0,
                    kind=StepKind.TABLE,
                    name=name,
                    action=StepAction.UPDATE if is_update else StepAction.CREATE,
                    destructive=destructive,
                    detail=detail,
                )
            )
            data_path = table_dir / TABLE_DATA_FILE
            if data_path.is_file():
                seeded_rows += _check_seed_rows(data_path, name, seeded_rows)
                data_steps.append((name, {}))

        # --- functions (+ deferred grants) -----------------------------------
        existing_functions = await self._existing.function_names()
        grant_functions = _names_with_grants(bundle_root, "functions")
        for d in _resource_subdirs(bundle_root, "functions"):
            steps.append(
                self._simple_step(StepKind.FUNCTION, d.name, existing_functions)
            )

        # --- agents (+ deferred grants) --------------------------------------
        existing_agents = await self._existing.agent_names()
        grant_agents = _names_with_grants(bundle_root, "agents")
        for d in _resource_subdirs(bundle_root, "agents"):
            steps.append(self._simple_step(StepKind.AGENT, d.name, existing_agents))

        # --- workflows -------------------------------------------------------
        existing_workflows = await self._existing.workflow_names()
        for d in _resource_subdirs(bundle_root, "workflows"):
            steps.append(
                self._create_once_step(
                    StepKind.WORKFLOW,
                    d.name,
                    existing_workflows,
                    reason=(
                        "a workflow of this name already exists; importing a "
                        "bundle does not replace it"
                    ),
                )
            )

        # --- schedules -------------------------------------------------------
        existing_schedules = await self._existing.schedule_names()
        for d in _resource_subdirs(bundle_root, "schedules"):
            steps.append(
                self._create_once_step(
                    StepKind.SCHEDULE,
                    d.name,
                    existing_schedules,
                    reason=(
                        "a schedule of this name already exists; importing a "
                        "bundle does not replace it"
                    ),
                )
            )

        # --- surfaces --------------------------------------------------------
        existing_surfaces = await self._existing.surface_names()
        for d in _resource_subdirs(bundle_root, "surfaces"):
            payload = load_resource_payload(d, d.name, resource_type="surfaces")
            steps.append(
                PlanStep(
                    index=0,
                    # The directory name: the applier loads the manifest by it.
                    name=d.name,
                    kind=StepKind.SURFACE,
                    action=(
                        StepAction.UPDATE
                        if _resolved_surface_name(payload, d.name) in existing_surfaces
                        else StepAction.CREATE
                    ),
                )
            )

        # --- apps ------------------------------------------------------------
        existing_apps = await self._existing.app_names()
        for d in _resource_subdirs(bundle_root, "apps"):
            steps.append(self._app_step(d, existing_apps))

        # --- files (folders parent-first, then file bytes) -------------------
        steps.extend(_file_steps(bundle_root))

        # --- grants (after every referenced resource exists) ------------------
        # Deferred for functions as well as agents: a function granted a folder,
        # an app, or another function that the same bundle creates would
        # otherwise be asked to resolve those names while its own FUNCTION step
        # runs — before files, apps, and the later functions exist.
        for name in grant_functions:
            steps.append(
                PlanStep(
                    index=0,
                    kind=StepKind.FUNCTION_GRANTS,
                    name=name,
                    action=StepAction.UPDATE,
                )
            )
        for name in grant_agents:
            steps.append(
                PlanStep(
                    index=0,
                    kind=StepKind.AGENT_GRANTS,
                    name=name,
                    action=StepAction.UPDATE,
                )
            )

        # --- table data (after tables exist) ---------------------------------
        for name, _ in data_steps:
            steps.append(
                PlanStep(
                    index=0,
                    kind=StepKind.TABLE_DATA,
                    name=name,
                    action=StepAction.CREATE,
                )
            )

        _check_step_count(steps)

        for i, step in enumerate(steps):
            step.index = i

        variables = _variables_from_manifest(pod_manifest)

        return ImportPlan(
            format_version=format_version,
            bundle_name=bundle_name,
            steps=steps,
            variables=variables,
            warnings=warnings,
        )

    def _simple_step(self, kind: StepKind, name: str, existing: set[str]) -> PlanStep:
        return PlanStep(
            index=0,
            kind=kind,
            name=name,
            action=StepAction.UPDATE if name in existing else StepAction.CREATE,
        )

    def _create_once_step(
        self, kind: StepKind, name: str, existing: set[str], *, reason: str
    ) -> PlanStep:
        """A step whose applier is create-once, planned SKIP rather than UPDATE.

        `_apply_workflow` and `_apply_schedule` return immediately when the pod
        already has the name. Planning those as UPDATE promised the user the
        resource would be replaced and then checkpointed the step DONE having
        changed nothing — so re-importing an updated bundle, the natural
        "install the new version" flow, reported success and applied none of it.

        Marked SKIPPED as well as SKIP, which is what keeps the apply loop from
        running it: `next_pending_step` only hands back PENDING/RUNNING steps,
        and progress already counts a SKIPPED step as accounted for.
        """
        if name not in existing:
            return PlanStep(index=0, kind=kind, name=name, action=StepAction.CREATE)
        return PlanStep(
            index=0,
            kind=kind,
            name=name,
            action=StepAction.SKIP,
            status=StepStatus.SKIPPED,
            error=reason,
        )

    def _app_step(self, bundle_dir: Path, existing: set[str]) -> PlanStep:
        """An APP step, carrying the visibility the app will be created with.

        An imported app takes the app default (PUBLIC) when its manifest is
        silent, which serves its HTML/JS on a guessable host to anyone. That is
        deliberate — an app is a shell whose data calls are authorized on their
        own — but the plan is where the importer is supposed to see what will
        happen, and it said nothing about this at all. An UPDATE leaves the
        existing app's visibility alone, so it claims nothing.
        """
        name = bundle_dir.name
        step = self._simple_step(StepKind.APP, name, existing)
        if step.action is StepAction.CREATE:
            payload = load_resource_payload(bundle_dir, name, resource_type="apps")
            step.detail = {"visibility": _resolved_app_visibility(payload)}
        return step

    @staticmethod
    def _read_pod_manifest(bundle_root: Path) -> dict[str, Any]:
        pod_path = bundle_root / POD_MANIFEST_FILE
        if not pod_path.is_file():
            return {}
        parsed = loads_jsonc(pod_path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}


class ServiceExistingResources:
    """Production :class:`ExistingResources` — lists the pod's resources through
    the module services over a caller-supplied short UoW + ``Context``. Lazy
    imports keep the module import cheap and cycle-free (mirrors the exporter)."""

    def __init__(self, *, uow: Any, ctx: Any, pod_id: UUID, user_id: UUID):
        self._uow = uow
        self._ctx = ctx
        self._pod_id = pod_id
        self._user_id = user_id

    async def table_names(self) -> set[str]:
        from app.composition.pod_bundle_resources import build_table_service

        service = build_table_service(self._uow)
        tables, _ = await service.list_tables(self._pod_id, self._ctx, limit=1000)
        return {str(t.name or "") for t in tables}

    async def table_manifest(self, name: str) -> dict[str, Any] | None:
        from app.composition.pod_bundle_resources import build_table_service
        from app.modules.datastore.contracts import TableResponse

        service = build_table_service(self._uow)
        table = await service.get_table(self._pod_id, name, self._ctx)
        if table is None:
            return None
        return TableResponse.model_validate(table).model_dump(mode="json")

    async def function_names(self) -> set[str]:
        from app.composition.pod_bundle_resources import build_function_service

        service = build_function_service(self._uow)
        functions, _ = await service.list_functions(
            self._pod_id, self._user_id, limit=1000, ctx=self._ctx
        )
        return {str(f.name or "") for f in functions}

    async def agent_names(self) -> set[str]:
        from app.composition.pod_bundle_resources import get_agent_service

        service = get_agent_service(self._uow)
        agents, _ = await service.list_agents(
            pod_id=self._pod_id,
            limit=1000,
            requester_user_id=self._user_id,
            ctx=self._ctx,
        )
        # Excluded to match the exporter -- a bundle can never contain one, so
        # this must never plan an update against the target pod's.
        return {str(a.name or "") for a in agents if is_exportable_agent(a)}

    async def workflow_names(self) -> set[str]:
        from app.composition.pod_bundle_resources import get_workflow_service

        service = get_workflow_service(self._uow)
        flows, _ = await service.list_workflows(
            self._pod_id, limit=1000, requester_user_id=self._user_id, ctx=self._ctx
        )
        return {str(f.name or "") for f in flows}

    async def schedule_names(self) -> set[str]:
        from app.composition.pod_bundle_resources import get_schedule_service

        service = get_schedule_service(self._uow)
        schedules, _ = await service.list_schedules(
            pod_id=self._pod_id, limit=1000, ctx=self._ctx
        )
        return {str(s.name or "") for s in schedules}

    async def app_names(self) -> set[str]:
        from app.composition.pod_bundle_resources import build_app_service

        service = build_app_service(self._uow)
        apps, _ = await service.list_apps(
            self._pod_id, self._user_id, 1000, None, ctx=self._ctx
        )
        return {str(a.name or "") for a in apps}

    async def surface_names(self) -> set[str]:
        """The pod's surface *names* — the key the applier upserts by, and the
        one the exporter writes a directory per. Keyed by platform, the diff
        called a second Slack surface an UPDATE of the first."""
        try:
            from app.composition.pod_bundle_resources import get_surface_service

            service = get_surface_service(self._uow)
            surfaces, _ = await service.list_surfaces_by_pod(self._pod_id, limit=100)
            return {str(getattr(s, "name", "") or "") for s in surfaces}
        except Exception:  # noqa: BLE001 - surfaces are best-effort in the plan
            # Degraded, not fatal: every bundled surface is then planned CREATE
            # while the applier still upserts by name, so the apply converges and
            # only the plan the person approved was wrong about it.
            logger.warning(
                "pod_bundle.plan_builder.surface_snapshot_unavailable.degraded",
                pod_id=self._pod_id,
                exc_info=True,
            )
            return set()


def _variables_from_manifest(pod_manifest: dict[str, Any]) -> list[VariableSpec]:
    """Turn ``pod.json -> variables`` into typed specs.

    A connector ``account`` variable is **required**: its source id belongs to the
    exporting org and cannot be reused, so the importer must supply one of their own
    accounts (validated at apply). A ``pod_member`` variable auto-resolves to the
    importing user, and a ``free`` variable (e.g. an app slug) is required only when
    it has no default.

    Every ``account`` variable must carry ``connector``/``provider`` metadata —
    a bundle missing it predates that guarantee (or was hand-edited) and must
    be re-exported rather than imported half-resolvable."""
    raw = pod_manifest.get("variables")
    if not isinstance(raw, dict):
        return []
    try:
        require_account_variable_metadata(raw)
    except ValueError as exc:
        raise BundleInvalidError(str(exc)) from exc
    specs: list[VariableSpec] = []
    for name, meta in raw.items():
        vtype = str((meta or {}).get("type") or "").lower()
        if vtype == "account":
            kind = "account"
        elif vtype in ("member", "pod_member"):
            kind = "pod_member"
        else:
            kind = "free"
        default = (meta or {}).get("default")
        connector = (meta or {}).get("connector")
        # `provider` is the pre-rename spelling, still read so bundles exported
        # before it keep planning.
        connector_kind = (meta or {}).get("connector_kind") or (meta or {}).get(
            "provider"
        )
        specs.append(
            VariableSpec(
                name=str(name),
                kind=kind,  # type: ignore[arg-type]
                description=(meta or {}).get("description"),
                required=(kind == "account") or (kind == "free" and default is None),
                default=str(default) if default is not None else None,
                connector=str(connector) if connector else None,
                connector_kind=str(connector_kind) if connector_kind else None,
            )
        )
    return specs
