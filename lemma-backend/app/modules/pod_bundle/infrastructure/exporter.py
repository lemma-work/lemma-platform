"""Assemble a pod's resources into a bundle archive.

Produces a directory tree byte-compatible with the CLI export
(``lemma pods export`` / ``lemma_cli.cli_app.pod_bundle.export_pod_bundle``) and
packs it with the shared :func:`lemma_pod_bundle.pack_bundle`, so a bundle
built by the API and one built by the CLI are interchangeable on import.

The exporter is handed an already-open short UoW + session-bound ``Context`` by
the job: it does every ``list`` + ``get`` DB read while that scope is live and
assembles the zip bytes in a ``TemporaryDirectory`` (no DB) before returning.
The job then closes the UoW and uploads the bytes with no pooled connection
held — the pool-safety discipline the whole feature exists to enforce.

Format parity with the CLI: for each resource we fetch the domain entity via
the module service, render it through that module's API *Response* schema (the
exact object the GET endpoint returns to the SDK), ``model_dump(mode="json")``
it, then feed the dict to the shared per-resource normalizer. The normalizers
expect the response shape, not the raw entity, so this mirrors what the CLI
feeds them (SDK response dicts) precisely.
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from lemma_pod_bundle import pack_bundle
from lemma_pod_bundle.layout import (
    RESOURCE_DIRS,
    TABLE_DATA_FILE,
    _record_export_contents,
    _write_json,
    extract_large_text,
    normalize_file_folders,
)
from lemma_pod_bundle.normalize import (
    _attach_permissions_payload,
    _normalize_app_payload,
    _normalize_function_payload,
    _normalize_pod_payload,
    _normalize_schedule_payload,
    _normalize_table_payload,
    _normalize_workflow_payload,
)
from lemma_pod_bundle.portability import _extract_portable_variables

from app.modules.pod_bundle.infrastructure.exporter_agents import export_agents
from app.modules.pod_bundle.infrastructure.exporter_surfaces import (
    export_surfaces,
)
from app.core.authorization.context import Context
from app.core.concurrency.offload import run_blocking
from app.core.helpers.slug import slugify
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.pod_bundle.config import pod_bundle_settings

logger = get_logger(__name__)

# Resource dirs the exporter knows how to fill, in a stable order. ``files`` is
# out of scope for the export slice (the CLI's file/asset export is separate and
# best-effort); we still create the empty dir for layout parity.
_EXPORT_RESOURCE_TYPES = (
    "tables",
    "functions",
    "agents",
    "workflows",
    "schedules",
    "surfaces",
    "apps",
)

_RECORD_EXPORT_PAGE = 1_000


ProgressCallback = Callable[[int, int], Awaitable[None]]


class _RecordBudget:
    """Bounds exported seed rows: a per-table cap and a running total across all
    tables. When exhausted, remaining tables export schema-only. Trips append a
    human warning so the export status can tell the caller what was dropped."""

    def __init__(self, *, per_table: int, total: int, warnings: list[str]):
        self._per_table = max(0, per_table)
        self._remaining = max(0, total)
        self._warnings = warnings

    def table_cap(self) -> int:
        return min(self._per_table, self._remaining)

    def note_written(self, *, table: str, written: int, available: int) -> None:
        self._remaining -= written
        if written == 0 and available > 0:
            self._warnings.append(
                f"table '{table}' seed data omitted: export size/record cap reached"
            )
        elif available > written:
            self._warnings.append(
                f"table '{table}' seed data truncated to {written} of "
                f"{available} rows (export cap)"
            )

    def note_skipped(self, *, table: str) -> None:
        self._warnings.append(
            f"table '{table}' seed data omitted: export record budget reached"
        )


class _ByteBudget:
    """Bounds exported payload bytes: a per-item ceiling and a running total.
    ``allow(name, size)`` records a skip warning and returns ``False`` when an
    item is too large or the budget is spent (used for whole-item payloads like
    pod files and app builds). ``item_cap()`` + ``consume()`` support the
    truncate-to-fit path a table's data.csv needs."""

    def __init__(self, *, per_file: int, total: int, warnings: list[str]):
        self._per_file = max(0, per_file)
        self._remaining = max(0, total)
        self._warnings = warnings

    def allow(self, *, name: str, size: int) -> bool:
        if size > self._per_file:
            self._warnings.append(
                f"'{name}' skipped: {size} bytes exceeds the per-item limit "
                f"({self._per_file} bytes)"
            )
            return False
        if size > self._remaining:
            self._warnings.append(f"'{name}' skipped: export size budget reached")
            return False
        self._remaining -= size
        return True

    def item_cap(self) -> int:
        """The most bytes a single item may use right now (per-item ceiling
        clamped by what's left of the total)."""
        return min(self._per_file, self._remaining)

    def consume(self, size: int) -> None:
        self._remaining -= max(0, size)


