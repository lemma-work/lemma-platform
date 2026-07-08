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
)
from lemma_pod_bundle.normalize import (
    _attach_permissions_payload,
    _normalize_agent_payload,
    _normalize_app_payload,
    _normalize_function_payload,
    _normalize_pod_payload,
    _normalize_schedule_payload,
    _normalize_surface_payload,
    _normalize_table_payload,
    _normalize_workflow_payload,
)
from lemma_pod_bundle.portability import _extract_portable_variables

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


async def _resolve_account_connector_info(
    uow: SqlAlchemyUnitOfWork, account_id: UUID
) -> tuple[str, str] | None:
    """Ground-truth ``(connector_id, provider)`` for an account, resolved via
    the connectors module — never inferred from a resource's own name (e.g. a
    surface's platform or a schedule's directory name), since that guess is
    wrong for any resource type with no platform concept of its own. Returns
    ``None`` if the account (or its auth config) no longer exists."""
    from app.modules.connectors.api.dependencies import get_connector_service

    service = get_connector_service(uow)
    account = await service.account_repository.get(account_id)
    if account is None:
        return None
    auth_config = await service.auth_config_repository.get(account.auth_config_id)
    if auth_config is None:
        return None
    return account.connector_id, auth_config.provider.value


class BundleExporter:
    """Builds a pod bundle archive from a pod's resources.

    Constructed with the per-UoW service builders it needs; :meth:`export` does
    all DB reads inside the caller-supplied ``uow``/``ctx`` and returns the
    assembled zip bytes.
    """

    def __init__(self) -> None:
        # Service builders are imported lazily inside export() to avoid import
        # cycles at module import time (module.py imports handlers -> exporter).
        pass

    async def export(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        with_data: bool,
        include: list[str] | None,
        data_tables: list[str] | None = None,
        with_files: bool = False,
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

        Row data is opt-in and off by default: a table is seeded only when
        ``with_data`` (every table) or its name is in ``data_tables`` (just those).
        """
        selected = _normalize_include(include)
        data_tables_set = _normalize_data_tables(data_tables)
        wants_data = with_data or bool(data_tables_set)
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
        from app.modules.agent.api.dependencies import get_agent_service
        from app.modules.apps.api.dependencies import build_app_service
        from app.modules.datastore.api.dependencies import (
            build_record_service,
            build_table_service,
        )
        from app.modules.datastore.services.table_context import TableContext
        from app.modules.function.api.dependencies import build_function_service
        from app.modules.pod.infrastructure.pod_repositories import PodRepository
        from app.modules.schedule.api.dependencies import get_schedule_service
        from app.modules.workflow.api.dependencies import get_flow_service

        from app.core.infrastructure.events.message_bus import get_message_bus

        message_bus = get_message_bus()

        with tempfile.TemporaryDirectory(prefix="lemma-pod-export-") as tmp:
            root = Path(tmp)
            for resource_dir in RESOURCE_DIRS:
                (root / resource_dir).mkdir(parents=True, exist_ok=True)

            # --- pod.json ------------------------------------------------------
            pod = await PodRepository(uow, message_bus=message_bus).get(pod_id)
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
            total = 1 + sum(
                1 for rtype in _EXPORT_RESOURCE_TYPES if rtype in selected
            )
            done = 1
            await on_progress(done, total)

            # --- tables (+ optional data) -------------------------------------
            if "tables" in selected:
                table_service = build_table_service(uow)
                record_service = build_record_service(uow) if wants_data else None
                schema_name = table_service.schema_manager.get_schema_name(pod_id)
                tables, _ = await table_service.list_tables(pod_id, ctx, limit=1000)
                exported_table_names: set[str] = set()
                for summary in sorted(tables, key=lambda t: str(t.name or "")):
                    table_name = str(summary.name or "")
                    exported_table_names.add(table_name)
                    table = await table_service.get_table(pod_id, table_name, ctx)
                    dir_ = root / "tables" / table_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    _write_json(
                        dir_ / f"{table_name}.json",
                        _normalize_table_payload(_table_response_dict(table)),
                    )
                    # Seed this table only when the caller asked for all data or
                    # named it explicitly.
                    seed_this = with_data or table_name in data_tables_set
                    if seed_this and record_service is not None:
                        cap = record_budget.table_cap()
                        if cap <= 0:
                            record_budget.note_skipped(table=table_name)
                        else:
                            written, available = await self._export_table_data(
                                record_service=record_service,
                                table_context=TableContext.from_table_entity(
                                    table, schema_name, events_enabled=False
                                ),
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
                function_service = build_function_service(uow)
                functions, _ = await function_service.list_functions(
                    pod_id, user_id, limit=1000, ctx=ctx
                )
                for summary in sorted(functions, key=lambda f: str(f.name or "")):
                    function_name = str(summary.name or "")
                    function = await function_service.get_function_by_name(
                        pod_id, function_name, user_id, raise_not_found=True, ctx=ctx
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
                        )
                        if grants:
                            payload = _attach_permissions_payload(payload, grants)
                    payload = _extract_large_text(
                        payload, field_name="code", file_name="code.py", resource_dir=dir_
                    )
                    _write_json(dir_ / f"{function_name}.json", payload)
                done += 1
                await on_progress(done, total)

            # --- agents -------------------------------------------------------
            if "agents" in selected:
                agent_service = get_agent_service(uow)
                agents, _ = await agent_service.list_agents(
                    pod_id=pod_id, limit=1000, requester_user_id=user_id, ctx=ctx
                )
                for summary in sorted(agents, key=lambda a: str(a.name or "")):
                    agent_name = str(summary.name or "")
                    agent = await agent_service.get_agent_by_name(
                        pod_id=pod_id,
                        name=agent_name,
                        requester_user_id=user_id,
                        ctx=ctx,
                    )
                    dir_ = root / "agents" / agent_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    payload = _normalize_agent_payload(_agent_response_dict(agent))
                    grantee_id = getattr(agent, "id", None)
                    if grantee_id is not None:
                        grants = await _resource_grants_payload(
                            uow,
                            pod_id=pod_id,
                            grantee_type="AGENT",
                            grantee_id=grantee_id,
                        )
                        if grants:
                            payload = _attach_permissions_payload(payload, grants)
                    payload = _extract_large_text(
                        payload,
                        field_name="instruction",
                        file_name="instruction.md",
                        resource_dir=dir_,
                    )
                    _write_json(dir_ / f"{agent_name}.json", payload)
                done += 1
                await on_progress(done, total)

            # --- workflows ----------------------------------------------------
            if "workflows" in selected:
                flow_service = get_flow_service(uow)
                flows, _ = await flow_service.list_flows(
                    pod_id, limit=1000, requester_user_id=user_id, ctx=ctx
                )
                for summary in sorted(flows, key=lambda f: str(f.name or "")):
                    workflow_name = str(summary.name or "")
                    flow = await flow_service.get_flow_by_name(
                        pod_id, workflow_name, requester_user_id=user_id, ctx=ctx
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
                schedule_service = get_schedule_service(uow)
                schedules, _ = await schedule_service.list_schedules(
                    pod_id=pod_id, limit=1000, ctx=ctx
                )
                for schedule in sorted(
                    schedules, key=lambda s: str(s.name or s.id or "")
                ):
                    schedule_name = str(schedule.name or schedule.id or "")
                    dir_ = root / "schedules" / schedule_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    raw_schedule = _schedule_response_dict(schedule)
                    account_id = raw_schedule.get("account_id")
                    if account_id:
                        info = await _resolve_account_connector_info(
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
                        raw_schedule["connector_id"], raw_schedule["provider"] = info
                    payload = _normalize_schedule_payload(raw_schedule)
                    payload.setdefault("name", schedule_name)
                    _write_json(dir_ / f"{schedule_name}.json", payload)
                done += 1
                await on_progress(done, total)

            # --- surfaces (best-effort) ---------------------------------------
            if "surfaces" in selected:
                await self._export_surfaces(root, uow, pod_id)
                done += 1
                await on_progress(done, total)

            # --- apps ---------------------------------------------------------
            if "apps" in selected:
                app_service = build_app_service(uow)
                apps, _ = await app_service.list_apps(pod_id, user_id, 1000, None, ctx=ctx)
                for summary in sorted(apps, key=lambda a: str(a.name or "")):
                    app_name = str(summary.name or "")
                    app = await app_service.get_app_by_name(
                        pod_id, app_name, user_id, raise_not_found=True, ctx=ctx
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
                        app_service=app_service,
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
            if with_files:
                wrote_files = await self._export_pod_files(
                    root=root,
                    uow=uow,
                    pod_id=pod_id,
                    ctx=ctx,
                    data_budget=data_budget,
                    warnings=warnings,
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
        record_service: Any,
        table_context: Any,
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

        rows: list[dict[str, Any]] = []
        offset = 0
        available = 0
        while len(rows) < cap:
            want = min(_RECORD_EXPORT_PAGE, cap - len(rows))
            items, total = await record_service.list_records(
                table_context, user_id, limit=want, offset=offset
            )
            available = int(total or 0)
            batch = [dict(item.data) for item in items]
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

    async def _export_surfaces(
        self, root: Path, uow: SqlAlchemyUnitOfWork, pod_id: UUID
    ) -> None:
        """Export configured surfaces best-effort: a surface that can't be
        serialized is skipped with a warning, never failing the whole export."""
        from app.modules.agent_surfaces.api.controllers.surface_controller import (
            _surface_response,
        )
        from app.modules.agent_surfaces.api.dependencies import get_surface_service

        try:
            service = get_surface_service(uow)
            surfaces, _ = await service.list_surfaces_by_pod(pod_id, limit=100)
        except Exception as exc:  # noqa: BLE001 - surfaces are best-effort
            logger.warning("Skipping surface export for pod %s: %s", pod_id, exc)
            return

        seen_names: set[str] = set()
        for surface in surfaces:
            try:
                raw_surface = _dump_response(_surface_response(surface))
                account_id = raw_surface.get("account_id")
                if account_id:
                    info = await _resolve_account_connector_info(
                        uow, UUID(str(account_id))
                    )
                    if info is None:
                        raise ValueError(
                            f"Surface references account {account_id}, which no "
                            "longer exists."
                        )
                    raw_surface["connector_id"], raw_surface["provider"] = info
                payload = _normalize_surface_payload(raw_surface)
                platform = str(payload.get("platform") or "")
                # De-dup by the surface's pod-unique name (not platform), so a pod
                # with several surfaces of one platform exports all of them.
                surface_name = str(payload.get("name") or "")
                if not platform or not surface_name or surface_name in seen_names:
                    continue
                seen_names.add(surface_name)
                dir_ = root / "surfaces" / surface_name
                dir_.mkdir(parents=True, exist_ok=True)
                _write_json(dir_ / f"{surface_name}.json", payload)
            except Exception as exc:  # noqa: BLE001 - one bad surface is not fatal
                logger.warning(
                    "Skipping surface %s in pod %s export: %s",
                    getattr(surface, "id", "?"),
                    pod_id,
                    exc,
                )

    async def _export_app_assets(
        self,
        *,
        app_service: Any,
        pod_id: UUID,
        app_name: str,
        user_id: UUID,
        dest: Path,
        ctx: Context,
        byte_budget: _ByteBudget,
    ) -> None:
        """Bundle an app's code: its source (extracted to ``source/``), or — for a
        widget/no-source app — its built ``dist.zip``. Best-effort and byte-budgeted:
        an app with neither archive, or one over budget, exports metadata-only.
        Mirrors the CLI's ``_download_app_assets`` for format parity."""
        from app.modules.apps.domain.errors import AppNotFoundError

        # Prefer source (rebuildable in the target pod); the exported vite dist is
        # baked with the source pod id and is not portable.
        source_bytes: bytes | None = None
        try:
            app_id, source_path = await app_service.resolve_source_archive(
                pod_id, app_name, user_id, ctx=ctx
            )
            source_bytes = await app_service.read_archive(app_id, source_path)
        except AppNotFoundError:
            source_bytes = None

        if source_bytes:
            if byte_budget.allow(name=f"apps/{app_name}/source", size=len(source_bytes)):
                await run_blocking(
                    _extract_zip_bytes, source_bytes, dest / "source", limiter="cpu_bound"
                )
            return

        dist_bytes: bytes | None = None
        try:
            app_id, dist_path = await app_service.resolve_dist_archive(
                pod_id, app_name, user_id, ctx=ctx
            )
            dist_bytes = await app_service.read_archive(app_id, dist_path)
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
        data_budget: _ByteBudget,
        warnings: list[str],
    ) -> bool:
        """Export the pod's POD-visible file tree into ``files/`` — folders as
        ``.folder.json``, file bytes (drawn from the shared data budget), and a
        ``.files.json`` manifest — mirroring the CLI layout so either tool can
        import the result. Returns whether any ``files/`` content was written.
        Best-effort: a file that can't be listed/downloaded is skipped, never
        failing the export."""
        from lemma_pod_bundle.layout import FILES_MANIFEST

        from app.modules.datastore.api.dependencies import build_file_service

        try:
            service = build_file_service(uow)
            entities = await self._walk_pod_files(service, pod_id, ctx)
        except Exception as exc:  # noqa: BLE001 - files are best-effort
            logger.warning("Skipping file export for pod %s: %s", pod_id, exc)
            return False

        pod_entities = [
            e for e in entities if str(getattr(e, "visibility", "") or "").upper() == "POD"
        ]
        if not pod_entities:
            return False

        files_root = root / "files"
        files_root.mkdir(parents=True, exist_ok=True)
        wrote = False

        # Folders first so parent dirs exist before their files land.
        for folder in sorted(
            (e for e in pod_entities if e.is_folder), key=lambda e: str(e.path or "")
        ):
            parts = [p for p in str(folder.path or "").split("/") if p]
            if not parts:
                continue
            target = files_root.joinpath(*parts)
            target.mkdir(parents=True, exist_ok=True)
            _write_json(
                target / ".folder.json",
                {"description": folder.description, "visibility": folder.visibility},
            )
            wrote = True

        file_manifest: list[dict[str, Any]] = []
        for entity in sorted(
            (e for e in pod_entities if e.is_file), key=lambda e: str(e.path or "")
        ):
            path = str(entity.path or "")
            parts = [p for p in path.split("/") if p]
            if not parts:
                continue
            # Pre-check the declared size so an oversized file isn't downloaded
            # just to be rejected.
            declared = int(getattr(entity, "size_bytes", 0) or 0)
            if declared and not data_budget.allow(name=f"files{path}", size=declared):
                continue
            try:
                _entity, content = await service.download_file_content_by_path(
                    pod_id, path, ctx
                )
            except Exception as exc:  # noqa: BLE001 - one bad file is not fatal
                warnings.append(f"file '{path}' skipped: {exc}")
                continue
            # When size wasn't known up front, budget the real bytes now.
            if not declared and not data_budget.allow(name=f"files{path}", size=len(content)):
                continue
            target = files_root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            await run_blocking(target.write_bytes, content, limiter="cpu_bound")
            file_manifest.append(
                {
                    "path": path,
                    "description": entity.description,
                    "visibility": entity.visibility,
                    "search_enabled": entity.search_enabled,
                }
            )
            wrote = True

        if file_manifest:
            _write_json(files_root / FILES_MANIFEST, {"files": file_manifest})
        return wrote

    async def _walk_pod_files(
        self, service: Any, pod_id: UUID, ctx: Context, dir_path: str = "/"
    ) -> list[Any]:
        """Depth-first list of every file/folder entity under ``dir_path``,
        paging each directory fully (the tree endpoint caps files-per-dir, so we
        walk with ``list_files`` instead)."""
        out: list[Any] = []
        cursor: str | None = None
        while True:
            items, cursor = await service.list_files(
                pod_id, ctx, directory_path=dir_path, limit=100, cursor=cursor
            )
            for item in items:
                out.append(item)
                if item.is_folder:
                    out.extend(
                        await self._walk_pod_files(
                            service, pod_id, ctx, dir_path=str(item.path or "")
                        )
                    )
            if not cursor:
                break
        return out


