from __future__ import annotations

import io
import json
import subprocess
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from zipfile import ZIP_DEFLATED, ZipFile

# The pure bundle-format vocabulary (constants, JSONC parsing, table diffing,
# portable variables, payload normalization) lives in the shared
# lemma-pod-bundle package so the backend can consume the same format. The
# names are re-bound here — including the underscore-private ones — so every
# existing `lemma_cli.cli_app.pod_bundle.<name>` reference keeps working.
from lemma_pod_bundle.diff import (
    TableDiff,
    _is_system_table_column,
    _normalize_column_for_diff,
    _order_table_dirs_by_dependency,
    _table_fk_dependencies,
    diff_table_columns,
)
from lemma_pod_bundle.jsonc import _strip_trailing_commas, loads_jsonc, strip_jsonc
from lemma_pod_bundle.layout import (
    APP_MANIFEST_ALIAS,
    EXPORTABLE_RESOURCE_DIRS,
    FILES_MANIFEST,
    FORMAT_VERSION,
    JSON_FILE_REF_KEY,
    POD_MEMBER_TOKEN,
    RAW_FILE_REF_KEY,
    RESOURCE_DIR_ALIASES,
    RESOURCE_DIRS,
    SYSTEM_TABLE_COLUMNS,
    TABLE_DATA_FILE,
    _TABLE_DATA_CANDIDATES,
    _bundle_folder_keys,
    _file_path_key,
    _json_dump,
    _looks_like_single_resource_dir,
    _parse_function_headers,
    _read_export_contents,
    _read_json,
    _record_export_contents,
    _resolve_file_refs,
    _resource_manifest_path,
    _sanitize_resource_name,
    _write_json,
    load_resource_payload,
    normalize_resource_dir_name,
)
from lemma_pod_bundle.normalize import (
    _AGENT_CLEARABLE_SCHEMA_FIELDS,
    _SEED_STRIP_COLUMNS,
    BundleValidationIssue,
    _attach_permissions_payload,
    _declared_reserved_columns,
    _normalize_agent_payload,
    _normalize_app_payload,
    _normalize_function_payload,
    _normalize_pod_payload,
    _normalize_resource_permissions_payload,
    _normalize_schedule_payload,
    _normalize_surface_payload,
    _normalize_table_payload,
    _normalize_workflow_payload,
    _sanitize_function_payload_for_import,
    _sanitize_table_payload_for_import,
    _split_resource_permissions_payload,
    _strip_keys,
    _surface_platform_from_payload,
    _validate_function_payload,
)
from lemma_pod_bundle.portability import (
    _ACCOUNT_REF_FIELDS,
    _MEMBER_REF_FIELDS,
    _PLACEHOLDER_RE,
    _extract_portable_variables,
    _placeholder,
    _slug_var_name,
    _strip_unresolved_placeholders,
    _tokenize_ref_fields,
)

from lemma_sdk import Lemma
from lemma_sdk.errors import LemmaAPIError
from lemma_sdk.openapi_client.models.add_column_request import AddColumnRequest
from lemma_sdk.openapi_client.models.agent_permissions_replace_request import (
    AgentPermissionsReplaceRequest,
)
from lemma_sdk.openapi_client.models.body_file_update import BodyFileUpdate
from lemma_sdk.openapi_client.models.create_agent_request import CreateAgentRequest
from lemma_sdk.openapi_client.models.create_app_request import CreateAppRequest
from lemma_sdk.openapi_client.models.create_function_request import CreateFunctionRequest
from lemma_sdk.openapi_client.models.create_schedule_request import CreateScheduleRequest
from lemma_sdk.openapi_client.models.create_table_request import CreateTableRequest
from lemma_sdk.openapi_client.models.function_permissions_replace_request import (
    FunctionPermissionsReplaceRequest,
)
from lemma_sdk.openapi_client.models.pod_update_request import PodUpdateRequest
from lemma_sdk.openapi_client.models.update_agent_request import UpdateAgentRequest
from lemma_sdk.openapi_client.models.update_app_request import UpdateAppRequest
from lemma_sdk.openapi_client.models.update_function_request import UpdateFunctionRequest
from lemma_sdk.openapi_client.models.update_schedule_request import UpdateScheduleRequest
from lemma_sdk.openapi_client.models.update_table_request import UpdateTableRequest
from lemma_sdk.openapi_client.models.workflow_create_request import WorkflowCreateRequest
from lemma_sdk.openapi_client.models.workflow_update_request import WorkflowUpdateRequest
from ..cli_core.io import list_items, to_plain
from ..cli_core.payload import build_request
from ..cli_core.state import console
from .app_bundle import deploy_app_bundle
from .enums import SURFACE_PLATFORMS
from .record_io import (
    RECORD_EXPORT_DEFAULT_LIMIT,
    fetch_records_capped,
    read_record_rows,
    write_export_rows,
)

def _progress_start(resource_type: str, resource_name: str, action: str) -> None:
    console.print(f"[cyan]{resource_type}[/cyan] {action} {resource_name}")


def _progress_done(resource_type: str, resource_name: str, action: str) -> None:
    console.print(f"[green]{resource_type}[/green] {action} {resource_name}")


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    stream_output: bool,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if stream_output:
        return subprocess.run(command, cwd=cwd, check=True, text=True, env=env)
    return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True, env=env)


