from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from zipfile import ZIP_DEFLATED, ZipFile

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

FORMAT_VERSION = 2
# Legacy placeholder for a pod-member assignee; superseded by ${name} variables
# but still recognized on import so older templated bundles keep working.
POD_MEMBER_TOKEN = "$POD_MEMBER"
# Per-table row dump carried by `--with-data` (CSV, complex cells as JSON text).
TABLE_DATA_FILE = "data.csv"
_TABLE_DATA_CANDIDATES = ("data.csv", "data.jsonl", "data.json")
RAW_FILE_REF_KEY = "$file"
JSON_FILE_REF_KEY = "$json_file"
# Some app scaffolds write the manifest as `lemma.app.json` rather than
# `<name>.json`. Accept it as an alias so those bundles import without a rename.
APP_MANIFEST_ALIAS = "lemma.app.json"
RESOURCE_DIRS = (
    "tables",
    "functions",
    "agents",
    "workflows",
    "schedules",
    "surfaces",
    "apps",
    "files",
)
EXPORTABLE_RESOURCE_DIRS = frozenset(RESOURCE_DIRS)
RESOURCE_DIR_ALIASES = {
    "table": "tables",
    "tables": "tables",
    "function": "functions",
    "functions": "functions",
    "agent": "agents",
    "agents": "agents",
    "workflow": "workflows",
    "workflows": "workflows",
    "schedule": "schedules",
    "schedules": "schedules",
    "surface": "surfaces",
    "surfaces": "surfaces",
    "app": "apps",
    "apps": "apps",
    "file": "files",
    "files": "files",
}
SYSTEM_TABLE_COLUMNS = frozenset({"created_at", "updated_at", "user_id"})


@dataclass
class TableDiff:
    to_add: list[dict[str, Any]]
    to_remove: list[str]
    incompatible: list[str]


@dataclass
class BundleValidationIssue:
    path: str
    message: str


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


def _json_dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, data: Any) -> None:
    path.write_text(_json_dump(data), encoding="utf-8")


def strip_jsonc(text: str) -> str:
    """Remove `//` line comments and `/* */` block comments from JSONC text,
    leaving string literals (and any `//` inside them, e.g. URLs) untouched.
    Comment characters are replaced with spaces so byte offsets — and thus the
    line/column in any downstream json error — stay accurate."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            # consume the closing */
            i += 2
            out.append("  ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Blank a trailing comma (one before a closing `}`/`]`, ignoring whitespace)
    by replacing it with a space, so a common hand-edit mistake still parses.
    Replacing rather than deleting keeps byte offsets — and json error line/cols —
    accurate. Expects comment-free input (run after strip_jsonc)."""
    out = list(text)
    n = len(out)
    in_string = False
    escaped = False
    for i, ch in enumerate(out):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            j = i + 1
            while j < n and out[j] in " \t\r\n":
                j += 1
            if j < n and out[j] in "}]":
                out[i] = " "
    return "".join(out)


def loads_jsonc(text: str) -> Any:
    """json.loads that tolerates JSONC comments and trailing commas (so scaffolded
    and hand-edited bundle files can be self-documenting and forgiving)."""
    return json.loads(_strip_trailing_commas(strip_jsonc(text)))


