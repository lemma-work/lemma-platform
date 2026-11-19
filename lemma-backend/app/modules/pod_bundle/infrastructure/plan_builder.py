"""Build an import plan by diffing a staged bundle against a pod's current state.

The plan is the *resume mechanism* for the whole feature: it is a pure diff
recomputed from the bundle + the pod's current resources, and every apply step
creates only what is missing, so replaying one converges. A lost plan (Redis
TTL) is never a problem — re-uploading and re-planning produces a plan that
picks up from reality.

What the pod already has is kept (``kept_parts``): planned SKIP with the reason
on its step, except a table, which is planned UPDATE when it lacks some of the
bundle's columns and gains only those. Nothing is planned to be removed or
replaced, so no plan has a destructive step.

:class:`PlanBuilder` is pure: it reads the staged bundle from disk and asks an
:class:`ExistingResources` port for the pod's current resource names (and, for a
table it already has, its columns). Production wires
:class:`ServiceExistingResources` (module services over a short UoW); unit tests
inject a fake, so the diff logic is tested without a database.
"""

from __future__ import annotations

import csv
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from lemma_pod_bundle import load_resource_payload
from lemma_pod_bundle.diff import _order_table_dirs_by_dependency
from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_pod_bundle.layout import (
    FILES_MANIFEST,
    POD_MANIFEST_FILE,
    TABLE_DATA_FILE,
    check_format_version,
)
from lemma_pod_bundle.limits import (
    MAX_IMPORT_PLAN_STEPS,
    MAX_RECORDS_PER_TABLE,
    MAX_RECORDS_TOTAL,
)

from app.modules.pod_bundle.domain.exportable import is_exportable_agent
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.infrastructure.connector_grants import (
    ConnectorDirectory,
    plan_connector_grants,
)
from app.modules.pod_bundle.infrastructure.connector_planning import (
    ConnectorLookup,
    NoConnectorLookup,
    plan_connectors,
)
from app.modules.pod_bundle.infrastructure import (
    account_checks,
    bundle_outcome,
    kept_parts,
    machine_bundle,
)
from app.modules.pod_bundle.infrastructure.grants import has_grants
from app.modules.pod_bundle.infrastructure.kept_parts import ExistingResources
from app.modules.pod_bundle.infrastructure.one_schedule import refuse_shared_workflows
from app.modules.pod_bundle.infrastructure.plan_variables import variables_from_manifest
from app.modules.pod_bundle.infrastructure.grant_planning import (
    GrantConferral,
    HeldGrants,
    NoGrantConferral,
    NoHeldGrants,
    plan_grant_conferral,
    plan_kept_access,
)
from app.modules.pod_bundle.domain.state import (
    ImportPlan,
    PlanStep,
    StepAction,
    StepKind,
)

logger = get_logger(__name__)


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
_IMPORTED_APP_DEFAULT_VISIBILITY = "POD"


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


#: The step kind of each bundle directory planned by ``_create_or_keep``.
_KINDS = {
    "functions": StepKind.FUNCTION,
    "agents": StepKind.AGENT,
    "workflows": StepKind.WORKFLOW,
}

#: The bundle directory holding the manifest each grants step writes from.
_GRANTS_FROM = {
    StepKind.FUNCTION_GRANTS: "functions",
    StepKind.AGENT_GRANTS: "agents",
}


def _create_or_keep(
    bundle_root: Path, resource_type: str, existing: set[str]
) -> list[PlanStep]:
    """One step per bundled resource of this type: created, or kept."""
    kind = _KINDS[resource_type]
    return [
        kept_parts.create_or_keep(kind, d.name, existing)
        for d in _resource_subdirs(bundle_root, resource_type)
    ]


def _created(steps: Sequence[PlanStep]) -> set[str]:
    """The names these steps create."""
    return {step.name for step in steps if step.action is StepAction.CREATE}