def _detect_package_manager(source_dir: Path) -> str:
    if (source_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (source_dir / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _install_command_for_package_manager(source_dir: Path, package_manager: str) -> list[str]:
    if package_manager == "npm":
        if (source_dir / "package-lock.json").exists():
            return ["npm", "ci"]
        return ["npm", "install"]
    if package_manager == "pnpm":
        return ["pnpm", "install", "--frozen-lockfile"] if (source_dir / "pnpm-lock.yaml").exists() else ["pnpm", "install"]
    if package_manager == "yarn":
        return ["yarn", "install", "--frozen-lockfile"] if (source_dir / "yarn.lock").exists() else ["yarn", "install"]
    raise ValueError(f"Unsupported package manager: {package_manager}")


def _archive_dist_directory(dist_dir: Path, archive_path: Path) -> Path:
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in dist_dir.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=path.relative_to(dist_dir).as_posix())
    return archive_path


def _build_app_bundle(
    resource_dir: Path,
    *,
    stream_output: bool,
) -> Path:
    source_dir = resource_dir / "source"
    dist_file = resource_dir / "dist.zip"
    if not source_dir.exists():
        html_file = resource_dir / "html.html"
        if html_file.exists():
            # html.html is the source of truth for a no-build app, so always
            # (re)build dist.zip from it. Returning a pre-existing dist.zip here
            # would shadow edits to html.html: a prior import writes dist.zip into
            # this dir, so "import → edit html.html → re-import" otherwise
            # re-uploads the STALE dist and the served app never changes.
            with ZipFile(dist_file, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("index.html", html_file.read_text(encoding="utf-8"))
            return dist_file
        if dist_file.exists():
            return dist_file
        raise ValueError(f"App bundle is missing both source/ and dist.zip in {resource_dir}")

    package_json = source_dir / "package.json"
    if not package_json.exists():
        raise ValueError(f"App source is missing package.json: {package_json}")

    package_manager = _detect_package_manager(source_dir)
    install_command = _install_command_for_package_manager(source_dir, package_manager)

    console.print(f"[cyan]app[/cyan] building {resource_dir.name}: {' '.join(install_command)}")
    try:
        _run_command(install_command, cwd=source_dir, stream_output=stream_output)
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or exc.stdout or str(exc)
        raise ValueError(f"{' '.join(install_command)} failed for app {resource_dir.name}: {details}") from exc

    build_command = [package_manager, "run", "build"]
    console.print(f"[cyan]app[/cyan] building {resource_dir.name}: {' '.join(build_command)}")
    try:
        _run_command(build_command, cwd=source_dir, stream_output=stream_output)
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or exc.stdout or str(exc)
        raise ValueError(f"{' '.join(build_command)} failed for app {resource_dir.name}: {details}") from exc

    dist_dir = source_dir / "dist"
    if not (dist_dir / "index.html").exists():
        raise ValueError(f"App build did not produce dist/index.html for {resource_dir.name}")

    _archive_dist_directory(dist_dir, dist_file)
    console.print(f"[green]app[/green] built {resource_dir.name}: wrote dist.zip from source/dist/")
    return dist_file


def _ensure_clean_dir(path: Path, *, force: bool) -> None:
    if path.exists():
        if not force:
            raise ValueError(f"Output directory already exists: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _ensure_resource_dirs(root: Path) -> None:
    for resource_dir in RESOURCE_DIRS:
        (root / resource_dir).mkdir(parents=True, exist_ok=True)


def _prepare_agent_update_payload(
    payload: dict[str, Any],
    _existing: dict[str, Any] | None,
) -> dict[str, Any]:
    update = _strip_keys(payload, {"name"})
    for field in _AGENT_CLEARABLE_SCHEMA_FIELDS:
        update.setdefault(field, None)
    return update


def _export_table_data(pod_sdk: Any, table_name: str, resource_dir: Path) -> None:
    """Dump up to the export cap of a table's rows to ``data.csv`` (skipped when
    empty). Warns if the row count hit the cap, since extra rows are dropped."""
    rows = fetch_records_capped(pod_sdk, table_name, RECORD_EXPORT_DEFAULT_LIMIT)
    if not rows:
        return
    write_export_rows(resource_dir / TABLE_DATA_FILE, rows, "csv")
    if len(rows) >= RECORD_EXPORT_DEFAULT_LIMIT:
        console.print(
            f"[yellow]warning[/yellow] table '{table_name}': exported the first "
            f"{RECORD_EXPORT_DEFAULT_LIMIT} rows (cap); any beyond that were skipped."
        )


def _import_table_data(pod_sdk: Any, table_name: str, resource_dir: Path) -> int:
    """Seed a table from a bundled ``data.{csv,jsonl,json}`` file via bulk create.
    Returns the number of rows sent (0 when there is no data file)."""
    data_file = next(
        (resource_dir / name for name in _TABLE_DATA_CANDIDATES if (resource_dir / name).is_file()),
        None,
    )
    if data_file is None:
        return 0
    rows = [
        {key: value for key, value in row.items() if key not in _SEED_STRIP_COLUMNS}
        for row in read_record_rows(data_file, None)
    ]
    if not rows:
        return 0
    # Upsert so re-importing an edited data.csv updates existing rows (matched on
    # the table's primary key) instead of failing — idempotent re-seeding.
    pod_sdk.records.bulk_create(table_name, rows, upsert=True)
    return len(rows)


def _surface_upsert_body(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "account_id",
            "config",
            "credential_mode",
            "default_agent_name",
            "is_enabled",
        )
        if key in payload
    }


def _app_payload_with_unique_public_slug(
    payload: dict[str, Any],
    *,
    pod_id: str,
    app_name: str,
) -> dict[str, Any]:
    base_slug = str(payload.get("public_slug") or app_name).strip().strip("-")
    suffix = "".join(ch for ch in pod_id.lower() if ch.isalnum())[:8]
    unique_slug = f"{base_slug}-{suffix}" if base_slug else suffix
    next_payload = dict(payload)
    next_payload["public_slug"] = unique_slug
    return next_payload


def _build_variable_applier(
    client: Lemma,
    pod_sdk: Any,
    *,
    source_dir: Path,
    var_overrides: dict[str, str] | None,
    member_override: str | None,
):
    """Return ``apply(payload) -> payload`` that resolves the bundle's ``${name}``
    variables (and the legacy ``$POD_MEMBER`` token) and drops any that stay
    unresolved. ``pod_member`` variables default to the importing user; account
    variables must be supplied via ``--var``/``--values`` or are left unresolved.
    """
    from .scaffold import substitute_placeholders

    pod_path = source_dir / "pod.json"
    declared = (_read_json(pod_path).get("variables") or {}) if pod_path.is_file() else {}
    overrides = dict(var_overrides or {})
    unknown = sorted(set(overrides) - set(declared))
    if unknown:
        raise ValueError(
            f"Unknown --var name(s): {', '.join(unknown)}. "
            f"This bundle declares: {', '.join(sorted(declared)) or '(none)'}."
        )
    member_cache: list[str] = []

    def member_default() -> str:
        if not member_cache:
            member_cache.append(
                _resolve_import_pod_member_id(client, pod_sdk, member_override)
            )
        return member_cache[0]

    def apply(payload: dict[str, Any]) -> dict[str, Any]:
        serialized = json.dumps(payload)
        replacements: dict[str, str] = {}
        if POD_MEMBER_TOKEN in serialized:
            replacements[POD_MEMBER_TOKEN] = member_default()
        for name, spec in declared.items():
            token = _placeholder(name)
            if token not in serialized:
                continue
            if name in overrides:
                replacements[token] = overrides[name]
            elif str((spec or {}).get("type") or "") == "pod_member":
                replacements[token] = member_default()
        if replacements:
            payload = substitute_placeholders(payload, replacements)
        return _strip_unresolved_placeholders(payload)

    return apply


def _extract_large_text(
    payload: dict[str, Any],
    *,
    field_name: str,
    file_name: str,
    resource_dir: Path,
) -> dict[str, Any]:
    value = payload.get(field_name)
    if not isinstance(value, str):
        return payload
    (resource_dir / file_name).write_text(value, encoding="utf-8")
    next_payload = dict(payload)
    next_payload[field_name] = {RAW_FILE_REF_KEY: file_name}
    return next_payload


def _download_app_assets(client: Lemma, pod_id: str, app_name: str, resource_dir: Path) -> None:
    pod_sdk = client.pod(pod_id)
    try:
        archive_bytes = pod_sdk.apps.download_source_archive(app_name)
    except LemmaAPIError:
        archive_bytes = b""
    if archive_bytes:
        source_dir = resource_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        with ZipFile(io.BytesIO(archive_bytes)) as archive:
            for member in archive.infolist():
                target = source_dir / member.filename
                if not target.resolve().is_relative_to(source_dir.resolve()):
                    raise ValueError(f"Unsafe path in app source archive for {app_name}: {member.filename}")
            archive.extractall(source_dir)
        return

    try:
        dist_archive = pod_sdk.apps.download_dist_archive(app_name)
    except LemmaAPIError:
        dist_archive = b""
    if dist_archive:
        (resource_dir / "dist.zip").write_bytes(dist_archive)


def _is_pod_visible_file(item: dict[str, Any]) -> bool:
    return str(item.get("visibility") or "").upper() == "POD"


def fetch_files_index(
    client: Lemma, pod_id: str
) -> tuple[dict[str | None, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    tree_payload = to_plain(client.pod(pod_id).files.tree("/"))
    root = tree_payload.get("tree")
    if not isinstance(root, dict):
        return {}, {}

    by_parent: dict[str | None, list[dict[str, Any]]] = {None: []}
    all_items: dict[str, dict[str, Any]] = {}

    def walk(node: dict[str, Any], *, parent_key: str | None) -> None:
        for child in node.get("children") or []:
            if not isinstance(child, dict):
                continue
            child_path = str(child.get("path") or "")
            if not child_path or child_path == "/":
                continue
            item = dict(child)
            item_id = str(item.get("id") or child_path)
            item["id"] = item_id
            all_items[item_id] = item
            by_parent.setdefault(parent_key, []).append(item)
            walk(child, parent_key=child_path)

    walk(root, parent_key=None)
    return by_parent, all_items


def _export_pod_files(
    client: Lemma,
    pod_id: str,
    bundle_root: Path,
    *,
    with_files: bool = False,
) -> dict[str, int]:
    files_root = bundle_root / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    _, all_items = fetch_files_index(client, pod_id)

    pod_items = {
        item_id: item
        for item_id, item in all_items.items()
        if _is_pod_visible_file(item)
    }
    folder_count = 0
    file_count = 0
    file_manifest: list[dict[str, Any]] = []

    def export_folder(item: dict[str, Any]) -> None:
        nonlocal folder_count
        relative_parts = [part for part in str(item.get("path") or "").split("/") if part]
        if not relative_parts:
            return
        target_path = files_root.joinpath(*relative_parts)
        target_path.mkdir(parents=True, exist_ok=True)
        _write_json(
            target_path / ".folder.json",
            {
                "description": item.get("description"),
                "visibility": item.get("visibility"),
            },
        )
        folder_count += 1

    def export_file(item: dict[str, Any]) -> None:
        nonlocal file_count
        path = str(item.get("path") or "")
        relative_parts = [part for part in path.split("/") if part]
        if not relative_parts:
            return
        try:
            content = client.pod(pod_id).files.download(path)
        except Exception as exc:  # noqa: BLE001 — best-effort; warn and continue
            console.print(
                f"[yellow]warning[/yellow] file '{path}': could not download "
                f"({exc}); skipped."
            )
            return
        target_path = files_root.joinpath(*relative_parts)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)
        file_manifest.append(
            {
                "path": path,
                "description": item.get("description"),
                "visibility": item.get("visibility"),
                "search_enabled": item.get("search_enabled"),
            }
        )
        file_count += 1

    for _item_id, item in sorted(
        pod_items.items(),
        key=lambda pair: (
            str(pair[1].get("kind") or "").upper() != "FOLDER",
            str(pair[1].get("name") or "").lower(),
        ),
    ):
        if str(item.get("kind") or "").upper() == "FOLDER":
            export_folder(item)
        elif with_files:
            export_file(item)

    if with_files:
        _write_json(files_root / FILES_MANIFEST, {"files": file_manifest})

    return {"folders": folder_count, "files": file_count}


def export_pod_bundle(
    client: Lemma,
    *,
    pod_id: str,
    output_dir: Path,
    force: bool = False,
    exclude: set[str] | None = None,
    include: set[str] | None = None,
    names: set[str] | None = None,
    with_data: bool = False,
    with_files: bool = False,
) -> dict[str, Any]:
    excluded = set(exclude or set())
    unknown = sorted(excluded - EXPORTABLE_RESOURCE_DIRS)
    if unknown:
        raise ValueError(
            f"Unknown export exclude value(s): {', '.join(unknown)}. "
            f"Allowed values: {', '.join(sorted(EXPORTABLE_RESOURCE_DIRS))}"
        )
    included = set(include or set())
    unknown_include = sorted(included - EXPORTABLE_RESOURCE_DIRS)
    if unknown_include:
        raise ValueError(
            f"Unknown export include value(s): {', '.join(unknown_include)}. "
            f"Allowed values: {', '.join(sorted(EXPORTABLE_RESOURCE_DIRS))}"
        )
    if included & excluded:
        overlap = ", ".join(sorted(included & excluded))
        raise ValueError(f"Resources cannot be both included and excluded: {overlap}")
    selected_names = {name for name in (names or set()) if name}

    def should_export(resource_type: str) -> bool:
        return resource_type not in excluded and (
            not included or resource_type in included
        )

    def should_export_name(resource: dict[str, Any], fallback: str = "") -> bool:
        if not selected_names:
            return True
        resource_names = {
            str(resource.get("name") or ""),
            str(resource.get("id") or ""),
            fallback,
        }
        return bool(selected_names & resource_names)

    pod_sdk = client.pod(pod_id)
    pod = to_plain(client.pods.get(pod_id))
    pod_name = _sanitize_resource_name(str(pod.get("name") or pod_id))
    bundle_root = output_dir / pod_name
    _ensure_clean_dir(bundle_root, force=force)
    _ensure_resource_dirs(bundle_root)
    _write_json(bundle_root / "pod.json", _normalize_pod_payload(pod))

    tables: list[dict[str, Any]] = []
    if should_export("tables"):
        tables = [
            item
            for item in list_items(pod_sdk.tables.list(limit=1000))
            if should_export_name(item)
        ]
        for table in sorted(tables, key=lambda item: str(item.get("name", ""))):
            table_name = str(table.get("name") or "")
            resource_dir = bundle_root / "tables" / table_name
            resource_dir.mkdir(parents=True, exist_ok=True)
            full_table = to_plain(pod_sdk.tables.get(table_name))
            _write_json(resource_dir / f"{table_name}.json", _normalize_table_payload(full_table))
            if with_data:
                _export_table_data(pod_sdk, table_name, resource_dir)

    functions: list[dict[str, Any]] = []
    if should_export("functions"):
        functions = [
            item
            for item in list_items(pod_sdk.functions.list(limit=1000))
            if should_export_name(item)
        ]
        for function in sorted(functions, key=lambda item: str(item.get("name", ""))):
            function_name = str(function.get("name") or "")
            resource_dir = bundle_root / "functions" / function_name
            resource_dir.mkdir(parents=True, exist_ok=True)
            full_function = to_plain(pod_sdk.functions.get(function_name))
            function_permissions = to_plain(pod_sdk.functions.permissions(function_name))
            function_payload = _extract_large_text(
                _attach_permissions_payload(
                    _normalize_function_payload(full_function),
                    function_permissions,
                ),
                field_name="code",
                file_name="code.py",
                resource_dir=resource_dir,
            )
            _write_json(resource_dir / f"{function_name}.json", function_payload)

    agents: list[dict[str, Any]] = []
    if should_export("agents"):
        agents = [
            item
            for item in list_items(pod_sdk.agents.list(limit=1000))
            if should_export_name(item)
        ]
        for agent in sorted(agents, key=lambda item: str(item.get("name", ""))):
            agent_name = str(agent.get("name") or "")
            resource_dir = bundle_root / "agents" / agent_name
            resource_dir.mkdir(parents=True, exist_ok=True)
            full_agent = to_plain(pod_sdk.agents.get(agent_name))
            agent_permissions = to_plain(pod_sdk.agents.permissions(agent_name))
            agent_payload = _extract_large_text(
                _attach_permissions_payload(
                    _normalize_agent_payload(full_agent),
                    agent_permissions,
                ),
                field_name="instruction",
                file_name="instruction.md",
                resource_dir=resource_dir,
            )
            _write_json(resource_dir / f"{agent_name}.json", agent_payload)

    workflows: list[dict[str, Any]] = []
    if should_export("workflows"):
        workflows = list_items(pod_sdk.workflows.list(limit=1000))
        workflows = [item for item in workflows if should_export_name(item)]
        for workflow in sorted(workflows, key=lambda item: str(item.get("name", ""))):
            workflow_name = str(workflow.get("name") or "")
            resource_dir = bundle_root / "workflows" / workflow_name
            resource_dir.mkdir(parents=True, exist_ok=True)
            full_workflow = to_plain(pod_sdk.workflows.get(workflow_name))
            _write_json(
                resource_dir / f"{workflow_name}.json",
                _normalize_workflow_payload(full_workflow),
            )

    schedules: list[dict[str, Any]] = []
    if should_export("schedules"):
        schedules = [
            item
            for item in list_items(pod_sdk.schedules.list(limit=1000))
            if should_export_name(item)
        ]
        for schedule in sorted(
            schedules,
            key=lambda item: str(item.get("name") or item.get("id") or ""),
        ):
            schedule_id = str(schedule.get("id") or "")
            schedule_name = str(schedule.get("name") or schedule_id)
            resource_dir = bundle_root / "schedules" / schedule_name
            resource_dir.mkdir(parents=True, exist_ok=True)
            full_schedule = (
                to_plain(pod_sdk.schedules.get(schedule_id))
                if schedule_id
                else schedule
            )
            _write_json(
                resource_dir / f"{schedule_name}.json",
                _normalize_schedule_payload(full_schedule),
            )

    surfaces: list[dict[str, Any]] = []
    if should_export("surfaces"):
        seen_platforms: set[str] = set()
        for surface in list_items(pod_sdk.surfaces.list(limit=100)):
            payload = _normalize_surface_payload(to_plain(surface))
            platform = str(payload.get("platform") or "")
            if not platform or platform in seen_platforms:
                continue
            if not should_export_name(surface, payload["name"]):
                continue
            seen_platforms.add(platform)
            surfaces.append(payload)
            surface_name = str(payload["name"])
            resource_dir = bundle_root / "surfaces" / surface_name
            resource_dir.mkdir(parents=True, exist_ok=True)
            _write_json(resource_dir / f"{surface_name}.json", payload)

    apps: list[dict[str, Any]] = []
    if should_export("apps"):
        apps = [
            item
            for item in list_items(pod_sdk.apps.list(limit=1000))
            if should_export_name(item)
        ]
        for app in sorted(apps, key=lambda item: str(item.get("name", ""))):
            app_name = str(app.get("name") or "")
            resource_dir = bundle_root / "apps" / app_name
            resource_dir.mkdir(parents=True, exist_ok=True)
            full_app = to_plain(pod_sdk.apps.get(app_name))
            _write_json(resource_dir / f"{app_name}.json", _normalize_app_payload(full_app))
            _download_app_assets(client, pod_id, app_name, resource_dir)

    file_counts = {"folders": 0, "files": 0}
    if should_export("files"):
        file_counts = _export_pod_files(
            client, pod_id, bundle_root, with_files=with_files
        )

    # Replace non-portable member/account ids with ${name} variables recorded in
    # pod.json, so the bundle can be re-imported into another pod/org.
    variables = _extract_portable_variables(bundle_root)

    # Record what this bundle carries (selective scope + whether row data / file
    # bytes were captured) so a re-import seeds them automatically and a re-export
    # can refresh exactly this set.
    _record_export_contents(
        bundle_root,
        included=included,
        excluded=excluded,
        names=selected_names,
        with_data=with_data,
        with_files=with_files,
    )

    return {
        "ok": True,
        "path": str(bundle_root),
        "pod_id": pod_id,
        "pod_name": pod_name,
        "excluded": sorted(excluded),
        "included": sorted(included),
        "names": sorted(selected_names),
        "variables": sorted(variables.keys()),
        "counts": {
            "tables": len(tables),
            "functions": len(functions),
            "agents": len(agents),
            "workflows": len(workflows),
            "schedules": len(schedules),
            "surfaces": len(surfaces),
            "apps": len(apps),
            "folders": file_counts["folders"],
            "files": file_counts.get("files", 0),
            "variables": len(variables),
        },
    }


def _resource_dirs(root: Path, resource_type: str) -> list[Path]:
    base = root / resource_type
    if not base.exists():
        return []
    # Keep directories that contain their expected manifest JSON. An author may
    # delete a starter resource's JSON but leave the empty directory behind
    # (e.g. `tables/items/` with no `items.json`); those empty leftovers are not
    # resources and are skipped silently. A *non-empty* dir with no recognizable
    # manifest, though, is usually a misnamed file (e.g. `items/item.json`) that
    # would otherwise vanish from the plan — warn loudly instead.
    kept: list[Path] = []
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        if _resource_manifest_path(path, path.name, resource_type=resource_type) is not None:
            kept.append(path)
        elif any(path.iterdir()):
            console.print(
                f"[yellow]{resource_type}[/yellow] skipping '{path.name}': no "
                f"'{path.name}.json' manifest found (misnamed file?)"
            )
    return kept


@contextmanager
def _prepared_import_source(source_dir: Path) -> Iterator[Path]:
    if (source_dir / "pod.json").exists():
        yield source_dir
        return

    resource_type = normalize_resource_dir_name(source_dir.name)
    if resource_type:
        with tempfile.TemporaryDirectory(prefix="lemma-import-") as tmp:
            root = Path(tmp)
            target = root / resource_type
            shutil.copytree(source_dir, target)
            yield root
        return

    parent_resource_type = normalize_resource_dir_name(source_dir.parent.name)
    if parent_resource_type and _looks_like_single_resource_dir(
        source_dir, parent_resource_type
    ):
        with tempfile.TemporaryDirectory(prefix="lemma-import-") as tmp:
            root = Path(tmp)
            target = root / parent_resource_type / source_dir.name
            shutil.copytree(source_dir, target)
            yield root
        return

    yield source_dir


def _build_existing_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("name")): item
        for item in items
        if isinstance(item, dict) and item.get("name")
    }


def _build_existing_schedule_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in (item.get("name"), item.get("id")):
            if key:
                mapping[str(key)] = item
    return mapping


def _list_pod_visible_items(client: Lemma, pod_id: str) -> list[dict[str, Any]]:
    _, all_items = fetch_files_index(client, pod_id)
    return [item for item in all_items.values() if _is_pod_visible_file(item)]


def _build_import_plan(
    client: Lemma,
    *,
    pod_id: str,
    source_dir: Path,
    upsert: bool,
) -> tuple[dict[str, list[str]], list[BundleValidationIssue]]:
    summary: dict[str, list[str]] = {key: [] for key in RESOURCE_DIRS}
    issues: list[BundleValidationIssue] = []
    pod_sdk = client.pod(pod_id)

    for resource_type in (
        "tables",
        "functions",
        "agents",
        "workflows",
        "schedules",
        "surfaces",
        "apps",
    ):
        for resource_dir in _resource_dirs(source_dir, resource_type):
            resource_name = resource_dir.name
            try:
                payload = load_resource_payload(
                    resource_dir, resource_name, resource_type=resource_type
                )
            except Exception as exc:
                issues.append(BundleValidationIssue(path=str(resource_dir), message=str(exc)))
                continue

            payload_name = payload.get("name")
            if payload_name and str(payload_name) != resource_name:
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir / f"{resource_name}.json"),
                        message=f"Resource name '{payload_name}' does not match folder name '{resource_name}'.",
                    )
                )

            if resource_type == "tables":
                for column_name in _declared_reserved_columns(payload):
                    issues.append(
                        BundleValidationIssue(
                            path=str(resource_dir / f"{resource_name}.json"),
                            message=(
                                f"Column '{column_name}' is system-managed and must not "
                                "be declared explicitly — Lemma adds it automatically. "
                                "Remove it from the table's columns."
                            ),
                        )
                    )
            if resource_type == "functions":
                issues.extend(_validate_function_payload(resource_dir, resource_name, payload))
            if resource_type == "schedules" and not payload.get("name"):
                payload["name"] = resource_name
            if resource_type == "surfaces":
                platform = _surface_platform_from_payload(payload, resource_name)
                if platform not in SURFACE_PLATFORMS:
                    issues.append(
                        BundleValidationIssue(
                            path=str(resource_dir / f"{resource_name}.json"),
                            message=(
                                f"Unknown surface platform '{platform}'. "
                                f"Allowed values: {', '.join(SURFACE_PLATFORMS)}"
                            ),
                        )
                    )

    existing_tables = _build_existing_map(list_items(pod_sdk.tables.list(limit=1000)))
    for resource_dir in _resource_dirs(source_dir, "tables"):
        table_name = resource_dir.name
        if table_name in existing_tables:
            if upsert:
                summary["tables"].append(f"updated:{table_name}")
            else:
                issues.append(BundleValidationIssue(path=str(resource_dir), message=f"Table already exists: {table_name}"))
        else:
            summary["tables"].append(f"created:{table_name}")

    existing_functions = _build_existing_map(list_items(pod_sdk.functions.list(limit=1000)))
    for resource_dir in _resource_dirs(source_dir, "functions"):
        function_name = resource_dir.name
        if function_name in existing_functions:
            if upsert:
                summary["functions"].append(f"updated:{function_name}")
            else:
                issues.append(BundleValidationIssue(path=str(resource_dir), message=f"Function already exists: {function_name}"))
        else:
            summary["functions"].append(f"created:{function_name}")

    existing_agents = _build_existing_map(list_items(pod_sdk.agents.list(limit=1000)))
    for resource_dir in _resource_dirs(source_dir, "agents"):
        agent_name = resource_dir.name
        if agent_name in existing_agents:
            if upsert:
                summary["agents"].append(f"updated:{agent_name}")
            else:
                issues.append(BundleValidationIssue(path=str(resource_dir), message=f"Agent already exists: {agent_name}"))
        else:
            summary["agents"].append(f"created:{agent_name}")

    workflow_dirs = _resource_dirs(source_dir, "workflows")
    existing_workflows = (
        _build_existing_map(list_items(pod_sdk.workflows.list(limit=1000)))
        if workflow_dirs
        else {}
    )
    for resource_dir in workflow_dirs:
        workflow_name = resource_dir.name
        if workflow_name in existing_workflows:
            if upsert:
                summary["workflows"].append(f"updated:{workflow_name}")
            else:
                issues.append(BundleValidationIssue(path=str(resource_dir), message=f"Workflow already exists: {workflow_name}"))
        else:
            summary["workflows"].append(f"created:{workflow_name}")

    schedule_dirs = _resource_dirs(source_dir, "schedules")
    existing_schedules = (
        _build_existing_schedule_map(list_items(pod_sdk.schedules.list(limit=1000)))
        if schedule_dirs
        else {}
    )
    for resource_dir in schedule_dirs:
        schedule_name = resource_dir.name
        if schedule_name in existing_schedules:
            if upsert:
                summary["schedules"].append(f"updated:{schedule_name}")
            else:
                issues.append(BundleValidationIssue(path=str(resource_dir), message=f"Schedule already exists: {schedule_name}"))
        else:
            summary["schedules"].append(f"created:{schedule_name}")

    surface_dirs = _resource_dirs(source_dir, "surfaces")
    existing_surface_platforms = (
        {
            str(item.get("platform") or item.get("surface_type") or "").upper()
            for item in list_items(pod_sdk.surfaces.list(limit=100))
        }
        if surface_dirs
        else set()
    )
    for resource_dir in surface_dirs:
        surface_name = resource_dir.name
        try:
            payload = load_resource_payload(resource_dir, surface_name)
        except Exception:
            continue
        platform = _surface_platform_from_payload(payload, surface_name)
        if platform in existing_surface_platforms:
            if upsert:
                summary["surfaces"].append(f"updated:{surface_name}")
            else:
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir),
                        message=f"Surface already exists for platform: {platform}",
                    )
                )
        else:
            summary["surfaces"].append(f"created:{surface_name}")

    app_dirs = _resource_dirs(source_dir, "apps")
    existing_apps = (
        _build_existing_map(list_items(pod_sdk.apps.list(limit=1000)))
        if app_dirs
        else {}
    )
    for resource_dir in app_dirs:
        app_name = resource_dir.name
        try:
            if (resource_dir / "source").exists():
                _build_app_bundle(
                    resource_dir,
                    stream_output=False,
                )
        except ValueError as exc:
            issues.append(BundleValidationIssue(path=str(resource_dir), message=str(exc)))
            continue
        if app_name in existing_apps:
            if upsert:
                summary["apps"].append(f"updated:{app_name}")
            else:
                issues.append(BundleValidationIssue(path=str(resource_dir), message=f"App already exists: {app_name}"))
        else:
            summary["apps"].append(f"created:{app_name}")

    files_root = source_dir / "files"
    existing_folder_map = _build_existing_folder_map(_list_pod_visible_items(client, pod_id))
    if files_root.exists():
        for folder_dir in sorted([path for path in files_root.rglob("*") if path.is_dir()], key=lambda path: len(path.relative_to(files_root).parts)):
            parts = list(folder_dir.relative_to(files_root).parts)
            if not parts:
                continue
            path_key = _file_path_key(parts)
            if path_key not in existing_folder_map:
                summary["files"].append(f"created-folder:{path_key}")

    _validate_grant_references(
        source_dir,
        issues,
        valid_tables=set(existing_tables)
        | {d.name for d in _resource_dirs(source_dir, "tables")},
        valid_functions=set(existing_functions)
        | {d.name for d in _resource_dirs(source_dir, "functions")},
        valid_agents=set(existing_agents)
        | {d.name for d in _resource_dirs(source_dir, "agents")},
        valid_folder_keys=set(existing_folder_map)
        | _bundle_folder_keys(files_root),
    )

    return summary, issues