def _read_json(path: Path) -> dict[str, Any]:
    payload = loads_jsonc(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _sanitize_resource_name(name: str) -> str:
    return name.strip()


def _ensure_clean_dir(path: Path, *, force: bool) -> None:
    if path.exists():
        if not force:
            raise ValueError(f"Output directory already exists: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _ensure_resource_dirs(root: Path) -> None:
    for resource_dir in RESOURCE_DIRS:
        (root / resource_dir).mkdir(parents=True, exist_ok=True)


def normalize_resource_dir_name(value: str) -> str:
    normalized = value.lower().strip().replace("-", "_")
    return RESOURCE_DIR_ALIASES.get(normalized, "")


def _strip_keys(payload: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in keys}


def _normalize_resource_permissions_payload(payload: dict[str, Any]) -> dict[str, Any]:
    grants = payload.get("grants", [])
    if not isinstance(grants, list):
        raise ValueError("Embedded permissions must be an object with a grants list.")
    if any(
        not isinstance(grant, dict) or "resource_name" not in grant
        for grant in grants
    ):
        raise ValueError("Permission grants must reference resources by resource_name.")
    return {"grants": grants}


def _split_resource_permissions_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    resource_payload = dict(payload)
    raw_permissions = resource_payload.pop("permissions", None)
    if raw_permissions is None:
        return resource_payload, None
    if not isinstance(raw_permissions, dict):
        raise ValueError("Embedded permissions must be an object with a grants list.")
    return resource_payload, _normalize_resource_permissions_payload(raw_permissions)


def _attach_permissions_payload(
    payload: dict[str, Any],
    permissions: dict[str, Any],
) -> dict[str, Any]:
    return {
        **payload,
        "permissions": _normalize_resource_permissions_payload(permissions),
    }


# Structured-contract fields an agent bundle declares wholesale: a bundle that
# omits them means "no schema". On update we send an explicit null so a stale
# schema on the existing agent is cleared rather than silently retained (the
# backend leaves omitted fields untouched). agent_runtime is deliberately NOT in
# this set — templates strip it to preserve the target's pinned runtime.
_AGENT_CLEARABLE_SCHEMA_FIELDS = ("output_schema", "input_schema")


def _prepare_agent_update_payload(
    payload: dict[str, Any],
    _existing: dict[str, Any] | None,
) -> dict[str, Any]:
    update = _strip_keys(payload, {"name"})
    for field in _AGENT_CLEARABLE_SCHEMA_FIELDS:
        update.setdefault(field, None)
    return update


def _record_export_contents(
    bundle_root: Path,
    *,
    included: set[str],
    excluded: set[str],
    names: set[str],
    with_data: bool,
    with_files: bool,
) -> dict[str, Any]:
    """Record the bundle's selective scope under ``pod.json -> contents``: which
    resource types/names it covers and whether it carries table rows (data.csv)
    and file bytes. On import this auto-enables seeding/upload; a re-export can
    refresh exactly this set."""
    pod_path = bundle_root / "pod.json"
    if not pod_path.is_file():
        return {}
    pod_data = _read_json(pod_path)
    contents: dict[str, Any] = {}
    if included:
        contents["resources"] = sorted(included)
    if excluded:
        contents["exclude"] = sorted(excluded)
    if names:
        contents["names"] = sorted(names)
    if with_data:
        contents["with_data"] = True
    if with_files:
        contents["with_files"] = True
    pod_data["contents"] = contents
    _write_json(pod_path, pod_data)
    return contents


def _read_export_contents(source_dir: Path) -> dict[str, Any]:
    """Read the ``contents`` block written by a manifest-aware export (empty for
    older bundles)."""
    pod_path = source_dir / "pod.json"
    if not pod_path.is_file():
        return {}
    contents = _read_json(pod_path).get("contents")
    return contents if isinstance(contents, dict) else {}


def _normalize_pod_payload(pod: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "name": pod.get("name"),
        "description": pod.get("description"),
        "icon_url": pod.get("icon_url"),
    }


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


# Audit/ownership columns dropped from seeded rows: the backend manages the
# timestamps, and a source-pod user_id would orphan ownership in the target pod
# (bulk-create assigns rows to the importer). The primary key is kept so any
# foreign-key references between seeded tables still resolve.
_SEED_STRIP_COLUMNS = frozenset({"created_at", "updated_at", "user_id"})


def _seed_strip_columns(resource_dir: Path, table_name: str) -> set[str]:
    """Columns to drop from seeded rows. Beyond the audit/ownership names, strip
    every backend-generated (``auto``) column except the primary key — the
    datastore rejects a value supplied for an auto column such as a SERIAL
    (e.g. a ``ticket_number``), while the primary key is kept so upsert matching
    and any FK references still resolve."""
    strip = set(_SEED_STRIP_COLUMNS)
    try:
        manifest = load_resource_payload(resource_dir, table_name)
    except Exception:
        return strip
    primary_key = str(manifest.get("primary_key_column") or "id")
    for column in manifest.get("columns") or []:
        name = str(column.get("name") or "")
        if name and column.get("auto") and name != primary_key:
            strip.add(name)
    return strip


def _import_table_data(pod_sdk: Any, table_name: str, resource_dir: Path) -> int:
    """Seed a table from a bundled ``data.{csv,jsonl,json}`` file via bulk create.
    Returns the number of rows sent (0 when there is no data file)."""
    data_file = next(
        (resource_dir / name for name in _TABLE_DATA_CANDIDATES if (resource_dir / name).is_file()),
        None,
    )
    if data_file is None:
        return 0
    strip_columns = _seed_strip_columns(resource_dir, table_name)
    rows = [
        {key: value for key, value in row.items() if key not in strip_columns}
        for row in read_record_rows(data_file, None)
    ]
    if not rows:
        return 0
    # Upsert so re-importing an edited data.csv updates existing rows (matched on
    # the table's primary key) instead of failing — idempotent re-seeding.
    pod_sdk.records.bulk_create(table_name, rows, upsert=True)
    return len(rows)


def _normalize_table_payload(table: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "name": table.get("name"),
        "columns": table.get("columns") or [],
        "config": table.get("config"),
        "enable_rls": table.get("enable_rls", True),
        "primary_key_column": table.get("primary_key_column", "id"),
        "visibility": table.get("visibility"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _is_system_table_column(column: dict[str, Any]) -> bool:
    return bool(column.get("system")) or str(column.get("name") or "") in SYSTEM_TABLE_COLUMNS


def _sanitize_table_payload_for_import(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    sanitized["columns"] = [
        column
        for column in payload.get("columns") or []
        if not _is_system_table_column(column)
    ]
    return sanitized


def _declared_reserved_columns(payload: dict[str, Any]) -> list[str]:
    """System-managed column names the user declared explicitly in a table payload.

    Mirrors the backend rule (``materialize_table_columns``): ``created_at`` and
    ``updated_at`` are always reserved, while ``user_id`` is reserved only when RLS
    is enabled. Columns the backend itself emitted (marked ``system: true``, as in
    an exported bundle) are excluded so export -> import round-trips cleanly — those
    are stripped silently by ``_sanitize_table_payload_for_import``. What's left is
    a genuine author mistake the backend would reject, so we surface it instead of
    silently dropping the column.
    """
    reserved = {"created_at", "updated_at"}
    if payload.get("enable_rls", True):
        reserved.add("user_id")
    declared: list[str] = []
    for column in payload.get("columns") or []:
        if bool(column.get("system")):
            continue
        name = str(column.get("name") or "")
        if name in reserved and name not in declared:
            declared.append(name)
    return declared


def _normalize_function_payload(function: dict[str, Any]) -> dict[str, Any]:
    payload = _strip_keys(
        function,
        {
            "id",
            "pod_id",
            "user_id",
            "created_at",
            "updated_at",
            "status",
            "code_path",
            "input_schema",
            "output_schema",
            "config_schema",
            "allowed_actions",
        },
    )
    return payload


def _sanitize_function_payload_for_import(payload: dict[str, Any]) -> dict[str, Any]:
    return _strip_keys(
        payload,
        {
            "id",
            "pod_id",
            "user_id",
            "created_at",
            "updated_at",
            "status",
            "code_path",
            "input_schema",
            "output_schema",
            "config_schema",
            "allowed_actions",
        },
    )


def _normalize_agent_payload(agent: dict[str, Any]) -> dict[str, Any]:
    payload = _strip_keys(
        agent,
        {"id", "pod_id", "user_id", "created_at", "updated_at", "allowed_actions"},
    )
    # Make the structured-contract fields explicit in the bundle (as null when
    # unset) so the declarative intent is visible and a re-import faithfully
    # clears a schema the source agent no longer defines.
    for field in _AGENT_CLEARABLE_SCHEMA_FIELDS:
        payload.setdefault(field, None)
    return payload


def _normalize_workflow_payload(workflow: dict[str, Any]) -> dict[str, Any]:
    payload = _strip_keys(
        workflow,
        {"id", "pod_id", "created_at", "updated_at", "is_active", "allowed_actions"},
    )
    payload.setdefault("nodes", [])
    payload.setdefault("edges", [])
    return payload


def _normalize_schedule_payload(schedule: dict[str, Any]) -> dict[str, Any]:
    return _strip_keys(
        schedule,
        {
            "id",
            "pod_id",
            "user_id",
            "created_at",
            "updated_at",
            "last_run_at",
            "next_run_at",
            "agent_id",
            "workflow_id",
            "allowed_actions",
        },
    )


def _normalize_surface_payload(surface: dict[str, Any]) -> dict[str, Any]:
    platform = str(surface.get("platform") or surface.get("surface_type") or "").upper()
    config = surface.get("config") or {}

    behavior_config: dict[str, Any] = {}
    channels: list[dict[str, Any]] = []
    for channel in config.get("channels") or []:
        if not isinstance(channel, dict) or channel.get("enabled") is False:
            continue
        entry = {
            key: channel[key]
            for key in ("channel_id", "channel_name", "agent_name")
            if channel.get(key)
        }
        if entry:
            channels.append(entry)
    if channels:
        behavior_config["channels"] = channels
    identity = config.get("identity") or {}
    identity_entry = {
        key: identity[key]
        for key in ("allowed_domains", "allowed_email_addresses")
        if identity.get(key)
    }
    if identity_entry:
        behavior_config["identity"] = identity_entry

    status = str(surface.get("status") or "").upper()
    payload: dict[str, Any] = {
        "name": platform.lower(),
        "platform": platform,
        "default_agent_name": surface.get("agent_name"),
        "credential_mode": surface.get("credential_mode"),
        "account_id": surface.get("account_id"),
        "is_enabled": status != "INACTIVE",
    }
    if behavior_config:
        payload["config"] = behavior_config
    return {key: value for key, value in payload.items() if value is not None}


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


def _surface_platform_from_payload(payload: dict[str, Any], resource_name: str) -> str:
    return str(payload.get("platform") or resource_name).upper()


def _normalize_app_payload(app: dict[str, Any]) -> dict[str, Any]:
    return _strip_keys(
        app,
        {
            "id",
            "pod_id",
            "user_id",
            "created_at",
            "updated_at",
            "status",
            "current_release_id",
            "source_archive_path",
            "allowed_actions",
        },
    )


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


# --- Portable variables (pod.json manifest) -------------------------------
#
# Some resource fields hold ids that are only valid in the source pod/org and
# break a re-import elsewhere: a workflow's assignee_pod_member_id and a
# schedule/surface account_id. On export we replace each with a ``${name}``
# placeholder and record it under ``pod.json -> variables``; on import the
# placeholders are resolved (``--var``, ``--values``, or a per-type default) and
# any still-unresolved ones drop their field so the import still succeeds.

_PLACEHOLDER_RE = re.compile(r"\$\{[A-Za-z0-9_]+\}")
# Fields whose values are non-portable ids, by variable type.
_MEMBER_REF_FIELDS = frozenset({"assignee_pod_member_id"})
_ACCOUNT_REF_FIELDS = frozenset({"account_id"})


def _placeholder(name: str) -> str:
    return "${" + name + "}"


def _slug_var_name(base: str, existing: dict[str, Any]) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(base).lower()).strip("_") or "var"
    name = cleaned
    index = 2
    while name in existing:
        name = f"{cleaned}_{index}"
        index += 1
    return name


def _tokenize_ref_fields(node: object, field_keys: frozenset[str], on_value) -> bool:
    """Recursively replace string values stored under ``field_keys`` with the
    placeholder returned by ``on_value(raw)``. Skips values already templated."""
    changed = False
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if (
                key in field_keys
                and isinstance(value, str)
                and value
                and value != POD_MEMBER_TOKEN
                and not _PLACEHOLDER_RE.fullmatch(value)
            ):
                node[key] = on_value(value)
                changed = True
            elif _tokenize_ref_fields(value, field_keys, on_value):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _tokenize_ref_fields(item, field_keys, on_value):
                changed = True
    return changed


def _extract_portable_variables(bundle_root: Path) -> dict[str, Any]:
    """Replace non-portable ids in workflows/schedules/surfaces with ``${name}``
    placeholders and record them under ``pod.json -> variables``. Returns the
    variables map (possibly empty)."""
    pod_path = bundle_root / "pod.json"
    if not pod_path.is_file():
        return {}
    pod_data = _read_json(pod_path)
    variables: dict[str, Any] = dict(pod_data.get("variables") or {})
    by_value: dict[tuple[str, str], str] = {}

    def register(vtype: str, raw: str, base: str, meta: dict[str, Any]) -> str:
        key = (vtype, str(raw))
        if key in by_value:
            return _placeholder(by_value[key])
        name = _slug_var_name(base, variables)
        variables[name] = {"type": vtype, "source_value": str(raw), **meta}
        by_value[key] = name
        return _placeholder(name)

    def rewrite(resource_glob: str, field_keys, make_ref) -> None:
        for resource_json in sorted((bundle_root).glob(resource_glob)):
            data = loads_jsonc(resource_json.read_text(encoding="utf-8"))
            owner = resource_json.parent.name
            if _tokenize_ref_fields(data, field_keys, lambda raw, owner=owner: make_ref(owner, raw)):
                resource_json.write_text(
                    json.dumps(data, indent=2) + "\n", encoding="utf-8"
                )

    rewrite(
        "workflows/*/*.json",
        _MEMBER_REF_FIELDS,
        lambda owner, raw: register(
            "pod_member",
            raw,
            f"{owner}_assignee",
            {"description": f"Pod member assigned in workflow '{owner}'"},
        ),
    )
    rewrite(
        "schedules/*/*.json",
        _ACCOUNT_REF_FIELDS,
        lambda owner, raw: register(
            "account",
            raw,
            f"{owner}_account",
            {"description": f"Connector account for schedule '{owner}'"},
        ),
    )
    rewrite(
        "surfaces/*/*.json",
        _ACCOUNT_REF_FIELDS,
        lambda owner, raw: register(
            "account",
            raw,
            f"{owner}_account",
            {
                "description": f"Connector account for the {owner} surface",
                "platform": owner,
            },
        ),
    )

    if variables:
        pod_data["variables"] = variables
        _write_json(pod_path, pod_data)
    return variables


def _strip_unresolved_placeholders(node: object) -> object:
    """Drop dict entries whose value is still an unresolved ``${...}`` token so
    a literal placeholder never reaches the API (e.g. an unsupplied account)."""
    if isinstance(node, dict):
        return {
            key: _strip_unresolved_placeholders(value)
            for key, value in node.items()
            if not (isinstance(value, str) and _PLACEHOLDER_RE.fullmatch(value))
        }
    if isinstance(node, list):
        return [_strip_unresolved_placeholders(item) for item in node]
    return node


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


FILES_MANIFEST = ".files.json"


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

    # Derive what the bundle needs (connectors, people, variables, data) and
    # what it does (capabilities), so the listing page, import wizard, and CLI
    # all render the same facts. Runs after variable extraction (it reads them).
    from .pod_requirements import extract_requirements

    requirements_summary = extract_requirements(bundle_root)

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
        "requirements": {
            category: len(items)
            for category, items in (requirements_summary.get("requirements") or {}).items()
            if isinstance(items, list)
        },
        "capabilities": [
            cap.get("summary") for cap in (requirements_summary.get("capabilities") or [])
        ],
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


def _resolve_file_refs(value: Any, *, base_dir: Path) -> Any:
    if isinstance(value, list):
        return [_resolve_file_refs(item, base_dir=base_dir) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value.keys()) == {RAW_FILE_REF_KEY}:
        raw_path = base_dir / str(value[RAW_FILE_REF_KEY])
        return raw_path.read_text(encoding="utf-8")
    if set(value.keys()) == {JSON_FILE_REF_KEY}:
        json_path = base_dir / str(value[JSON_FILE_REF_KEY])
        return loads_jsonc(json_path.read_text(encoding="utf-8"))
    return {key: _resolve_file_refs(item, base_dir=base_dir) for key, item in value.items()}


def _resource_manifest_path(
    resource_dir: Path,
    resource_name: str,
    *,
    resource_type: str | None = None,
) -> Path | None:
    """Path to a resource directory's manifest JSON, or None if absent.

    The canonical name is `<resource_name>.json`. Apps additionally accept
    `lemma.app.json` as a fallback, since some app scaffolds write that name.
    """
    primary = resource_dir / f"{resource_name}.json"
    if primary.exists():
        return primary
    if resource_type == "apps":
        alias = resource_dir / APP_MANIFEST_ALIAS
        if alias.exists():
            return alias
    return None


def load_resource_payload(
    resource_dir: Path,
    resource_name: str,
    *,
    resource_type: str | None = None,
) -> dict[str, Any]:
    manifest_path = _resource_manifest_path(
        resource_dir, resource_name, resource_type=resource_type
    )
    if manifest_path is None:
        manifest_path = resource_dir / f"{resource_name}.json"
    payload = _resolve_file_refs(
        _read_json(manifest_path),
        base_dir=resource_dir,
    )
    if "tool_sets" in payload and "toolsets" not in payload:
        payload["toolsets"] = payload.pop("tool_sets")
    return payload


def _normalize_column_for_diff(column: dict[str, Any], *, primary_key: bool = False) -> dict[str, Any]:
    type_name = (column.get("type_params") or {}).get("type") or column.get("type")
    normalized = {
        "name": column.get("name"),
        "type": type_name,
        "required": bool(column.get("required", False)),
        "unique": bool(column.get("unique", False)),
    }
    if primary_key:
        normalized["primary_key"] = True
    return normalized


def diff_table_columns(existing: dict[str, Any], desired: dict[str, Any]) -> TableDiff:
    primary_key = str(existing.get("primary_key_column") or desired.get("primary_key_column") or "id")
    existing_columns = {
        str(column.get("name")): _normalize_column_for_diff(
            column,
            primary_key=str(column.get("name")) == primary_key,
        )
        for column in existing.get("columns") or []
        if not _is_system_table_column(column)
    }
    desired_columns = {
        str(column.get("name")): _normalize_column_for_diff(
            column,
            primary_key=str(column.get("name")) == primary_key,
        )
        for column in desired.get("columns") or []
        if not _is_system_table_column(column)
    }
    desired_columns_raw = {
        str(column.get("name")): column
        for column in desired.get("columns") or []
        if not _is_system_table_column(column)
    }

    to_add = [
        desired_columns_raw[name]
        for name in desired_columns_raw.keys() - existing_columns.keys()
    ]
    to_remove = sorted(
        name
        for name in existing_columns.keys() - desired_columns.keys()
        if name and name != primary_key
    )

    incompatible: list[str] = []
    for name in sorted(existing_columns.keys() & desired_columns.keys()):
        existing_column = existing_columns[name]
        desired_column = desired_columns[name]
        if existing_column != desired_column:
            incompatible.append(name)

    return TableDiff(
        to_add=sorted(to_add, key=lambda item: str(item.get("name", ""))),
        to_remove=to_remove,
        incompatible=incompatible,
    )


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


def _table_fk_dependencies(payload: dict[str, Any]) -> set[str]:
    """Table names referenced by this table's foreign-key columns."""
    deps: set[str] = set()
    for column in payload.get("columns") or []:
        if not isinstance(column, dict):
            continue
        fk = column.get("foreign_key")
        if isinstance(fk, dict) and fk.get("references"):
            referenced = str(fk["references"]).split(".", 1)[0].strip()
            if referenced:
                deps.add(referenced)
    return deps


def _order_table_dirs_by_dependency(table_dirs: list[Path]) -> list[Path]:
    """Sort table directories so a table is imported after any table it
    references via a foreign key. Falls back to alphabetical for ties and
    leaves cycles in a stable order rather than failing."""
    dir_by_name: dict[str, Path] = {path.name: path for path in table_dirs}
    deps_by_name: dict[str, set[str]] = {}
    for path in table_dirs:
        try:
            payload = load_resource_payload(path, path.name)
        except Exception:
            payload = {}
        # Only intra-bundle dependencies matter for ordering.
        deps_by_name[path.name] = _table_fk_dependencies(payload) & set(dir_by_name)

    ordered: list[Path] = []
    placed: set[str] = set()
    remaining = [path.name for path in table_dirs]
    while remaining:
        ready = [
            name for name in remaining if deps_by_name[name] <= placed
        ]
        if not ready:
            # Cycle or self-reference: emit the rest in stable order.
            ready = list(remaining)
        for name in sorted(ready):
            ordered.append(dir_by_name[name])
            placed.add(name)
        remaining = [name for name in remaining if name not in placed]
    return ordered


def _looks_like_single_resource_dir(path: Path, resource_type: str) -> bool:
    if resource_type == "files":
        return False
    return _resource_manifest_path(
        path, path.name, resource_type=resource_type
    ) is not None


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


def _file_path_key(parts: list[str]) -> str:
    return "/".join(parts)


def _parse_function_headers(code: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        if ":" not in stripped:
            continue
        key, value = stripped[1:].split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def _validate_function_payload(
    resource_dir: Path,
    resource_name: str,
    payload: dict[str, Any],
) -> list[BundleValidationIssue]:
    issues: list[BundleValidationIssue] = []
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        issues.append(BundleValidationIssue(path=str(resource_dir), message="Function code is required."))
        return issues

    try:
        ast.parse(code, filename=str(resource_dir / "code.py"))
    except SyntaxError as exc:
        issues.append(
            BundleValidationIssue(
                path=str(resource_dir / "code.py"),
                message=f"Python syntax error: {exc.msg} at line {exc.lineno}",
            )
        )
        return issues

    headers = _parse_function_headers(code)
    required_headers = ["input_type_name", "output_type_name", "function_name"]
    if payload.get("config_schema") is not None:
        required_headers.append("config_type_name")

    for header_name in required_headers:
        actual_value = headers.get(header_name)
        if not actual_value:
            issues.append(
                BundleValidationIssue(
                    path=str(resource_dir / "code.py"),
                    message=f"Missing required header #{header_name}.",
                )
            )
    function_name_in_code = headers.get("function_name")
    if function_name_in_code and function_name_in_code != resource_name:
        issues.append(
            BundleValidationIssue(
                path=str(resource_dir / "code.py"),
                message=f"Invalid #function_name header: expected '{resource_name}'.",
            )
        )
    return issues


def _table_destructive_change(
    pod_sdk: Any, source_dir: Path, table_name: str
) -> dict[str, Any] | None:
    """Return the data-losing part of updating an existing table — columns that
    would be dropped or rebuilt — or None if the update is purely additive.

    A column the bundle no longer declares is *removed* (its data is lost); a
    column whose type/required/unique changed is *incompatible* and today can't
    be migrated in place. Surfacing these in the plan is what lets the importer
    (or the wizard) see data loss before it happens instead of after.
    """
    resource_dir = source_dir / "tables" / table_name
    try:
        desired = load_resource_payload(resource_dir, table_name)
        existing = to_plain(pod_sdk.tables.get(table_name))
    except Exception:
        return None  # payload/fetch errors are reported elsewhere
    diff = diff_table_columns(existing, desired)
    if not diff.to_remove and not diff.incompatible:
        return None
    change: dict[str, Any] = {"table": table_name}
    if diff.to_remove:
        change["removed_columns"] = list(diff.to_remove)
    if diff.incompatible:
        change["incompatible_columns"] = list(diff.incompatible)
    return change


def _build_import_plan(
    client: Lemma,
    *,
    pod_id: str,
    source_dir: Path,
    upsert: bool,
) -> tuple[dict[str, list[str]], list[BundleValidationIssue], list[dict[str, Any]]]:
    summary: dict[str, list[str]] = {key: [] for key in RESOURCE_DIRS}
    issues: list[BundleValidationIssue] = []
    destructive: list[dict[str, Any]] = []
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
                change = _table_destructive_change(pod_sdk, source_dir, table_name)
                if change is not None:
                    destructive.append(change)
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

    return summary, issues, destructive


def _bundle_folder_keys(files_root: Path) -> set[str]:
    keys: set[str] = set()
    if files_root.exists():
        for folder_dir in (path for path in files_root.rglob("*") if path.is_dir()):
            parts = list(folder_dir.relative_to(files_root).parts)
            if parts:
                keys.add(_file_path_key(parts))
    return keys


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


def _format_unresolved_message(unresolved: list[dict[str, Any]]) -> str:
    """Render a blocking-requirements error that tells the importer exactly what
    to supply (and how) instead of a field silently vanishing."""
    lines: list[str] = []
    for item in unresolved:
        kind = item.get("kind")
        used_by = ", ".join(item.get("used_by") or []) or "—"
        if kind == "connector":
            var = (item.get("resolution") or {}).get("var") or item.get("key")
            purpose = item.get("purpose") or f"connector '{item.get('key')}'"
            lines.append(f"  - connector {item.get('key')} ({purpose}; used by {used_by})"
                         f"  →  --var {var}=<account-id>")
        elif kind == "member":
            var = (item.get("resolution") or {}).get("var") or item.get("key")
            lines.append(f"  - person {item.get('key')} (used by {used_by})"
                         f"  →  --var {var}=<pod-member-id>")
        else:
            key = item.get("key")
            purpose = item.get("purpose") or item.get("type") or "value"
            lines.append(f"  - variable {key} ({purpose})  →  --var {key}=<value>")
    body = "\n".join(lines)
    return (
        "This bundle needs values before it can run. Supply them with "
        "--var NAME=VALUE (or --values file.json), or re-run with --defer to "
        f"import now and wire them up later:\n{body}"
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
    allow_unresolved: bool = False,
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
                allow_unresolved=allow_unresolved,
            )
            result["source_dir"] = str(source_dir)
            return result

    # A manifest-aware bundle records whether it carries table rows / file bytes;
    # honor that so re-importing it seeds them without re-passing the flags.
    manifest_contents = _read_export_contents(source_dir)
    with_data = with_data or bool(manifest_contents.get("with_data"))
    with_files = with_files or bool(manifest_contents.get("with_files"))

    summary, issues, destructive = _build_import_plan(
        client,
        pod_id=pod_id,
        source_dir=source_dir,
        upsert=upsert,
    )
    # Surface what the bundle still needs from the importer. Members default to
    # the importing user, so only connectors/variables bound to an unsupplied
    # variable block. This turns the old silent placeholder-drop into an explicit
    # gate (override with allow_unresolved / --defer to import and wire up later).
    from .pod_requirements import unresolved_requirements

    unresolved = unresolved_requirements(
        source_dir, supplied_vars=set(variables or {})
    )
    if dry_run:
        return {
            "ok": len(issues) == 0,
            "dry_run": True,
            "pod_id": pod_id,
            "source_dir": str(source_dir),
            "summary": summary,
            "unresolved": unresolved,
            "destructive": destructive,
            "errors": [{"path": issue.path, "message": issue.message} for issue in issues],
        }
    if issues:
        rendered = "\n".join(f"- {issue.path}: {issue.message}" for issue in issues)
        raise ValueError(f"Bundle validation failed:\n{rendered}")
    if unresolved and not allow_unresolved:
        raise ValueError(_format_unresolved_message(unresolved))

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