# --- response-dict adapters (per-module GET serialization) -------------------


async def _resource_grants_payload(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
) -> dict[str, Any] | None:
    """Serialize an agent's/function's resource grants into the bundle's portable
    ``{"grants": [...]}`` shape (keyed by ``resource_name``, never a source-org id).

    Mirrors the ``…/permissions`` GET endpoint the CLI exporter reads, so a pod
    exported through the async backend keeps the same executable grants a
    CLI-exported one does — without them, an imported agent/function can be created
    but can't call the tables/functions it was granted. ``list_grantee_resource_grants``
    already drops grants whose resource no longer resolves to a name, and the applier
    skips any that don't resolve in the target pod, so this stays best-effort/portable.
    Best-effort: a failure to read grants logs and returns ``None`` (the resource
    still exports, just without its grants) rather than sinking the whole export,
    matching the surfaces/files/apps best-effort policy. Returns ``None`` when the
    grantee has no grants (so nothing is attached)."""
    from app.core.authorization.grants import list_grantee_resource_grants

    try:
        grouped = await list_grantee_resource_grants(
            uow.session,
            pod_id=pod_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )
    except Exception as exc:  # noqa: BLE001 - grant export is best-effort
        logger.warning(
            "Skipping grant export for %s %s: %s", grantee_type, grantee_id, exc
        )
        return None
    if not grouped:
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
    from app.modules.pod.api.schemas.pod_schemas import PodResponse

    return _dump_response(PodResponse.model_validate(pod))