def _validate_grant_references(
    source_dir: Path,
    issues: list[BundleValidationIssue],
    *,
    valid_tables: set[str],
    valid_functions: set[str],
    valid_agents: set[str],
    valid_folder_keys: set[str],
) -> None:
    """Fail the import plan up front if any agent/function grant references a
    resource that neither the bundle creates nor the pod already has — so a
    dangling grant never leaves a half-imported pod (grants apply last).
    Connector-connector grants are environment-specific and skipped here."""
    targets_by_type: dict[str, set[str]] = {
        "datastore_table": valid_tables,
        "function": valid_functions,
        "agent": valid_agents,
    }
    for kind in ("agents", "functions"):
        for resource_dir in _resource_dirs(source_dir, kind):
            try:
                _, permissions = _split_resource_permissions_payload(
                    load_resource_payload(resource_dir, resource_dir.name, resource_type=kind)
                )
            except Exception:
                continue  # payload errors are already reported elsewhere
            for grant in (permissions or {}).get("grants", []):
                if not isinstance(grant, dict):
                    continue
                rtype = str(grant.get("resource_type") or "")
                rname = str(grant.get("resource_name") or "")
                if not rname:
                    continue
                if rtype in ("folder", "document"):
                    found = _file_path_key(
                        [part for part in rname.split("/") if part]
                    ) in valid_folder_keys
                elif rtype in targets_by_type:
                    found = rname in targets_by_type[rtype]
                else:
                    continue  # e.g. connector — not validatable locally
                if not found:
                    issues.append(
                        BundleValidationIssue(
                            path=str(resource_dir / f"{resource_dir.name}.json"),
                            message=(
                                f"Grant references unknown {rtype} '{rname}' — not "
                                "created by this bundle or present in the pod. Add the "
                                "resource (e.g. export it --with-files) or drop the grant."
                            ),
                        )
                    )


