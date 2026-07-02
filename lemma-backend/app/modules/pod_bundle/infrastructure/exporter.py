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
from app.core.helpers.slug import slugify
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger

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

# Ceiling on rows dumped per table under ``with_data`` — matches the CLI's
# RECORD_EXPORT_DEFAULT_LIMIT so both exporters cap identically.
_RECORD_EXPORT_LIMIT = 10_000
_RECORD_EXPORT_PAGE = 1_000


ProgressCallback = Callable[[int, int], Awaitable[None]]


def _dump_response(response: Any) -> dict[str, Any]:
    """Render an API response model the way the GET controller serializes it for
    the SDK — the exact dict the normalizers were written against."""
    return response.model_dump(mode="json")


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
        ctx: Context,
        uow: SqlAlchemyUnitOfWork,
        on_progress: ProgressCallback,
    ) -> tuple[str, bytes]:
        """Assemble the bundle and return ``(bundle_filename, zip_bytes)``.

        All ``list`` + ``get`` reads run against the live ``uow``/``ctx``; the
        zip is built in a temp dir with no DB. ``on_progress(done, total)`` is
        awaited as each resource type completes so the job can refresh Redis.
        """
        selected = _normalize_include(include)

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
                record_service = build_record_service(uow) if with_data else None
                schema_name = table_service.schema_manager.get_schema_name(pod_id)
                tables, _ = await table_service.list_tables(pod_id, ctx, limit=1000)
                for summary in sorted(tables, key=lambda t: str(t.name or "")):
                    table_name = str(summary.name or "")
                    table = await table_service.get_table(pod_id, table_name, ctx)
                    dir_ = root / "tables" / table_name
                    dir_.mkdir(parents=True, exist_ok=True)
                    _write_json(
                        dir_ / f"{table_name}.json",
                        _normalize_table_payload(_table_response_dict(table)),
                    )
                    if with_data and record_service is not None:
                        await self._export_table_data(
                            record_service=record_service,
                            table_context=TableContext.from_table_entity(
                                table, schema_name, events_enabled=False
                            ),
                            user_id=user_id,
                            dest=dir_ / TABLE_DATA_FILE,
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
                    payload = _normalize_schedule_payload(
                        _schedule_response_dict(schedule)
                    )
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
                done += 1
                await on_progress(done, total)

            # --- portability + contents manifest (no DB) ----------------------
            _extract_portable_variables(root)
            _record_export_contents(
                root,
                included=selected if include else set(),
                excluded=set(),
                names=set(),
                with_data=with_data,
                with_files=False,
            )

            zip_bytes = pack_bundle(root)

        bundle_filename = f"{slugify(pod_name) or 'pod'}.zip"
        await on_progress(total, total)
        return bundle_filename, zip_bytes

    async def _export_table_data(
        self,
        *,
        record_service: Any,
        table_context: Any,
        user_id: UUID,
        dest: Path,
    ) -> None:
        """Page a table's rows (up to the export cap) and write ``data.csv`` the
        same way the CLI's record IO does — skipped when the table is empty."""
        from lemma_pod_bundle.normalize import _SEED_STRIP_COLUMNS

        rows: list[dict[str, Any]] = []
        offset = 0
        while len(rows) < _RECORD_EXPORT_LIMIT:
            want = min(_RECORD_EXPORT_PAGE, _RECORD_EXPORT_LIMIT - len(rows))
            items, _total = await record_service.list_records(
                table_context, user_id, limit=want, offset=offset
            )
            batch = [dict(item.data) for item in items]
            rows.extend(batch)
            offset += len(batch)
            if not batch or len(batch) < want:
                break
        if not rows:
            return
        # Drop audit/ownership columns so a re-import re-owns rows to the importer,
        # matching the CLI seed contract.
        cleaned = [
            {k: v for k, v in row.items() if k not in _SEED_STRIP_COLUMNS}
            for row in rows
        ]
        _write_export_csv(dest, cleaned)

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

        seen_platforms: set[str] = set()
        for surface in surfaces:
            try:
                payload = _normalize_surface_payload(
                    _dump_response(_surface_response(surface))
                )
                platform = str(payload.get("platform") or "")
                if not platform or platform in seen_platforms:
                    continue
                seen_platforms.add(platform)
                surface_name = str(payload["name"])
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


# --- response-dict adapters (per-module GET serialization) -------------------


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


def _write_export_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write records to CSV with the same cell semantics as the CLI's
    ``record_io.write_export_rows`` (complex cells -> JSON text, None -> empty)."""
    import csv
    import io
    import json

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

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(row.get(key)) for key in fieldnames})
    path.write_text(buffer.getvalue(), encoding="utf-8")