def _dump_response(response: Any) -> dict[str, Any]:
    """Render an API response model the way the GET controller serializes it for
    the SDK — the exact dict the normalizers were written against."""
    return response.model_dump(mode="json")


class BundleExporter:
    """Builds a pod bundle archive from a pod's resources.

    :meth:`export` does all DB reads inside the caller-supplied ``uow``/``ctx``
    and returns the assembled zip bytes. Every provider module's operations are
    imported lazily inside it, so importing this module stays cheap and free of
    the cycle `module.py -> handlers -> exporter` would otherwise close.
    """

    async def export(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        include: list[str] | None,
        data_tables: list[str] | None = None,
        file_folders: list[str] | None = None,
        ctx: Context,
        uow: SqlAlchemyUnitOfWork,
        on_progress: ProgressCallback,
    ) -> tuple[str, bytes, list[str]]:
        """Assemble the bundle and return ``(bundle_filename, zip_bytes, warnings)``.

        All ``list`` + ``get`` reads run against the live ``uow``/``ctx``; the
        zip is built in a temp dir with no DB. ``on_progress(done, total)`` is
        awaited as each resource type completes so the job can refresh Redis.
        The schema always exports fully; only row data + file/asset bytes are
        bounded (best-effort), with each cap that trips noted in ``warnings``.

        Row data and files are selected by name, never in bulk: a table is
        seeded only when it appears in ``data_tables``, and a file is included
        only when it lives under one of ``file_folders``. Naming nothing exports
        the pod's resources alone.
        """
        selected = _normalize_include(include)
        data_tables_set = _normalize_data_tables(data_tables)
        folder_paths = normalize_file_folders(file_folders)
        wants_data = bool(data_tables_set)
        warnings: list[str] = []
        record_budget = _RecordBudget(
            per_table=pod_bundle_settings.pod_bundle_export_max_records_per_table,
            total=pod_bundle_settings.pod_bundle_export_max_records_total,
            warnings=warnings,
        )
        # Data + files share ONE conservative byte pool; app builds get their own
        # so a big app can't starve seed data (and vice versa).
        data_budget = _ByteBudget(
            per_file=pod_bundle_settings.pod_bundle_export_max_file_bytes,
            total=pod_bundle_settings.pod_bundle_export_max_files_total_bytes,
            warnings=warnings,
        )
        app_budget = _ByteBudget(
            per_file=pod_bundle_settings.pod_bundle_export_max_app_bytes,
            total=pod_bundle_settings.pod_bundle_export_max_apps_total_bytes,
            warnings=warnings,
        )

        # Lazy imports (avoid import cycles + keep the module import cheap).
        from app.modules.apps.contracts.provisioning import (
            list_app_names,
            require_app,
        )
        from app.modules.connectors.contracts.provisioning import (
            resolve_account_connector,
        )
        from app.modules.datastore.contracts.provisioning import (
            get_table,
            list_table_names,
        )
        from app.modules.function.contracts.provisioning import (
            list_function_names,
            require_function,
        )
        from app.modules.pod.contracts.provisioning import get_pod
        from app.modules.schedule.contracts.provisioning import list_schedules
        from app.modules.workflow.contracts.provisioning import (
            get_workflow,
            list_workflow_names,
        )

        with tempfile.TemporaryDirectory(prefix="lemma-pod-export-") as tmp:
            root = Path(tmp)
            for resource_dir in RESOURCE_DIRS:
                (root / resource_dir).mkdir(parents=True, exist_ok=True)

            # --- pod.json ------------------------------------------------------
            pod = await get_pod(uow, pod_id=pod_id)
            if pod is None:
                # ctx already authorized POD_READ, so this only happens on a race
                # with a pod delete — treat as an invalid export.
                from app.modules.pod_bundle.domain.errors import BundleInvalidError

                raise BundleInvalidError(f"Pod {pod_id} no longer exists.")
            pod_dict = _pod_response_dict(pod)
            pod_name = str(pod_dict.get("name") or str(pod_id)).strip() or str(pod_id)
            _write_json(root / "pod.json", _normalize_pod_payload(pod_dict))

            # Total = the pod.json step + every selected resource type; drives the
            # progress bar deterministically without a pre-count DB round-trip.
            total = 1 + sum(1 for rtype in _EXPORT_RESOURCE_TYPES if rtype in selected)
            done = 1
            await on_progress(done, total)

            # --- tables (+ optional data) -------------------------------------
            if "tables" in selected:
                exported_table_names: set[str] = set()
                for table_name in sorted(
                    await list_table_names(uow, pod_id=pod_id, ctx=ctx)
                ):
                    exported_table_names.add(table_name)
                    table = await get_table(
                        uow, pod_id=pod_id, name=table_name, ctx=ctx
                    )
                    if table is None:
                        continue
                    dir_ = root / "tables" / table_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    _write_json(
                        dir_ / f"{table_name}.json",
                        _normalize_table_payload(_table_response_dict(table)),
                    )
                    # Seed this table only when the caller asked for all data or
                    # named it explicitly.
                    if table_name in data_tables_set:
                        cap = record_budget.table_cap()
                        if cap <= 0:
                            record_budget.note_skipped(table=table_name)
                        else:
                            written, available = await self._export_table_data(
                                uow=uow,
                                pod_id=pod_id,
                                table=table,
                                user_id=user_id,
                                dest=dir_ / TABLE_DATA_FILE,
                                cap=cap,
                                data_budget=data_budget,
                            )
                            record_budget.note_written(
                                table=table_name, written=written, available=available
                            )
                # A name in data_tables that isn't a real table can't be seeded —
                # tell the caller rather than silently dropping it.
                for missing in sorted(data_tables_set - exported_table_names):
                    warnings.append(
                        f"table '{missing}' requested for seed data but not found "
                        f"in the pod; skipped"
                    )
                done += 1
                await on_progress(done, total)

            # --- functions ----------------------------------------------------
            if "functions" in selected:
                for function_name in sorted(
                    await list_function_names(
                        uow, pod_id=pod_id, user_id=user_id, ctx=ctx
                    )
                ):
                    function = await require_function(
                        uow, pod_id=pod_id, name=function_name, user_id=user_id, ctx=ctx
                    )
                    dir_ = root / "functions" / function_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    payload = _normalize_function_payload(
                        _function_response_dict(function)
                    )
                    grantee_id = getattr(function, "id", None)
                    if grantee_id is not None:
                        grants = await _resource_grants_payload(
                            uow,
                            pod_id=pod_id,
                            grantee_type="FUNCTION",
                            grantee_id=grantee_id,
                            warnings=warnings,
                            grantee_name=function_name,
                        )
                        # Attach even an EMPTY grant list — see
                        # _resource_grants_payload for why None differs from [].
                        if grants is not None:
                            payload = _attach_permissions_payload(payload, grants)
                    payload = extract_large_text(
                        payload,
                        field_name="code",
                        file_name="code.py",
                        resource_dir=dir_,
                    )
                    _write_json(dir_ / f"{function_name}.json", payload)
                done += 1
                await on_progress(done, total)

            # --- agents -------------------------------------------------------
            if "agents" in selected:
                await export_agents(
                    uow,
                    root=root,
                    pod_id=pod_id,
                    user_id=user_id,
                    ctx=ctx,
                    grants_payload=_resource_grants_payload,
                    warnings=warnings,
                )
                done += 1
                await on_progress(done, total)

            # --- workflows ----------------------------------------------------
            if "workflows" in selected:
                for workflow_name in sorted(
                    await list_workflow_names(
                        uow, pod_id=pod_id, user_id=user_id, ctx=ctx
                    )
                ):
                    flow = await get_workflow(
                        uow, pod_id=pod_id, name=workflow_name, user_id=user_id, ctx=ctx
                    )
                    dir_ = root / "workflows" / workflow_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    _write_json(
                        dir_ / f"{workflow_name}.json",
                        _normalize_workflow_payload(_flow_response_dict(flow)),
                    )
                done += 1
                await on_progress(done, total)

            # --- schedules ----------------------------------------------------
            if "schedules" in selected:
                schedules = await list_schedules(uow, pod_id=pod_id, ctx=ctx)
                for schedule in sorted(
                    schedules, key=lambda s: str(s.name or s.id or "")
                ):
                    schedule_name = str(schedule.name or schedule.id or "")
                    dir_ = root / "schedules" / schedule_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    raw_schedule = _schedule_response_dict(schedule)
                    account_id = raw_schedule.get("account_id")
                    if account_id:
                        info = await resolve_account_connector(
                            uow, UUID(str(account_id))
                        )
                        if info is None:
                            from app.modules.pod_bundle.domain.errors import (
                                BundleInvalidError,
                            )

                            raise BundleInvalidError(
                                f"Schedule '{schedule_name}' references account "
                                f"{account_id}, which no longer exists."
                            )
                        raw_schedule["connector_id"], raw_schedule["connector_kind"] = (
                            info
                        )
                    payload = _normalize_schedule_payload(raw_schedule)
                    payload.setdefault("name", schedule_name)
                    _write_json(dir_ / f"{schedule_name}.json", payload)
                done += 1
                await on_progress(done, total)

            # --- surfaces (best-effort) ---------------------------------------
            if "surfaces" in selected:
                await export_surfaces(root, uow, pod_id, warnings)
                done += 1
                await on_progress(done, total)

            # --- apps ---------------------------------------------------------
            if "apps" in selected:
                for app_name in sorted(
                    await list_app_names(uow, pod_id=pod_id, user_id=user_id, ctx=ctx)
                ):
                    app = await require_app(
                        uow, pod_id=pod_id, name=app_name, user_id=user_id, ctx=ctx
                    )
                    dir_ = root / "apps" / app_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    _write_json(
                        dir_ / f"{app_name}.json",
                        _normalize_app_payload(_app_response_dict(app)),
                    )
                    # The app's source code is the critical payload: without it a
                    # re-import gets an empty app. Download source (rebuildable) or,
                    # for widget/no-source apps, the built dist — mirrors the CLI's
                    # _download_app_assets so an API export carries app code too.
                    await self._export_app_assets(
                        uow=uow,
                        pod_id=pod_id,
                        app_name=app_name,
                        user_id=user_id,
                        dest=dir_,
                        ctx=ctx,
                        byte_budget=app_budget,
                    )
                done += 1
                await on_progress(done, total)

            # --- files (opt-in, byte-budgeted, shares the data pool) ----------
            wrote_files = False
            if folder_paths:
                wrote_files = await self._export_pod_files(
                    root=root,
                    uow=uow,
                    pod_id=pod_id,
                    ctx=ctx,
                    data_budget=data_budget,
                    warnings=warnings,
                    folder_paths=folder_paths,
                )

            # --- portability + contents manifest (no DB) ----------------------
            _extract_portable_variables(root)
            _record_export_contents(
                root,
                included=selected if include else set(),
                excluded=set(),
                names=set(),
                with_data=wants_data,
                with_files=wrote_files,
            )

            # DEFLATE over the whole bundle is CPU-bound; keep it off the loop.
            zip_bytes = await run_blocking(pack_bundle, root, limiter="cpu_bound")

        bundle_filename = f"{slugify(pod_name) or 'pod'}.zip"
        await on_progress(total, total)
        return bundle_filename, zip_bytes, warnings

    async def _export_table_data(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        pod_id: UUID,
        table,
        user_id: UUID,
        dest: Path,
        cap: int,
        data_budget: _ByteBudget,
    ) -> tuple[int, int]:
        """Page up to ``cap`` rows and write ``data.csv`` (CLI record-IO cell
        semantics), then trim trailing rows so the file fits the shared byte
        budget (a 10k-row table can still be huge). Consumes the bytes actually
        written. Returns ``(rows_written, total_available)`` so the caller can
        warn on row- or byte-driven truncation."""
        from lemma_pod_bundle.normalize import _SEED_STRIP_COLUMNS

        from app.modules.datastore.contracts.provisioning import read_table_rows

        rows: list[dict[str, Any]] = []
        offset = 0
        available = 0
        while len(rows) < cap:
            want = min(_RECORD_EXPORT_PAGE, cap - len(rows))
            batch, available = await read_table_rows(
                uow,
                pod_id=pod_id,
                table=table,
                user_id=user_id,
                limit=want,
                offset=offset,
            )
            rows.extend(batch)
            offset += len(batch)
            if not batch or len(batch) < want:
                break
        if not rows:
            return 0, available
        # Drop audit/ownership columns so a re-import re-owns rows to the importer,
        # matching the CLI seed contract.
        cleaned = [
            {k: v for k, v in row.items() if k not in _SEED_STRIP_COLUMNS}
            for row in rows
        ]
        csv_text, kept = _csv_within_bytes(cleaned, data_budget.item_cap())
        if kept == 0:
            return 0, max(available, len(cleaned))
        await run_blocking(
            dest.write_text, csv_text, encoding="utf-8", limiter="cpu_bound"
        )
        data_budget.consume(len(csv_text.encode("utf-8")))
        return kept, max(available, len(cleaned))

    async def _export_app_assets(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        pod_id: UUID,
        app_name: str,
        user_id: UUID,
        dest: Path,
        ctx: Context,
        byte_budget: _ByteBudget,
    ) -> None:
        """Bundle an app's code: its source (extracted to ``source/``), or — for a
        widget/no-source app — its built ``dist.zip``. Best-effort and byte-budgeted:
        an app with neither archive, or one over budget, exports metadata-only. A
        one-file app lands as ``source/index.html``; the CLI writes ``html.html``."""
        from app.modules.apps.contracts import AppNotFoundError
        from app.modules.apps.contracts.provisioning import (
            read_app_archive,
            resolve_app_dist_archive,
            resolve_app_source_archive,
        )

        # Prefer source (rebuildable in the target pod); the exported vite dist is
        # baked with the source pod id and is not portable.
        source_bytes: bytes | None = None
        try:
            app_id, source_path = await resolve_app_source_archive(
                uow, pod_id=pod_id, name=app_name, user_id=user_id, ctx=ctx
            )
            source_bytes = await read_app_archive(
                uow, app_id=app_id, archive_path=source_path
            )
        except AppNotFoundError:
            source_bytes = None

        if source_bytes:
            if byte_budget.allow(
                name=f"apps/{app_name}/source", size=len(source_bytes)
            ):
                await run_blocking(
                    _extract_zip_bytes,
                    source_bytes,
                    dest / "source",
                    limiter="cpu_bound",
                )
            return

        dist_bytes: bytes | None = None
        try:
            app_id, dist_path = await resolve_app_dist_archive(
                uow, pod_id=pod_id, name=app_name, user_id=user_id, ctx=ctx
            )
            dist_bytes = await read_app_archive(
                uow, app_id=app_id, archive_path=dist_path
            )
        except AppNotFoundError:
            dist_bytes = None

        if dist_bytes and byte_budget.allow(
            name=f"apps/{app_name}/dist.zip", size=len(dist_bytes)
        ):
            await run_blocking(
                (dest / "dist.zip").write_bytes, dist_bytes, limiter="cpu_bound"
            )

    async def _export_pod_files(
        self,
        *,
        root: Path,
        uow: SqlAlchemyUnitOfWork,
        pod_id: UUID,
        ctx: Context,
        data_budget: "_ByteBudget",
        warnings: list[str],
        folder_paths: list[str],
    ) -> bool:
        from app.modules.pod_bundle.infrastructure.exporter_files import (
            export_pod_files,
        )

        return await export_pod_files(
            root=root,
            uow=uow,
            pod_id=pod_id,
            ctx=ctx,
            data_budget=data_budget,
            warnings=warnings,
            folder_paths=folder_paths,
        )