def _build_existing_folder_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    folders_by_path: dict[str, dict[str, Any]] = {}

    for item in items:
        parts = [part for part in str(item.get("path") or "").split("/") if part]
        if not parts:
            continue
        key = _file_path_key(parts)
        if str(item.get("kind") or "").upper() == "FOLDER":
            folders_by_path[key] = item
    return folders_by_path


def _import_pod_files(
    client: Lemma,
    pod_id: str,
    source_dir: Path,
    *,
    with_files: bool = False,
) -> list[str]:
    files_root = source_dir / "files"
    if not files_root.exists():
        return []

    existing_items = _list_pod_visible_items(client, pod_id)
    pod_sdk = client.pod(pod_id)
    folders_by_path = _build_existing_folder_map(existing_items)
    folder_summaries: list[str] = []

    created_folder_paths: set[str] = set()

    def desired_folder_metadata(folder_dir: Path) -> dict[str, Any]:
        folder_meta_path = folder_dir / ".folder.json"
        folder_meta = _read_json(folder_meta_path) if folder_meta_path.exists() else {}
        return {
            "description": folder_meta.get("description"),
            "visibility": folder_meta.get("visibility") or "POD",
        }

    def sync_existing_folder(path_key: str, folder_dir: Path, existing: dict[str, Any]) -> None:
        desired = desired_folder_metadata(folder_dir)
        update_args: dict[str, Any] = {}
        if existing.get("description") != desired["description"]:
            update_args["description"] = desired["description"]
        if str(existing.get("visibility") or "").upper() != str(desired["visibility"]).upper():
            update_args["visibility"] = desired["visibility"]
        if not update_args:
            return

        _progress_start("file", path_key, "updating folder")
        updated = to_plain(pod_sdk.files.update(
            "/" + path_key,
            BodyFileUpdate.from_dict({"path": "/" + path_key, **update_args}),
        ))
        folders_by_path[path_key] = updated
        folder_summaries.append(f"updated-folder:{path_key}")
        _progress_done("file", path_key, "updated folder")

    def ensure_folder(parts: list[str], folder_dir: Path) -> str:
        path_key = _file_path_key(parts)
        if path_key in created_folder_paths:
            return path_key
        existing = folders_by_path.get(path_key)
        if existing is not None:
            sync_existing_folder(path_key, folder_dir, existing)
            created_folder_paths.add(path_key)
            return path_key
        else:
            parent_parts = parts[:-1]
            if parent_parts:
                ensure_folder(parent_parts, files_root.joinpath(*parent_parts))
            folder_meta = desired_folder_metadata(folder_dir)
            _progress_start("file", path_key, "creating folder")
            try:
                created = to_plain(pod_sdk.files.create_folder(
                    path="/" + path_key,
                    description=folder_meta.get("description"),
                    visibility=folder_meta.get("visibility"),
                ))
                folders_by_path[path_key] = created
                folder_summaries.append(f"created-folder:{path_key}")
                _progress_done("file", path_key, "created folder")
            except LemmaAPIError as exc:
                if exc.code != "DATASTORE_CONFLICT":
                    raise
                existing = to_plain(pod_sdk.files.get("/" + path_key))
                folders_by_path[path_key] = existing
                sync_existing_folder(path_key, folder_dir, existing)
        created_folder_paths.add(path_key)
        return path_key

    folder_dirs = sorted(
        [path for path in files_root.rglob("*") if path.is_dir()],
        key=lambda path: len(path.relative_to(files_root).parts),
    )
    for folder_dir in folder_dirs:
        parts = list(folder_dir.relative_to(files_root).parts)
        if parts:
            ensure_folder(parts, folder_dir)

    if with_files:
        folder_summaries.extend(
            _upload_bundled_files(pod_sdk, files_root)
        )
    return folder_summaries