def _table_response_dict(table: Any) -> dict[str, Any]:
    from app.modules.datastore.api.schemas.datastore_schemas import TableResponse

    return _dump_response(TableResponse.model_validate(table))


def _function_response_dict(function: Any) -> dict[str, Any]:
    from app.modules.function.api.schemas.function_schemas import FunctionResponse

    return _dump_response(FunctionResponse.model_validate(function.model_dump()))


def _agent_response_dict(agent: Any) -> dict[str, Any]:
    from app.modules.agent.api.schemas import AgentResponse

    return _dump_response(AgentResponse.model_validate(agent))


def _flow_response_dict(flow: Any) -> dict[str, Any]:
    from app.modules.workflow.api.schemas import flow_response_from_domain

    return _dump_response(flow_response_from_domain(flow))


def _schedule_response_dict(schedule: Any) -> dict[str, Any]:
    from app.modules.schedule.api.schemas.schedule_schemas import ScheduleResponse

    return _dump_response(ScheduleResponse.model_validate(schedule))


def _app_response_dict(app: Any) -> dict[str, Any]:
    from app.modules.apps.api.schemas.app_schemas import AppDetailResponse

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
    (unless ``with_data`` seeds every table). Blank entries are dropped."""
    if not data_tables:
        return set()
    return {name.strip() for name in data_tables if name and name.strip()}


def _extract_large_text(
    payload: dict[str, Any],
    *,
    field_name: str,
    file_name: str,
    resource_dir: Path,
) -> dict[str, Any]:
    """Extract a large text field (``code``/``instruction``) to a sidecar file
    referenced by ``$file`` — byte-identical to the CLI's ``_extract_large_text``."""
    from lemma_pod_bundle.layout import RAW_FILE_REF_KEY

    value = payload.get(field_name)
    if not isinstance(value, str):
        return payload
    (resource_dir / file_name).write_text(value, encoding="utf-8")
    next_payload = dict(payload)
    next_payload[field_name] = {RAW_FILE_REF_KEY: file_name}
    return next_payload


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


def _csv_within_bytes(
    rows: list[dict[str, Any]], max_bytes: int
) -> tuple[str, int]:
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