# --- response-dict adapters (per-module GET serialization) -------------------


async def _resource_grants_payload(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    warnings: list[str],
    grantee_name: str,
) -> dict[str, Any] | None:
    """Serialize an agent's/function's resource grants into the bundle's portable
    ``{"grants": [...]}`` shape (keyed by ``resource_name``, never a source-org id).

    Mirrors the ``…/permissions`` GET endpoint the CLI exporter reads, so a pod
    exported through the async backend keeps the same executable grants a
    CLI-exported one does — without them, an imported agent/function can be created
    but can't call the tables/functions it was granted. ``list_grantee_resource_grants``
    already drops grants whose resource no longer resolves to a name, and the applier
    skips any that don't resolve in the target pod, so this stays best-effort/portable.
    Best-effort: a failure to read grants logs, warns the exporter, and returns
    ``None`` — "grants unknown, leave the target's alone" — rather than sinking
    the whole export. A grantee that simply holds none returns ``{"grants": []}``,
    which imports as "holds nothing". Collapsing those two is what let an export
    silently change an imported workload's access — and returning ``None``
    *without* a warning is the same silence by another route, because the
    imported workload then runs on whatever the target pod happens to grant."""
    from app.core.authorization.grants import list_grantee_resource_grants

    try:
        grouped = await list_grantee_resource_grants(
            uow.session,
            pod_id=pod_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )
    except Exception as exc:  # noqa: BLE001 - grant export is best-effort
        logger.debug(
            "pod_bundle.exporter.skipping_grant_export_s_s.diagnostic",
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )
        warnings.append(
            f"{grantee_type.lower()} '{grantee_name}' exported without its "
            f"permissions: they could not be read ({type(exc).__name__}). On "
            f"import it will keep whatever grants the target pod already gives it."
        )
        return None
    return {
        "grants": [
            {
                "resource_type": resource_type.value,
                "resource_name": resource_name,
                "permission_ids": sorted(set(permission_ids)),
            }
            for (resource_type, resource_name), permission_ids in grouped.items()
        ]
    }