def _upload_bundled_files(pod_sdk: Any, files_root: Path) -> list[str]:
    """Upload the file bytes captured by ``--with-files`` (described in the
    ``.files.json`` manifest) back into the pod, preserving each file's
    description, visibility, and search flag. A path that already exists is
    replaced with the bundled content (delete + re-upload) so re-importing an
    edited bundle refreshes the files."""
    manifest_path = files_root / FILES_MANIFEST
    if not manifest_path.exists():
        return []
    manifest = _read_json(manifest_path)
    summaries: list[str] = []
    for entry in manifest.get("files") or []:
        path = str(entry.get("path") or "")
        relative_parts = [part for part in path.split("/") if part]
        if not relative_parts:
            continue
        local_file = files_root.joinpath(*relative_parts)
        if not local_file.is_file():
            continue
        directory_path = "/" + "/".join(relative_parts[:-1])
        name = relative_parts[-1]
        path_key = "/".join(relative_parts)
        full_path = "/" + path_key

        def _do_upload() -> None:
            pod_sdk.files.upload(
                local_file,
                directory_path=directory_path,
                name=name,
                description=entry.get("description"),
                search_enabled=bool(entry.get("search_enabled", True)),
                visibility=entry.get("visibility"),
            )

        _progress_start("file", path_key, "uploading")
        try:
            _do_upload()
            summaries.append(f"uploaded-file:{path_key}")
            _progress_done("file", path_key, "uploaded")
        except LemmaAPIError as exc:
            if exc.code != "DATASTORE_CONFLICT":
                raise
            # Replace the existing file with the bundled content.
            pod_sdk.files.delete(full_path)
            _do_upload()
            summaries.append(f"replaced-file:{path_key}")
            _progress_done("file", path_key, "replaced")
    return summaries