def _names_with_grants(bundle_root: Path, resource_type: str) -> list[str]:
    """Resources of this type whose manifest declares grants — the ones that
    need a deferred grants step after every referenced resource exists, when
    the import creates them.

    Uses :func:`grants.has_grants`, the same predicate the applier runs, so an
    explicit ``{"grants": []}`` ("this workload holds nothing") is a write and
    plans a step, while a manifest with no ``permissions`` key plans none."""
    return [
        d.name
        for d in _resource_subdirs(bundle_root, resource_type)
        if has_grants(load_resource_payload(d, d.name, resource_type=resource_type))
    ]


class PlanBuilder:
    def __init__(
        self,
        existing: ExistingResources,
        directory: ConnectorDirectory | None = None,
        *,
        connectors: ConnectorLookup | None = None,
        grants: GrantConferral | None = None,
        accounts: account_checks.AccountUsability | None = None,
        outcome: bundle_outcome.OwnedOutcome | None = None,
        created: Collection[str] = (),
        held: HeldGrants | None = None,
    ):
        #: Less what this import made before, which a replan finishes.
        self._existing = kept_parts.TheirParts(existing, created)
        #: What the target pod owns now; ``None`` when this import sets none.
        self._outcome = outcome
        #: Whether the importer could use an account a schedule names.
        self._accounts = accounts or account_checks.AnyAccountUsable()
        #: Grants on connectors the pod does not declare (``connector_grants``).
        self._directory = directory
        #: The bundle's own ``connectors/`` (``connector_planning``).
        self._connectors = connectors or NoConnectorLookup()
        self._grants = grants or NoGrantConferral()
        #: What a kept function or agent already holds (``plan_kept_access``).
        self._held = held or NoHeldGrants()

    async def build_plan(self, *, bundle_root: Path) -> ImportPlan:
        pod_manifest = self._read_pod_manifest(bundle_root)
        try:
            format_version = check_format_version(pod_manifest)
        except ValueError as exc:
            raise BundleInvalidError(str(exc)) from exc
        bundle_name = pod_manifest.get("name")

        # --- connectors first: everything else may name one by slug --------
        connectors, steps, warnings = await plan_connectors(
            bundle_root, self._connectors
        )

        # --- tables (FK-ordered); their rows wait until everything exists ----
        table_steps, data_steps = await self._table_steps(bundle_root, warnings)
        steps += table_steps

        # --- machines: before the agents that name one, and their grants ----
        steps.extend(await machine_bundle.machine_steps(bundle_root, self._existing))

        # --- functions, agents, workflows, schedules -------------------------
        functions = _create_or_keep(
            bundle_root, "functions", await self._existing.function_names()
        )
        agents = _create_or_keep(
            bundle_root, "agents", await self._existing.agent_names()
        )
        steps += functions + agents
        existing_workflows = await self._existing.workflow_names()
        steps += _create_or_keep(bundle_root, "workflows", existing_workflows)
        existing_schedules = await self._existing.schedule_names()
        for d in _resource_subdirs(bundle_root, "schedules"):
            step = kept_parts.create_or_keep(
                StepKind.SCHEDULE, d.name, existing_schedules
            )
            payload = load_resource_payload(d, d.name, resource_type="schedules")
            steps.append(account_checks.noting_deprecated_account(step, payload))

        # --- surfaces ------------------------------------------------------
        existing_surfaces = await self._existing.surface_names()
        for d in _resource_subdirs(bundle_root, "surfaces"):
            payload = load_resource_payload(d, d.name, resource_type="surfaces")
            # Matched by the name the applier creates it under, and named by
            # its directory, which the applier loads the manifest by.
            here = _resolved_surface_name(payload, d.name) in existing_surfaces
            here = here and not self._existing.ours(StepKind.SURFACE, d.name)
            steps.append(
                kept_parts.create_or_keep(
                    StepKind.SURFACE, d.name, {d.name} if here else set()
                )
            )

        # --- apps ------------------------------------------------------------
        existing_apps = await self._existing.app_names()
        for d in _resource_subdirs(bundle_root, "apps"):
            steps.append(self._app_step(d, existing_apps))

        # --- files (folders parent-first, then file bytes) -------------------
        file_steps = _file_steps(bundle_root)
        _check_step_count(file_steps)  # before one lookup per path, not after
        present = (
            await self._existing.existing_paths(["/" + s.name for s in file_steps])
            if file_steps
            else set()
        )
        steps += kept_parts.keep_existing_files(file_steps, present)

        # --- grants (after every referenced resource exists) ------------------
        # Deferred for functions as well as agents: a function granted a folder,
        # an app, or another function that the same bundle creates would
        # otherwise be asked to resolve those names while its own FUNCTION step
        # runs — before files, apps, and the later functions exist.
        grant_steps = kept_parts.grants_steps(
            StepKind.FUNCTION_GRANTS,
            _names_with_grants(bundle_root, "functions"),
            _created(functions),
        ) + kept_parts.grants_steps(
            StepKind.AGENT_GRANTS,
            _names_with_grants(bundle_root, "agents"),
            _created(agents),
        )
        steps += grant_steps
        warnings += await plan_grant_conferral(bundle_root, steps, self._grants)
        warnings += await plan_kept_access(bundle_root, functions + agents, self._held)

        if self._directory is not None:
            warnings += await plan_connector_grants(
                bundle_root,
                self._directory,
                bundled={plan.slug for plan in connectors},
                writing={(_GRANTS_FROM[s.kind], s.name) for s in grant_steps},
            )

        # --- table data (after tables exist) ---------------------------------
        steps += data_steps

        _check_step_count(steps)

        for i, step in enumerate(steps):
            step.index = i

        variables = variables_from_manifest(pod_manifest)
        # An account written into the bundle is known now; one given as an
        # import variable is checked again when the apply is requested.
        await account_checks.skip_unusable_accounts(
            steps,
            warnings,
            replacements={v.name: v.default for v in variables if v.default},
            check=self._accounts,
        )
        refuse_shared_workflows(
            bundle_root, steps, existing_schedules, existing_workflows
        )

        outcome, notes = await bundle_outcome.plan_outcome(pod_manifest, self._outcome)

        return ImportPlan(
            format_version=format_version,
            bundle_name=bundle_name,
            description=pod_manifest.get("description"),
            steps=steps,
            variables=variables,
            warnings=warnings + notes,
            connectors=connectors,
            outcome=outcome,
        )

    async def _table_steps(
        self, bundle_root: Path, warnings: list[str]
    ) -> tuple[list[PlanStep], list[PlanStep]]:
        """TABLE steps in foreign-key order, and the TABLE_DATA steps that run
        last. A table the pod has only gains columns, and only a table this
        import creates is seeded (``kept_parts.table_data_step``)."""
        existing = await self._existing.table_names()
        tables: list[PlanStep] = []
        data: list[PlanStep] = []
        seeded_rows = 0
        for table_dir in _order_table_dirs_by_dependency(
            _resource_subdirs(bundle_root, "tables")
        ):
            name = table_dir.name
            step = PlanStep(
                index=0, kind=StepKind.TABLE, name=name, action=StepAction.CREATE
            )
            if name in existing:
                step, notes = kept_parts.existing_table_step(
                    name,
                    await self._existing.table_manifest(name),
                    load_resource_payload(table_dir, name, resource_type="tables"),
                )
                warnings += notes
            tables.append(step)
            data_path = table_dir / TABLE_DATA_FILE
            if data_path.is_file():
                # Counted whether or not they load: the caps bound the bundle.
                seeded_rows += _check_seed_rows(data_path, name, seeded_rows)
                data.append(
                    kept_parts.table_data_step(
                        name,
                        table_created=step.action is StepAction.CREATE,
                        loaded=self._existing.ours(StepKind.TABLE_DATA, name),
                    )
                )
        return tables, data

    def _app_step(self, bundle_dir: Path, existing: set[str]) -> PlanStep:
        """An APP step, carrying the visibility the app will be created with.

        An imported app takes the app default (POD) when its manifest is
        silent, and whatever the manifest says otherwise -- which may be
        PUBLIC, serving its HTML/JS on a guessable host to anyone. The plan is
        where the importer is supposed to see what will happen. An app the pod
        already has is kept with its own visibility, so it claims nothing.
        """
        name = bundle_dir.name
        step = kept_parts.create_or_keep(StepKind.APP, name, existing)
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