def _pod_response_dict(pod: Any) -> dict[str, Any]:
    from app.modules.pod.contracts import PodResponse

    return _dump_response(PodResponse.model_validate(pod))


def _table_response_dict(table: Any) -> dict[str, Any]:
    from app.modules.datastore.contracts import TableResponse

    return _dump_response(TableResponse.model_validate(table))


def _function_response_dict(function: Any) -> dict[str, Any]:
    from app.modules.function.contracts import FunctionResponse

    return _dump_response(FunctionResponse.model_validate(function.model_dump()))


def _agent_response_dict(agent: Any) -> dict[str, Any]:
    from app.modules.agent.contracts import AgentResponse

    return _dump_response(AgentResponse.model_validate(agent))


def _flow_response_dict(flow: Any) -> dict[str, Any]:
    from app.modules.workflow.contracts import workflow_response_from_domain

    return _dump_response(workflow_response_from_domain(flow))


def _schedule_response_dict(schedule: Any) -> dict[str, Any]:
    from app.modules.schedule.contracts import ScheduleResponse

    return _dump_response(ScheduleResponse.model_validate(schedule))


def _app_response_dict(app: Any) -> dict[str, Any]:
    from app.modules.apps.contracts import AppDetailResponse

    return _dump_response(AppDetailResponse.model_validate(app))


