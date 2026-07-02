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

from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from lemma_pod_bundle import diff_table_columns, load_resource_payload
from lemma_pod_bundle.diff import _order_table_dirs_by_dependency
from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_pod_bundle.layout import POD_MANIFEST_FILE, TABLE_DATA_FILE

from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.state import (
    ImportPlan,
    PlanStep,
    StepAction,
    StepKind,
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
    async def surface_platforms(self) -> set[str]: ...


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


def _has_grants(payload: dict[str, Any]) -> bool:
    grants = payload.get("permissions") or payload.get("grants")
    if isinstance(grants, dict):
        return bool(grants.get("grants") or grants)
    return bool(grants)


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
            if (table_dir / TABLE_DATA_FILE).is_file():
                data_steps.append((name, {}))

        # --- functions -------------------------------------------------------
        existing_functions = await self._existing.function_names()
        for d in _resource_subdirs(bundle_root, "functions"):
            steps.append(self._simple_step(StepKind.FUNCTION, d.name, existing_functions))

        # --- agents (+ deferred grants) --------------------------------------
        existing_agents = await self._existing.agent_names()
        grant_agents: list[str] = []
        for d in _resource_subdirs(bundle_root, "agents"):
            steps.append(self._simple_step(StepKind.AGENT, d.name, existing_agents))
            payload = load_resource_payload(d, d.name, resource_type="agents")
            if _has_grants(payload):
                grant_agents.append(d.name)

        # --- agent grants (after all resources exist) ------------------------
        for name in grant_agents:
            steps.append(
                PlanStep(
                    index=0,
                    kind=StepKind.AGENT_GRANTS,
                    name=name,
                    action=StepAction.UPDATE,
                )
            )

        # --- workflows -------------------------------------------------------
        existing_workflows = await self._existing.workflow_names()
        for d in _resource_subdirs(bundle_root, "workflows"):
            steps.append(self._simple_step(StepKind.WORKFLOW, d.name, existing_workflows))

        # --- schedules -------------------------------------------------------
        existing_schedules = await self._existing.schedule_names()
        for d in _resource_subdirs(bundle_root, "schedules"):
            steps.append(self._simple_step(StepKind.SCHEDULE, d.name, existing_schedules))

        # --- surfaces --------------------------------------------------------
        existing_surfaces = await self._existing.surface_platforms()
        for d in _resource_subdirs(bundle_root, "surfaces"):
            payload = load_resource_payload(d, d.name, resource_type="surfaces")
            platform = str(payload.get("platform") or d.name).upper()
            steps.append(
                PlanStep(
                    index=0,
                    kind=StepKind.SURFACE,
                    name=d.name,
                    action=(
                        StepAction.UPDATE
                        if platform in existing_surfaces
                        else StepAction.CREATE
                    ),
                )
            )

        # --- apps ------------------------------------------------------------
        existing_apps = await self._existing.app_names()
        for d in _resource_subdirs(bundle_root, "apps"):
            steps.append(self._simple_step(StepKind.APP, d.name, existing_apps))

        # --- table data (after tables exist) ---------------------------------
        for name, _ in data_steps:
            steps.append(
                PlanStep(index=0, kind=StepKind.TABLE_DATA, name=name, action=StepAction.CREATE)
            )

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

    def _simple_step(
        self, kind: StepKind, name: str, existing: set[str]
    ) -> PlanStep:
        return PlanStep(
            index=0,
            kind=kind,
            name=name,
            action=StepAction.UPDATE if name in existing else StepAction.CREATE,
        )

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
        from app.modules.datastore.api.dependencies import build_table_service

        service = build_table_service(self._uow)
        tables, _ = await service.list_tables(self._pod_id, self._ctx, limit=1000)
        return {str(t.name or "") for t in tables}

    async def table_manifest(self, name: str) -> dict[str, Any] | None:
        from app.modules.datastore.api.dependencies import build_table_service
        from app.modules.datastore.api.schemas.datastore_schemas import TableResponse

        service = build_table_service(self._uow)
        table = await service.get_table(self._pod_id, name, self._ctx)
        if table is None:
            return None
        return TableResponse.model_validate(table).model_dump(mode="json")

    async def function_names(self) -> set[str]:
        from app.modules.function.api.dependencies import build_function_service

        service = build_function_service(self._uow)
        functions, _ = await service.list_functions(
            self._pod_id, self._user_id, limit=1000, ctx=self._ctx
        )
        return {str(f.name or "") for f in functions}

    async def agent_names(self) -> set[str]:
        from app.modules.agent.api.dependencies import get_agent_service

        service = get_agent_service(self._uow)
        agents, _ = await service.list_agents(
            pod_id=self._pod_id, limit=1000, requester_user_id=self._user_id, ctx=self._ctx
        )
        return {str(a.name or "") for a in agents}

    async def workflow_names(self) -> set[str]:
        from app.modules.workflow.api.dependencies import get_flow_service

        service = get_flow_service(self._uow)
        flows, _ = await service.list_flows(
            self._pod_id, limit=1000, requester_user_id=self._user_id, ctx=self._ctx
        )
        return {str(f.name or "") for f in flows}

    async def schedule_names(self) -> set[str]:
        from app.modules.schedule.api.dependencies import get_schedule_service

        service = get_schedule_service(self._uow)
        schedules, _ = await service.list_schedules(
            pod_id=self._pod_id, limit=1000, ctx=self._ctx
        )
        return {str(s.name or "") for s in schedules}

    async def app_names(self) -> set[str]:
        from app.modules.apps.api.dependencies import build_app_service

        service = build_app_service(self._uow)
        apps, _ = await service.list_apps(
            self._pod_id, self._user_id, 1000, None, ctx=self._ctx
        )
        return {str(a.name or "") for a in apps}

    async def surface_platforms(self) -> set[str]:
        try:
            from app.modules.agent_surfaces.api.dependencies import get_surface_service

            service = get_surface_service(self._uow)
            surfaces, _ = await service.list_surfaces_by_pod(self._pod_id, limit=100)
            return {
                str(getattr(s, "surface_type", getattr(s, "platform", "")) or "").upper()
                for s in surfaces
            }
        except Exception as exc:  # noqa: BLE001 - surfaces are best-effort in the plan
            logger.warning("Skipping surface snapshot for pod %s: %s", self._pod_id, exc)
            return set()


def _variables_from_manifest(pod_manifest: dict[str, Any]) -> list[VariableSpec]:
    """Turn ``pod.json -> variables`` into typed specs. Account/member variables
    are auto-resolvable on apply (the importer's own account / the importing
    user), so they are not *required*; a free variable with no default is."""
    raw = pod_manifest.get("variables")
    if not isinstance(raw, dict):
        return []
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
        specs.append(
            VariableSpec(
                name=str(name),
                kind=kind,  # type: ignore[arg-type]
                description=(meta or {}).get("description"),
                required=(kind == "free" and default is None),
                default=str(default) if default is not None else None,
            )
        )
    return specs