def _create_or_update_app(
    client: Lemma,
    *,
    pod_id: str,
    app_name: str,
    payload: dict[str, Any],
    app_exists: bool,
) -> str:
    pod_sdk = client.pod(pod_id)
    if app_exists:
        pod_sdk.apps.update(
            app_name,
            build_request(
                UpdateAppRequest, _strip_keys(payload, {"name"}), context=f"app {app_name}"
            ),
        )
        return "updated"

    try:
        pod_sdk.apps.create(build_request(CreateAppRequest, payload, context=f"app {app_name}"))
        return "created"
    except LemmaAPIError as exc:
        if exc.code != "APP_CONFLICT":
            raise
        console.print(
            f"[yellow]app[/yellow] public slug conflict for {app_name}; retrying with a pod-specific public_slug"
        )
        pod_sdk.apps.create(
            build_request(
                CreateAppRequest,
                _app_payload_with_unique_public_slug(
                    payload,
                    pod_id=pod_id,
                    app_name=app_name,
                ),
                context=f"app {app_name}",
            )
        )
        return "created"


def _update_app_with_conflict_retry(
    client: Lemma,
    *,
    pod_id: str,
    app_name: str,
    payload: dict[str, Any],
) -> None:
    pod_sdk = client.pod(pod_id)
    update_payload = _strip_keys(payload, {"name"})
    try:
        pod_sdk.apps.update(
            app_name, build_request(UpdateAppRequest, update_payload, context=f"app {app_name}")
        )
    except LemmaAPIError as exc:
        if exc.code != "APP_CONFLICT":
            raise
        console.print(
            f"[yellow]app[/yellow] public slug conflict for {app_name}; retrying update with a pod-specific public_slug"
        )
        pod_sdk.apps.update(
            app_name,
            build_request(
                UpdateAppRequest,
                _app_payload_with_unique_public_slug(
                    update_payload,
                    pod_id=pod_id,
                    app_name=app_name,
                ),
                context=f"app {app_name}",
            ),
        )


def _schedule_create_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "name",
            "schedule_type",
            "config",
            "agent_name",
            "workflow_name",
            "account_id",
            "connector_trigger_id",
            "filter_instruction",
            "filter_output_schema",
        )
        if key in payload and payload[key] is not None
    }


def _schedule_update_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "name",
            "config",
            "agent_name",
            "workflow_name",
            "filter_instruction",
            "filter_output_schema",
            "is_active",
            "visibility",
        )
        if key in payload
    }