# --- small format helpers (mirror lemma_cli.cli_app.pod_bundle) --------------


def _normalize_include(include: list[str] | None) -> set[str]:
    """Resolve the caller's ``include`` list to the set of resource-dir names to
    export. ``None``/empty means everything the exporter knows how to produce."""
    from lemma_pod_bundle.layout import normalize_resource_dir_name

    if not include:
        return set(_EXPORT_RESOURCE_TYPES)
    resolved: set[str] = set()
    for value in include:
        dir_name = normalize_resource_dir_name(str(value))
        if dir_name in _EXPORT_RESOURCE_TYPES:
            resolved.add(dir_name)
    return resolved or set(_EXPORT_RESOURCE_TYPES)


def _normalize_data_tables(data_tables: list[str] | None) -> set[str]:
    """The set of table names to seed row data for. ``None``/empty means none
    Blank entries are dropped."""
    if not data_tables:
        return set()
    return {name.strip() for name in data_tables if name and name.strip()}


def _extract_zip_bytes(data: bytes, dest_dir: Path) -> None:
    """Extract a source zip into ``dest_dir``, guarding against path traversal
    (zip-slip), mirroring the CLI's app-source extraction check."""
    import io
    import zipfile

    from app.modules.pod_bundle.domain.errors import BundleInvalidError

    dest_dir.mkdir(parents=True, exist_ok=True)
    base = dest_dir.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            target = (dest_dir / member.filename).resolve()
            if target != base and not target.is_relative_to(base):
                raise BundleInvalidError(
                    f"Unsafe path in app source archive: {member.filename}"
                )
        archive.extractall(dest_dir)