class ServiceExistingResources(machine_bundle.PodMachineNames):
    """Production :class:`ExistingResources` — lists the pod's resources through
    the module services over a caller-supplied short UoW + ``Context``. Lazy
    imports keep the module import cheap and cycle-free (mirrors the exporter)."""

    def __init__(self, *, uow: Any, ctx: Any, pod_id: UUID, user_id: UUID):
        self._uow = uow
        self._ctx = ctx
        self._pod_id = pod_id
        self._user_id = user_id

    async def table_names(self) -> set[str]:
        from app.modules.datastore.contracts.provisioning import list_table_names

        return set(
            await list_table_names(self._uow, pod_id=self._pod_id, ctx=self._ctx)
        )

    async def table_manifest(self, name: str) -> dict[str, Any] | None:
        from app.modules.datastore.contracts import TableResponse
        from app.modules.datastore.contracts.provisioning import get_table

        table = await get_table(
            self._uow, pod_id=self._pod_id, name=name, ctx=self._ctx
        )
        if table is None:
            return None
        return TableResponse.model_validate(table).model_dump(mode="json")

    async def function_names(self) -> set[str]:
        from app.modules.function.contracts.provisioning import list_function_names

        return set(
            await list_function_names(
                self._uow, pod_id=self._pod_id, user_id=self._user_id, ctx=self._ctx
            )
        )

    async def agent_names(self) -> set[str]:
        from app.modules.agent.contracts.provisioning import list_agents

        agents = await list_agents(
            self._uow, pod_id=self._pod_id, user_id=self._user_id, ctx=self._ctx
        )
        # Excluded to match the exporter -- a bundle can never contain one, so
        # this must never plan an update against the target pod's.
        return {str(a.name or "") for a in agents if is_exportable_agent(a)}

    async def workflow_names(self) -> set[str]:
        from app.modules.workflow.contracts.provisioning import list_workflow_names

        return set(
            await list_workflow_names(
                self._uow, pod_id=self._pod_id, user_id=self._user_id, ctx=self._ctx
            )
        )

    async def schedule_names(self) -> set[str]:
        from app.modules.schedule.contracts.provisioning import list_schedules

        schedules = await list_schedules(self._uow, pod_id=self._pod_id, ctx=self._ctx)
        return {str(s.name or "") for s in schedules}

    async def app_names(self) -> set[str]:
        from app.modules.apps.contracts.provisioning import list_app_names

        return set(
            await list_app_names(
                self._uow, pod_id=self._pod_id, user_id=self._user_id, ctx=self._ctx
            )
        )

    async def existing_paths(self, paths: Sequence[str]) -> set[str]:
        """One lookup per path, the question the FILE step asks again when it
        runs. A plan carries at most ``MAX_IMPORT_PLAN_STEPS`` paths."""
        from app.modules.datastore.contracts.provisioning import file_exists

        return {
            path
            for path in paths
            if await file_exists(
                self._uow, pod_id=self._pod_id, path=path, ctx=self._ctx
            )
        }

    async def surface_names(self) -> set[str]:
        """The pod's surface *names* — the key the applier creates by, and the
        one the exporter writes a directory per. Keyed by platform, the diff
        called a second Slack surface the first one."""
        from app.modules.agent_surfaces.contracts.provisioning import list_surfaces

        try:
            surfaces = await list_surfaces(self._uow, pod_id=self._pod_id)
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