def _create_schedule_from_payload(
    client: Lemma,
    *,
    pod_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    pod_sdk = client.pod(pod_id)
    create_fields = _schedule_create_fields(payload)
    if not create_fields.get("name"):
        raise ValueError("Schedule import requires name.")
    if not create_fields.get("schedule_type") or not create_fields.get("config"):
        raise ValueError("Schedule import requires schedule_type and config.")
    created = to_plain(
        pod_sdk.schedules.create(
            build_request(
                CreateScheduleRequest,
                create_fields,
                context=f"schedule {create_fields.get('name')}",
            )
        )
    )
    if "is_active" in payload and created.get("id"):
        desired_active = bool(payload["is_active"])
        if bool(created.get("is_active", True)) != desired_active:
            pod_sdk.schedules.update(
                str(created["id"]),
                UpdateScheduleRequest.from_dict({"is_active": desired_active}),
            )
            created = {**created, "is_active": desired_active}
    return created


def _resolve_import_pod_member_id(client: Lemma, pod_sdk: Any, override: str | None) -> str:
    """Concrete pod-member id that ``$POD_MEMBER`` tokens in imported workflows
    resolve to: an explicit ``--pod-member`` override, else the importing user's
    own membership in this pod. Raises (listing the pod's members) when neither
    resolves, so a templated approval never imports with a bogus assignee."""
    if override:
        return override
    members = list_items(pod_sdk.members.list(limit=1000))
    user_id = ""
    try:
        user_id = str(getattr(client.user.profile(), "id", "") or "")
    except Exception:  # pragma: no cover - profile lookup is best-effort
        user_id = ""
    if user_id:
        for member in members:
            if str(member.get("user_id") or "") == user_id:
                return str(member["pod_member_id"])
    available = ", ".join(
        f"{member.get('email') or member.get('user_email') or '?'}={member.get('pod_member_id')}"
        for member in members
    )
    raise ValueError(
        "This bundle assigns a workflow approval to a pod member ($POD_MEMBER), but "
        "the importing user is not a member of this pod. Pass --pod-member <id> to "
        f"choose the assignee. Members: {available or '(none)'}"
    )


def import_pod_bundle(
    client: Lemma,
    *,
    pod_id: str,
    source_dir: Path,
    upsert: bool = True,
    dry_run: bool = False,
    pod_member_id: str | None = None,
    with_data: bool = False,
    with_files: bool = False,
    variables: dict[str, str] | None = None,
    set_pod_meta: bool = False,
) -> dict[str, Any]:
    if not source_dir.exists():
        raise ValueError(f"Source directory does not exist: {source_dir}")
    pod_sdk = client.pod(pod_id)

    with _prepared_import_source(source_dir) as prepared_source_dir:
        if prepared_source_dir != source_dir:
            result = import_pod_bundle(
                client,
                pod_id=pod_id,
                source_dir=prepared_source_dir,
                upsert=upsert,
                dry_run=dry_run,
                pod_member_id=pod_member_id,
                with_data=with_data,
                with_files=with_files,
                variables=variables,
                set_pod_meta=set_pod_meta,
            )
            result["source_dir"] = str(source_dir)
            return result

    # A manifest-aware bundle records whether it carries table rows / file bytes;
    # honor that so re-importing it seeds them without re-passing the flags.
    manifest_contents = _read_export_contents(source_dir)
    with_data = with_data or bool(manifest_contents.get("with_data"))
    with_files = with_files or bool(manifest_contents.get("with_files"))

    summary, issues = _build_import_plan(
        client,
        pod_id=pod_id,
        source_dir=source_dir,
        upsert=upsert,
    )
    if dry_run:
        return {
            "ok": len(issues) == 0,
            "dry_run": True,
            "pod_id": pod_id,
            "source_dir": str(source_dir),
            "summary": summary,
            "errors": [{"path": issue.path, "message": issue.message} for issue in issues],
        }
    if issues:
        rendered = "\n".join(f"- {issue.path}: {issue.message}" for issue in issues)
        raise ValueError(f"Bundle validation failed:\n{rendered}")

    # By default an import leaves the target pod's own name/description/icon
    # alone — importing resources into an existing pod should never silently
    # rename it. Opt in with set_pod_meta to push the bundle's pod metadata.
    pod_manifest_path = source_dir / "pod.json"
    if set_pod_meta and pod_manifest_path.exists():
        pod_manifest = _read_json(pod_manifest_path)
        pod_update_payload = {
            key: pod_manifest[key]
            for key in ("name", "description", "icon_url")
            if key in pod_manifest
        }
        if pod_update_payload:
            _progress_start("pod", pod_id, "updating metadata")
            client.pods.update(pod_id, PodUpdateRequest.from_dict(pod_update_payload))
            _progress_done("pod", pod_id, "updated metadata")

    summary = {key: [] for key in RESOURCE_DIRS}

    existing_tables = _build_existing_map(list_items(pod_sdk.tables.list(limit=1000)))
    for resource_dir in _order_table_dirs_by_dependency(
        _resource_dirs(source_dir, "tables")
    ):
        table_name = resource_dir.name
        raw_payload = load_resource_payload(resource_dir, table_name)
        declared_reserved = _declared_reserved_columns(raw_payload)
        if declared_reserved:
            raise ValueError(
                f"Table {table_name} declares system-managed column(s): "
                f"{', '.join(declared_reserved)}. Lemma adds these automatically — "
                "remove them from the table's columns."
            )
        payload = _sanitize_table_payload_for_import(raw_payload)
        existing = existing_tables.get(table_name)
        if existing is None:
            _progress_start("table", table_name, "creating")
            pod_sdk.tables.create(
                build_request(
                    CreateTableRequest,
                    {
                        **payload,
                        "name": str(payload.get("name") or table_name),
                        "columns": payload.get("columns") or [],
                    },
                    context=f"table {table_name}",
                )
            )
            summary["tables"].append(f"created:{table_name}")
            if with_data:
                seeded = _import_table_data(pod_sdk, table_name, resource_dir)
                if seeded:
                    summary["tables"].append(f"data:{table_name}:{seeded}")
            _progress_done("table", table_name, "created")
            continue
        if not upsert:
            raise ValueError(f"Table already exists and --no-upsert was requested: {table_name}")

        _progress_start("table", table_name, "updating")
        full_existing = to_plain(pod_sdk.tables.get(table_name))
        update_fields: dict[str, Any] = {"config": payload.get("config") or {}}
        if payload.get("visibility") is not None:
            update_fields["visibility"] = payload["visibility"]
        # Only send enable_rls when it actually changes — the backend rejects a
        # toggle on a non-empty table, so a no-op flip would surface a spurious
        # error on re-import of a populated table whose RLS already matches.
        desired_rls = payload.get("enable_rls")
        if desired_rls is not None and bool(desired_rls) != bool(
            full_existing.get("enable_rls")
        ):
            update_fields["enable_rls"] = bool(desired_rls)
        pod_sdk.tables.update(
            table_name, UpdateTableRequest.from_dict(update_fields)
        )
        diff = diff_table_columns(full_existing, payload)
        if diff.incompatible:
            names = ", ".join(diff.incompatible)
            raise ValueError(
                f"Table {table_name} has incompatible column changes for: {names}. "
                "Current CLI import supports add/remove columns and config updates, but not in-place column mutations."
            )
        for column in diff.to_add:
            pod_sdk.tables.add_column(
                table_name,
                build_request(AddColumnRequest, {"column": column}, context=f"table {table_name} column"),
            )
        for column_name in diff.to_remove:
            pod_sdk.tables.remove_column(table_name, column_name)
        summary["tables"].append(f"updated:{table_name}")
        _progress_done("table", table_name, "updated")

    # Grants reference resources by name, and may point at workflows, apps,
    # schedules, or folders that import later than agents/functions. Collect
    # permission payloads here and apply them in one pass at the end.
    pending_permissions: list[tuple[str, str, dict[str, Any]]] = []

    existing_functions = _build_existing_map(list_items(pod_sdk.functions.list(limit=1000)))
    for resource_dir in _resource_dirs(source_dir, "functions"):
        function_name = resource_dir.name
        payload, permissions_payload = _split_resource_permissions_payload(
            load_resource_payload(resource_dir, function_name)
        )
        payload = _sanitize_function_payload_for_import(payload)
        if function_name in existing_functions:
            if not upsert:
                raise ValueError(f"Function already exists and --no-upsert was requested: {function_name}")
            _progress_start("function", function_name, "updating")
            update_payload = _strip_keys(
                payload,
                {"name", "input_schema", "output_schema", "config_schema", "config"},
            )
            if update_payload:
                pod_sdk.functions.update(
                    function_name,
                    build_request(UpdateFunctionRequest, update_payload, context=f"function {function_name}"),
                )
            summary["functions"].append(f"updated:{function_name}")
            _progress_done("function", function_name, "updated")
        else:
            _progress_start("function", function_name, "creating")
            pod_sdk.functions.create(
                build_request(CreateFunctionRequest, payload, context=f"function {function_name}")
            )
            summary["functions"].append(f"created:{function_name}")
            _progress_done("function", function_name, "created")
        if permissions_payload is not None:
            pending_permissions.append(("function", function_name, permissions_payload))

    existing_agents = _build_existing_map(list_items(pod_sdk.agents.list(limit=1000)))
    for resource_dir in _resource_dirs(source_dir, "agents"):
        agent_name = resource_dir.name
        payload, permissions_payload = _split_resource_permissions_payload(
            load_resource_payload(resource_dir, agent_name)
        )
        if agent_name in existing_agents:
            if not upsert:
                raise ValueError(f"Agent already exists and --no-upsert was requested: {agent_name}")
            _progress_start("agent", agent_name, "updating")
            existing_agent = to_plain(pod_sdk.agents.get(agent_name))
            update_payload = _prepare_agent_update_payload(payload, existing_agent)
            if update_payload:
                pod_sdk.agents.update(
                    agent_name,
                    build_request(UpdateAgentRequest, update_payload, context=f"agent {agent_name}"),
                )
            summary["agents"].append(f"updated:{agent_name}")
            _progress_done("agent", agent_name, "updated")
        else:
            _progress_start("agent", agent_name, "creating")
            pod_sdk.agents.create(
                build_request(CreateAgentRequest, payload, context=f"agent {agent_name}")
            )
            summary["agents"].append(f"created:{agent_name}")
            _progress_done("agent", agent_name, "created")
        if permissions_payload is not None:
            pending_permissions.append(("agent", agent_name, permissions_payload))

    apps = _build_existing_map(list_items(pod_sdk.apps.list(limit=1000)))
    for resource_dir in _resource_dirs(source_dir, "apps"):
        app_name = resource_dir.name
        payload = load_resource_payload(
            resource_dir, app_name, resource_type="apps"
        )
        app_exists = app_name in apps
        if app_exists:
            if not upsert:
                raise ValueError(f"App already exists and --no-upsert was requested: {app_name}")
            _progress_start("app", app_name, "updating")
            _update_app_with_conflict_retry(
                client,
                pod_id=pod_id,
                app_name=app_name,
                payload=payload,
            )
            summary["apps"].append(f"updated:{app_name}")
            _progress_done("app", app_name, "updated")
        else:
            _progress_start("app", app_name, "creating")
            action = _create_or_update_app(
                client,
                pod_id=pod_id,
                app_name=app_name,
                payload=payload,
                app_exists=False,
            )
            summary["apps"].append(f"{action}:{app_name}")
            _progress_done("app", app_name, "created")

        source_subdir = resource_dir / "source"
        html_file = resource_dir / "html.html"
        dist_archive_file = resource_dir / "dist.zip"
        if source_subdir.exists():
            _progress_start("app", app_name, "deploying bundle")
            deploy_app_bundle(
                client,
                pod_id=pod_id,
                app_name=app_name,
                source_dir=source_subdir,
                ensure_exists=False,
            )
            _progress_done("app", app_name, "deployed bundle")
        elif dist_archive_file.exists() or html_file.exists():
            dist_archive_path = _build_app_bundle(
                resource_dir,
                stream_output=True,
            )
            _progress_start("app", app_name, "uploading bundle")
            pod_sdk.apps.upload_bundle(
                app_name,
                dist_archive=dist_archive_path,
            )
            _progress_done("app", app_name, "uploaded bundle")

    workflow_dirs = _resource_dirs(source_dir, "workflows")
    existing_workflows = (
        _build_existing_map(list_items(pod_sdk.workflows.list(limit=1000)))
        if workflow_dirs
        else {}
    )
    # Resolve ${name} variables (and the legacy $POD_MEMBER token) lazily and
    # once: member resolution costs a members.list call we skip for bundles that
    # carry no placeholders.
    apply_variables = _build_variable_applier(
        client,
        pod_sdk,
        source_dir=source_dir,
        var_overrides=variables,
        member_override=pod_member_id,
    )

    for resource_dir in workflow_dirs:
        workflow_name = resource_dir.name
        payload = apply_variables(load_resource_payload(resource_dir, workflow_name))
        metadata_payload = _strip_keys(payload, {"name", "nodes", "edges"})
        graph_start = payload.get("start")

        if workflow_name in existing_workflows:
            if not upsert:
                raise ValueError(f"Workflow already exists and --no-upsert was requested: {workflow_name}")
            _progress_start("workflow", workflow_name, "updating")
            pod_sdk.workflows.update(
                workflow_name,
                build_request(WorkflowUpdateRequest, metadata_payload, context=f"workflow {workflow_name}"),
            )
            action = "updated"
        else:
            create_payload = {"name": workflow_name, **metadata_payload}
            _progress_start("workflow", workflow_name, "creating")
            pod_sdk.workflows.create(
                build_request(WorkflowCreateRequest, create_payload, context=f"workflow {workflow_name}")
            )
            action = "created"

        graph_payload: dict[str, Any] = {
            "nodes": payload.get("nodes") or [],
            "edges": payload.get("edges") or [],
        }
        if graph_start is not None:
            graph_payload["start"] = graph_start
        pod_sdk.workflows.update_graph(workflow_name, graph_payload)
        summary["workflows"].append(f"{action}:{workflow_name}")
        _progress_done("workflow", workflow_name, action)

    schedule_dirs = _resource_dirs(source_dir, "schedules")
    existing_schedules = (
        _build_existing_schedule_map(list_items(pod_sdk.schedules.list(limit=1000)))
        if schedule_dirs
        else {}
    )
    for resource_dir in schedule_dirs:
        schedule_name = resource_dir.name
        payload = apply_variables(load_resource_payload(resource_dir, schedule_name))
        payload.setdefault("name", schedule_name)
        existing = existing_schedules.get(schedule_name)
        existing_id = str(existing.get("id") or "") if existing else ""
        if existing and existing_id:
            if not upsert:
                raise ValueError(f"Schedule already exists and --no-upsert was requested: {schedule_name}")
            _progress_start("schedule", schedule_name, "updating")
            pod_sdk.schedules.update(
                existing_id,
                build_request(
                    UpdateScheduleRequest,
                    _schedule_update_fields(payload),
                    context=f"schedule {schedule_name}",
                ),
            )
            summary["schedules"].append(f"updated:{schedule_name}")
            _progress_done("schedule", schedule_name, "updated")
        else:
            _progress_start("schedule", schedule_name, "creating")
            created = _create_schedule_from_payload(
                client,
                pod_id=pod_id,
                payload=payload,
            )
            created_name = str(created.get("name") or created.get("id") or schedule_name)
            summary["schedules"].append(f"created:{created_name}")
            _progress_done("schedule", schedule_name, "created")

    surface_dirs = _resource_dirs(source_dir, "surfaces")
    existing_surface_platforms = (
        {
            str(item.get("platform") or item.get("surface_type") or "").upper()
            for item in list_items(pod_sdk.surfaces.list(limit=100))
        }
        if surface_dirs
        else set()
    )
    for resource_dir in surface_dirs:
        surface_name = resource_dir.name
        payload = apply_variables(load_resource_payload(resource_dir, surface_name))
        platform = _surface_platform_from_payload(payload, surface_name)
        exists = platform in existing_surface_platforms
        if exists and not upsert:
            raise ValueError(
                f"Surface already exists for platform and --no-upsert was requested: {platform}"
            )
        action = "updated" if exists else "created"
        _progress_start("surface", surface_name, "upserting")
        pod_sdk.surfaces.upsert(platform, _surface_upsert_body(payload))
        summary["surfaces"].append(f"{action}:{surface_name}")
        _progress_done("surface", surface_name, action)

    summary["files"].extend(
        _import_pod_files(client, pod_id, source_dir, with_files=with_files)
    )

    for kind, resource_name, permissions_payload in pending_permissions:
        _progress_start(kind, resource_name, "replacing permissions")
        if kind == "function":
            pod_sdk.functions.replace_permissions(
                resource_name,
                build_request(
                    FunctionPermissionsReplaceRequest,
                    permissions_payload,
                    context=f"function {resource_name} permissions",
                ),
            )
        else:
            pod_sdk.agents.replace_permissions(
                resource_name,
                build_request(
                    AgentPermissionsReplaceRequest,
                    permissions_payload,
                    context=f"agent {resource_name} permissions",
                ),
            )
        summary[f"{kind}s"].append(f"permissions:{resource_name}")
        _progress_done(kind, resource_name, "replaced permissions")

    return {
        "ok": True,
        "pod_id": pod_id,
        "source_dir": str(source_dir),
        "summary": summary,
    }