def _csv_within_bytes(rows: list[dict[str, Any]], max_bytes: int) -> tuple[str, int]:
    """Render records to CSV (CLI ``record_io.write_export_rows`` cell semantics:
    complex cells -> JSON text, None -> empty), keeping only as many *leading*
    rows as fit within ``max_bytes`` (header always included). Returns
    ``(csv_text, rows_kept)``; ``("", 0)`` when not even the header + one row fit."""
    import csv
    import io
    import json

    if not rows or max_bytes <= 0:
        return "", 0

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    def _cell(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _line(mapping: dict[str, str]) -> str:
        buf = io.StringIO()
        csv.DictWriter(buf, fieldnames=fieldnames).writerow(mapping)
        return buf.getvalue()

    header_buf = io.StringIO()
    csv.DictWriter(header_buf, fieldnames=fieldnames).writeheader()
    header = header_buf.getvalue()

    used = len(header.encode("utf-8"))
    if used > max_bytes:
        return "", 0
    parts = [header]
    kept = 0
    for row in rows:
        line = _line({key: _cell(row.get(key)) for key in fieldnames})
        size = len(line.encode("utf-8"))
        if used + size > max_bytes:
            break
        parts.append(line)
        used += size
        kept += 1
    if kept == 0:
        return "", 0
    return "".join(parts), kept
