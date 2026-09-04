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
#
# The `X as X` spellings are the explicit re-export form: those names exist for
# importers of this module and are not used in the body, so without it they read
# as dead imports and `make lint` fails on the whole file.
from lemma_pod_bundle.diff import (
    TableDiff as TableDiff,
    _is_system_table_column as _is_system_table_column,
    _normalize_column_for_diff as _normalize_column_for_diff,
    _order_table_dirs_by_dependency,
    _table_fk_dependencies as _table_fk_dependencies,
    diff_table_columns,
)
from lemma_pod_bundle.jsonc import (
    _strip_trailing_commas as _strip_trailing_commas,
    loads_jsonc as loads_jsonc,
    strip_jsonc as strip_jsonc,
)
from lemma_pod_bundle.layout import (
    APP_MANIFEST_ALIAS as APP_MANIFEST_ALIAS,
    EXPORTABLE_RESOURCE_DIRS,
    FILES_MANIFEST,
    FORMAT_VERSION as FORMAT_VERSION,
    JSON_FILE_REF_KEY as JSON_FILE_REF_KEY,
    POD_MEMBER_TOKEN,
    RAW_FILE_REF_KEY,
    RESOURCE_DIR_ALIASES as RESOURCE_DIR_ALIASES,
    RESOURCE_DIRS,
    SYSTEM_TABLE_COLUMNS as SYSTEM_TABLE_COLUMNS,
    TABLE_DATA_FILE,
    _TABLE_DATA_CANDIDATES,
    _bundle_folder_keys,
    _file_path_key,
    _json_dump as _json_dump,
    _looks_like_single_resource_dir,
    _parse_function_headers as _parse_function_headers,
    _read_export_contents,
    _read_json,
    _record_export_contents,
    _resolve_file_refs as _resolve_file_refs,
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
    _normalize_resource_permissions_payload as _normalize_resource_permissions_payload,
    _normalize_schedule_payload,
    _normalize_surface_payload,
    _normalize_table_payload,
    _normalize_workflow_payload,
    _sanitize_app_payload_for_import,
    _sanitize_function_payload_for_import,
    _sanitize_table_payload_for_import,
    _split_resource_permissions_payload,
    _strip_keys,
    _surface_name_from_payload,
    _surface_platform_from_payload,
    _validate_function_payload,
)
from lemma_pod_bundle.portability import (
    _ACCOUNT_REF_FIELDS as _ACCOUNT_REF_FIELDS,
    _MEMBER_REF_FIELDS as _MEMBER_REF_FIELDS,
    _PLACEHOLDER_RE,
    _extract_portable_variables,
    _placeholder,
    _slug_var_name as _slug_var_name,
    _strip_unresolved_placeholders,
    _tokenize_ref_fields as _tokenize_ref_fields,
    GRANT_METADATA_KEYS,
    require_account_variable_metadata,
)
from lemma_pod_bundle.apply_fields import SCHEDULE_APPLY_FIELDS, SURFACE_APPLY_FIELDS

from lemma_sdk import Lemma
from lemma_sdk.errors import LemmaAPIError
from lemma_sdk.openapi_client.models.add_column_request import AddColumnRequest
from lemma_sdk.openapi_client.models.agent_permissions_replace_request import (
    AgentPermissionsReplaceRequest,
)
from lemma_sdk.openapi_client.models.update import Update
from lemma_sdk.openapi_client.models.create_agent_request import CreateAgentRequest
from lemma_sdk.openapi_client.models.create_app_request import CreateAppRequest
from lemma_sdk.openapi_client.models.create_function_request import (
    CreateFunctionRequest,
)
from lemma_sdk.openapi_client.models.create_schedule_request import (
    CreateScheduleRequest,
)
from lemma_sdk.openapi_client.models.create_table_request import CreateTableRequest
from lemma_sdk.openapi_client.models.function_permissions_replace_request import (
    FunctionPermissionsReplaceRequest,
)
from lemma_sdk.openapi_client.models.pod_update_request import PodUpdateRequest
from lemma_sdk.openapi_client.models.update_agent_request import UpdateAgentRequest
from lemma_sdk.openapi_client.models.update_app_request import UpdateAppRequest
from lemma_sdk.openapi_client.models.update_function_request import (
    UpdateFunctionRequest,
)
from lemma_sdk.openapi_client.models.update_schedule_request import (
    UpdateScheduleRequest,
)
from lemma_sdk.openapi_client.models.update_table_request import UpdateTableRequest
from lemma_sdk.openapi_client.models.workflow_create_request import (
    WorkflowCreateRequest,
)
from lemma_sdk.openapi_client.models.workflow_update_request import (
    WorkflowUpdateRequest,
)
from ..cli_core.io import list_items, to_plain
from ..cli_core.payload import build_request
from ..cli_core.state import err_console as console
from .app_bundle import classify_app_source, deploy_app_bundle
from .enums import SURFACE_PLATFORMS
from lemma_pod_bundle.limits import (
    MAX_APP_BYTES,
    MAX_APPS_TOTAL_BYTES,
    MAX_DATA_TOTAL_BYTES,
    MAX_ITEM_BYTES,
    MAX_RECORDS_PER_TABLE,
    MAX_RECORDS_TOTAL,
)
from .record_io import (
    fetch_records_capped,
    read_record_rows,
    write_export_rows,
)

# Mirrors the backend's DESTRUCTIVE_ACTIONS (app/core/authorization/delegation.py):
# workloads may only perform these with an explicit grant (standing authority)
# or a per-conversation user approval. A bundle granting one is legitimate but
# worth surfacing at import time. (The bundle-format constants and TableDiff /
# BundleValidationIssue types live in the shared lemma_pod_bundle package and are
# imported above; only this advisory list is CLI-local.)
DESTRUCTIVE_PERMISSION_IDS = frozenset(
    {
        "pod.delete",
        "pod.role.manage",
        "pod.member.manage",
        "datastore.table.delete",
        "folder.delete",
        "function.delete",
        "agent.delete",
        "workflow.delete",
        "app.delete",
        "schedule.delete",
        "connector_account.manage",
    }
)


class _RowBudget:
    """Per-table + running-total row cap for seed data — the same numbers the
    server enforces (``lemma_pod_bundle.limits``), so CLI and API bundles are
    bounded identically."""

    def __init__(self) -> None:
        self.remaining = MAX_RECORDS_TOTAL

    def table_cap(self) -> int:
        return max(0, min(MAX_RECORDS_PER_TABLE, self.remaining))

    def consume(self, rows: int) -> None:
        self.remaining -= max(0, rows)


class _ByteBudget:
    """Per-item + running-total byte cap. ``allow`` warns + returns False for an
    oversized/over-budget item, else reserves its bytes."""

    def __init__(self, *, per_item: int, total: int, warnings: list[str]) -> None:
        self.per_item = per_item
        self.remaining = total
        self.warnings = warnings

    def allow(self, *, name: str, size: int) -> bool:
        if size > self.per_item:
            self.warnings.append(
                f"'{name}' skipped: {size} bytes exceeds the per-item limit "
                f"({self.per_item} bytes)"
            )
            return False
        if size > self.remaining:
            self.warnings.append(f"'{name}' skipped: export size budget reached")
            return False
        self.remaining -= size
        return True


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
    return subprocess.run(
        command, cwd=cwd, check=True, text=True, capture_output=True, env=env
    )


def _detect_package_manager(source_dir: Path) -> str:
    if (source_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (source_dir / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _install_command_for_package_manager(
    source_dir: Path, package_manager: str
) -> list[str]:
    if package_manager == "npm":
        if (source_dir / "package-lock.json").exists():
            return ["npm", "ci"]
        return ["npm", "install"]
    if package_manager == "pnpm":
        return (
            ["pnpm", "install", "--frozen-lockfile"]
            if (source_dir / "pnpm-lock.yaml").exists()
            else ["pnpm", "install"]
        )
    if package_manager == "yarn":
        return (
            ["yarn", "install", "--frozen-lockfile"]
            if (source_dir / "yarn.lock").exists()
            else ["yarn", "install"]
        )
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
        raise ValueError(
            f"App bundle is missing both source/ and dist.zip in {resource_dir}"
        )

    package_json = source_dir / "package.json"
    if not package_json.exists():
        raise ValueError(f"App source is missing package.json: {package_json}")

    package_manager = _detect_package_manager(source_dir)
    install_command = _install_command_for_package_manager(source_dir, package_manager)

    console.print(
        f"[cyan]app[/cyan] building {resource_dir.name}: {' '.join(install_command)}"
    )
    try:
        _run_command(install_command, cwd=source_dir, stream_output=stream_output)
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or exc.stdout or str(exc)
        raise ValueError(
            f"{' '.join(install_command)} failed for app {resource_dir.name}: {details}"
        ) from exc

    build_command = [package_manager, "run", "build"]
    console.print(
        f"[cyan]app[/cyan] building {resource_dir.name}: {' '.join(build_command)}"
    )
    try:
        _run_command(build_command, cwd=source_dir, stream_output=stream_output)
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or exc.stdout or str(exc)
        raise ValueError(
            f"{' '.join(build_command)} failed for app {resource_dir.name}: {details}"
        ) from exc

    dist_dir = source_dir / "dist"
    if not (dist_dir / "index.html").exists():
        raise ValueError(
            f"App build did not produce dist/index.html for {resource_dir.name}"
        )

    _archive_dist_directory(dist_dir, dist_file)
    console.print(
        f"[green]app[/green] built {resource_dir.name}: wrote dist.zip from source/dist/"
    )
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


def _export_table_data(
    pod_sdk: Any,
    table_name: str,
    resource_dir: Path,
    *,
    row_budget: _RowBudget,
    data_budget: _ByteBudget,
    warnings: list[str],
) -> None:
    """Dump a table's rows to ``data.csv`` under the shared row + byte budgets
    (skipped when empty, over budget, or the row total is spent). Warns on any
    truncation so the caller knows rows were dropped."""
    cap = row_budget.table_cap()
    if cap <= 0:
        warnings.append(
            f"table '{table_name}' seed data omitted: export record budget reached"
        )
        return
    rows = fetch_records_capped(pod_sdk, table_name, cap)
    if not rows:
        return
    dest = resource_dir / TABLE_DATA_FILE
    write_export_rows(dest, rows, "csv")
    size = dest.stat().st_size
    if not data_budget.allow(name=f"tables/{table_name}/data.csv", size=size):
        # Over the per-item or shared data budget → don't ship it.
        dest.unlink(missing_ok=True)
        return
    row_budget.consume(len(rows))
    if len(rows) >= cap:
        warnings.append(
            f"table '{table_name}': exported the first {len(rows)} rows (cap); "
            f"any beyond that were skipped."
        )


def _has_seed_data(resource_dir: Path) -> bool:
    """True when a table dir carries a `data.{csv,jsonl,json}` file to seed from."""
    return any((resource_dir / name).is_file() for name in _TABLE_DATA_CANDIDATES)


def _rows_typed_by_schema(
    rows: list[dict[str, Any]], resource_dir: Path
) -> list[dict[str, Any]]:
    """Re-read CSV cells as the columns they are going into.

    CSV has no types, so the reader guesses from the text -- and a TEXT column
    holding "2026" arrives as an int, which Postgres rejects outright:
    `invalid input for query argument $13: 2026 (expected str, got int)`. The
    export is not wrong and the CSV is not wrong; the guess is. A year, a
    postcode, an ISBN, a version, an account number: every one of them is text
    that looks like a number, and any of them stops a pod from being imported.

    The declared type is right there in the table's own JSON, next to the data
    file, so use it. Only the columns the table declares are touched, and only
    where the guess disagrees; anything unrecognised is left exactly as read.
    """
    schema_files = [
        path for path in resource_dir.glob("*.json") if path.stem == resource_dir.name
    ]
    if not schema_files:
        return rows
    try:
        columns = json.loads(schema_files[0].read_text(encoding="utf-8")).get("columns")
    except OSError, json.JSONDecodeError:
        return rows
    if not isinstance(columns, list):
        return rows
    declared = {
        str(column.get("name")): str(column.get("type") or "").upper()
        for column in columns
        if isinstance(column, dict) and column.get("name")
    }
    # Types whose values are carried as strings. A number that reached a TEXT
    # column is the guess misfiring, so put it back the way the CSV spelled it.
    textual = {"TEXT", "ENUM", "UUID", "FILE_PATH", "DATE", "DATETIME", "TIME"}
    retyped: list[dict[str, Any]] = []
    for row in rows:
        fixed = dict(row)
        for key, value in row.items():
            if declared.get(key) in textual and isinstance(value, (int, float)):
                fixed[key] = str(value)
        retyped.append(fixed)
    return retyped


def _import_table_data(pod_sdk: Any, table_name: str, resource_dir: Path) -> int:
    """Seed a table from a bundled ``data.{csv,jsonl,json}`` file via bulk create.
    Returns the number of rows sent (0 when there is no data file)."""
    data_file = next(
        (
            resource_dir / name
            for name in _TABLE_DATA_CANDIDATES
            if (resource_dir / name).is_file()
        ),
        None,
    )
    if data_file is None:
        return 0
    rows = [
        {key: value for key, value in row.items() if key not in _SEED_STRIP_COLUMNS}
        for row in read_record_rows(data_file, None)
    ]
    if data_file.suffix.lower() == ".csv":
        rows = _rows_typed_by_schema(rows, resource_dir)
    if not rows:
        return 0
    # Upsert so re-importing an edited data.csv updates existing rows (matched on
    # the table's primary key) instead of failing — idempotent re-seeding.
    pod_sdk.records.bulk_create(table_name, rows, upsert=True)
    return len(rows)


def _surface_upsert_body(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in SURFACE_APPLY_FIELDS if key in payload}


def _resolve_account_connector_info(client: Lemma, account_id: str) -> tuple[str, str]:
    """Ground-truth ``(connector_id, kind)`` for an account, resolved via the
    connectors API — never inferred from a resource's own name, since that
    guess is wrong for any resource type with no platform concept of its own
    (e.g. a schedule). Raises if the account (or its kind) can't be resolved,
    since a bundle can't be tokenized without this metadata."""
    try:
        account = to_plain(client.connectors.accounts.get(account_id))
    except LemmaAPIError as exc:
        raise ValueError(
            f"Could not resolve connector info for account {account_id}: {exc}"
        ) from exc
    connector_id = account.get("connector_id")
    kind = account.get("kind")
    if not connector_id or not kind:
        raise ValueError(
            f"Account {account_id} is missing connector_id/kind info — "
            "upgrade the backend or lemma-sdk before exporting connector-bound "
            "resources."
        )
    return str(connector_id), str(kind)


def _stamp_account_grant_metadata(
    client: Lemma, permissions: dict[str, Any]
) -> dict[str, Any]:
    """Attach ``connector_id``/``connector_kind`` to every ``connector_account``
    grant, resolved from the account itself.

    A pinned-account grant names the account by its raw id (the backend has no
    other identifier for one), which is meaningless in any other org. The
    portable-variable pass turns that id into a ``${var}`` — but only if the
    grant carries the connector metadata the variable must record, and grants
    come back from the permissions API without it. Stamping it here is the
    exact mirror of what the exporter already does beside a surface's or
    schedule's ``account_id``.
    """
    grants = permissions.get("grants")
    if not isinstance(grants, list):
        return permissions
    stamped: list[Any] = []
    for grant in grants:
        if (
            not isinstance(grant, dict)
            or str(grant.get("resource_type") or "") != "connector_account"
            or not grant.get("resource_name")
        ):
            stamped.append(grant)
            continue
        connector_id, connector_kind = _resolve_account_connector_info(
            client, str(grant["resource_name"])
        )
        stamped.append(
            {
                **grant,
                "connector_id": connector_id,
                "connector_kind": connector_kind,
            }
        )
    return {**permissions, "grants": stamped}


def _stamp_cli_account_defaults(bundle_root: Path, variables: dict[str, Any]) -> None:
    """CLI-only convenience: record each account variable's source account id
    as its ``default``, so re-importing into the same org (a common local dev
    loop — export, tweak, reimport) doesn't require ``--var`` every time. A
    backend/UI-triggered export never does this — the importer there must
    always be prompted to supply their own account."""
    changed = False
    for meta in variables.values():
        if str((meta or {}).get("type") or "") == "account" and not meta.get("default"):
            meta["default"] = meta["source_value"]
            changed = True
    if not changed:
        return
    pod_path = bundle_root / "pod.json"
    pod_data = _read_json(pod_path)
    pod_data["variables"] = variables
    _write_json(pod_path, pod_data)


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
    unresolved. ``pod_member`` variables default to the importing user; an
    account variable falls back to its recorded ``default`` (the source
    account id, stamped by this CLI's own export for same-org re-import
    convenience) once that account is verified reachable, otherwise it must be
    supplied via ``--var``/``--values`` or is left unresolved.
    """
    from .scaffold import substitute_placeholders

    pod_path = source_dir / "pod.json"
    declared = (
        (_read_json(pod_path).get("variables") or {}) if pod_path.is_file() else {}
    )
    try:
        require_account_variable_metadata(declared)
    except ValueError as exc:
        raise ValueError(
            f"{exc} Re-export this bundle with a newer lemma-cli."
        ) from exc
    overrides = dict(var_overrides or {})
    unknown = sorted(set(overrides) - set(declared))
    if unknown:
        raise ValueError(
            f"Unknown --var name(s): {', '.join(unknown)}. "
            f"This bundle declares: {', '.join(sorted(declared)) or '(none)'}."
        )
    member_cache: list[str] = []
    account_default_cache: dict[str, str | None] = {}

    def member_default() -> str:
        if not member_cache:
            member_cache.append(
                _resolve_import_pod_member_id(client, pod_sdk, member_override)
            )
        return member_cache[0]

    def verified_account_default(name: str, spec: dict[str, Any]) -> str | None:
        """Never blindly reuse a bundle's recorded source account id: confirm
        it still exists (and is reachable to the current session) before
        treating it as resolved, since a stale/foreign id would otherwise
        silently bind the wrong account."""
        if name not in account_default_cache:
            default_id = spec.get("default")
            resolved: str | None = None
            if default_id:
                try:
                    client.connectors.accounts.get(str(default_id))
                except LemmaAPIError as exc:
                    # A 404/403 means the recorded source account isn't ours to
                    # reuse — leave the variable unresolved. But a 429 (rate
                    # limited) or 5xx is transient/systemic: treating it as
                    # "account gone" would silently drop the binding and import a
                    # half-wired resource, so surface it instead.
                    if getattr(exc, "status_code", None) in (403, 404):
                        resolved = None
                    else:
                        raise
                else:
                    resolved = str(default_id)
            account_default_cache[name] = resolved
        return account_default_cache[name]

    def apply(
        payload: dict[str, Any], *, strip_unresolved: bool = True
    ) -> dict[str, Any]:
        """Substitute every resolvable placeholder.

        ``strip_unresolved`` drops the fields still holding a ``${...}`` token so
        a literal placeholder never reaches the API — right for a resource body,
        wrong for a grant, where dropping the ``resource_name`` key would turn an
        unresolved account into a confusing "grants must reference resources by
        resource_name" failure instead of a skipped grant. Grants therefore ask
        for the un-stripped form and decide for themselves (see
        ``_resolve_grant_permissions``).
        """
        serialized = json.dumps(payload)
        replacements: dict[str, str] = {}
        if POD_MEMBER_TOKEN in serialized:
            replacements[POD_MEMBER_TOKEN] = member_default()
        for name, spec in declared.items():
            token = _placeholder(name)
            if token not in serialized:
                continue
            vtype = str((spec or {}).get("type") or "")
            if name in overrides:
                replacements[token] = overrides[name]
            elif vtype == "pod_member":
                replacements[token] = member_default()
            elif vtype == "account":
                default_value = verified_account_default(name, spec or {})
                if default_value is not None:
                    replacements[token] = default_value
        if replacements:
            payload = substitute_placeholders(payload, replacements)
        return _strip_unresolved_placeholders(payload) if strip_unresolved else payload

    return apply


def _resolve_grant_permissions(
    apply_variables: Any,
    permissions_payload: dict[str, Any] | None,
    *,
    kind: str,
    name: str,
) -> dict[str, Any] | None:
    """Resolve a resource's grants for the target pod.

    Three things happen here, all of which used to be missing:

    * ``${var}`` placeholders resolve (grants never saw the variable applier at
      all, because it was built after the agent/function loops).
    * A ``connector_account`` grant whose account variable stayed unresolved is
      DROPPED with a warning. Sending it would 400 the whole permissions pass —
      at the very end of an import that has already written everything else,
      leaving a half-wired pod. A missing pinned account is a
      reconnect-after-import chore, not a reason to fail.
    * Export-only connector metadata is stripped back off.
    """
    if permissions_payload is None:
        return None
    resolved = apply_variables(permissions_payload, strip_unresolved=False)
    kept: list[Any] = []
    dropped: list[str] = []
    for grant in resolved.get("grants") or []:
        if not isinstance(grant, dict):
            kept.append(grant)
            continue
        resource_name = grant.get("resource_name")
        if isinstance(resource_name, str) and _PLACEHOLDER_RE.fullmatch(resource_name):
            dropped.append(f"{grant.get('resource_type')} {resource_name}")
            continue
        kept.append(_strip_keys(grant, set(GRANT_METADATA_KEYS)))
    if dropped:
        console.print(
            f"[yellow]warning[/yellow] {kind} '{name}': dropped "
            f"{len(dropped)} grant(s) whose account variable was not supplied "
            f"({', '.join(dropped)}). Connect the account in this pod, then run "
            f"`lemma {kind}s permissions add {name} account:<id>:use`."
        )
    return {"grants": kept}


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


def _collapse_single_file_app_source(resource_dir: Path) -> None:
    """Rewrite a one-file `source/index.html` app back to `html.html`.

    A no-build app uploads the same archive as source and dist, so its source
    comes back as a lone `index.html`. `html.html` is the form the author wrote
    and the form the docs describe, and it survives a re-export unchanged — a
    round trip should hand back the file you edit, not a directory pretending
    to be a project.
    """
    source_dir = resource_dir / "source"
    files = [path for path in source_dir.rglob("*") if path.is_file()]
    if len(files) != 1 or files[0].name != "index.html":
        return
    (resource_dir / "html.html").write_text(
        files[0].read_text(encoding="utf-8"), encoding="utf-8"
    )
    shutil.rmtree(source_dir)


def _download_app_assets(
    client: Lemma,
    pod_id: str,
    app_name: str,
    resource_dir: Path,
    *,
    app_budget: _ByteBudget,
) -> None:
    """Download an app's source AND its build.

    Both, not one. Source alone meant every import rebuilt in a sandbox; the
    build alone -- the fallback for an app with no source archive -- shipped a
    bundle whose code was gone. Mirrors the backend exporter's
    ``_export_app_assets``; the two must stay in step or an export round-trips
    differently depending on which one produced it.
    """
    pod_sdk = client.pod(pod_id)
    try:
        archive_bytes = pod_sdk.apps.download_source_archive(app_name)
    except LemmaAPIError:
        archive_bytes = b""
    if archive_bytes:
        if not app_budget.allow(
            name=f"apps/{app_name}/source", size=len(archive_bytes)
        ):
            return
        source_dir = resource_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        with ZipFile(io.BytesIO(archive_bytes)) as archive:
            for member in archive.infolist():
                target = source_dir / member.filename
                if not target.resolve().is_relative_to(source_dir.resolve()):
                    raise ValueError(
                        f"Unsafe path in app source archive for {app_name}: {member.filename}"
                    )
            archive.extractall(source_dir)
        _collapse_single_file_app_source(resource_dir)
        if (resource_dir / "html.html").is_file():
            return

    try:
        dist_archive = pod_sdk.apps.download_dist_archive(app_name)
    except LemmaAPIError:
        dist_archive = b""
    if dist_archive and app_budget.allow(
        name=f"apps/{app_name}/dist.zip", size=len(dist_archive)
    ):
        (resource_dir / "dist.zip").write_bytes(dist_archive)
        # Whether the importer may deploy this build as-is or must rebuild it.
        from lemma_pod_bundle import dist_is_portable

        (resource_dir / "dist.json").write_text(
            json.dumps(
                {"portable": dist_is_portable(dist_archive, pod_id=pod_id)}, indent=2
            )
            + "\n"
        )


def _normalize_file_folders(file_folders: list[str] | None) -> list[str]:
    """Folder paths normalized to a leading slash, de-duplicated, order kept.

    ``/`` is dropped: it is the whole file tree, which is exactly what naming
    folders exists to avoid.
    """
    if not file_folders:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in file_folders:
        if not raw or not raw.strip():
            continue
        path = "/" + raw.strip().strip("/")
        if path == "/" or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _is_pod_visible_file(item: dict[str, Any]) -> bool:
    return str(item.get("visibility") or "").upper() == "POD"


def fetch_files_index(
    client: Lemma, pod_id: str
) -> tuple[dict[str | None, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Every file and folder in the pod, by walking directories to exhaustion.

    This used to read the directory-*tree* endpoint, which is a preview: it
    returns at most ``files_per_directory`` entries per directory, defaulting to
    three and capped at twenty. Export then treated that preview as the pod's
    contents. On a real pod that meant a directory of twelve files exported
    three of them, and the summary counted what it collected, so the number
    looked right and nothing warned. Measured on one pod: 266 files, 104
    exported, no error.

    The backend's own exporter already avoids this and says why -- "the tree
    endpoint caps files-per-dir, so we walk with ``list_files`` instead". This
    is the same walk, and it inherits the same hazard the backend documents: a
    listing is not guaranteed to point strictly downward, because a user's home
    folder lists *itself*. Without ``seen`` that recurses until the interpreter
    gives up.
    """
    by_parent: dict[str | None, list[dict[str, Any]]] = {None: []}
    all_items: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    stack: list[tuple[str, str | None]] = [("/", None)]

    while stack:
        directory, parent_key = stack.pop()
        if directory in seen:
            continue
        seen.add(directory)
        for entry in client.pod(pod_id).files.list_all(directory):
            item = to_plain(entry)
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            if not path or path == "/":
                continue
            item_id = str(item.get("id") or path)
            item["id"] = item_id
            all_items[item_id] = item
            by_parent.setdefault(parent_key, []).append(item)
            if str(item.get("kind") or "").upper() == "FOLDER":
                stack.append((path, path))

    return by_parent, all_items


def _under_named_folder(path: str, folders: list[str]) -> bool:
    """Is ``path`` one of the named folders, or inside one of them?"""
    return any(path == folder or path.startswith(f"{folder}/") for folder in folders)


def _export_pod_files(
    client: Lemma,
    pod_id: str,
    bundle_root: Path,
    *,
    file_folders: list[str] | None = None,
    warnings: list[str] | None = None,
    data_budget: _ByteBudget | None = None,
) -> dict[str, int]:
    """Export the named folders and everything beneath them.

    Selection is by name only: there is no "every folder" switch, so a pod's
    private files cannot ride along in a bundle nobody meant to put them in.
    """
    files_root = bundle_root / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    folders = _normalize_file_folders(file_folders)
    notes = warnings if warnings is not None else []
    if not folders:
        return {"folders": 0, "files": 0}
    _, all_items = fetch_files_index(client, pod_id)

    known_folders = {
        str(item.get("path") or "")
        for item in all_items.values()
        if str(item.get("kind") or "").upper() == "FOLDER"
    }
    for folder in folders:
        if folder not in known_folders:
            notes.append(
                f"folder '{folder}' requested for export but not found in the "
                f"pod; skipped"
            )

    pod_items = {
        item_id: item
        for item_id, item in all_items.items()
        if _is_pod_visible_file(item)
        and _under_named_folder(str(item.get("path") or ""), folders)
    }
    folder_count = 0
    file_count = 0
    file_manifest: list[dict[str, Any]] = []

    def export_folder(item: dict[str, Any]) -> None:
        nonlocal folder_count
        relative_parts = [
            part for part in str(item.get("path") or "").split("/") if part
        ]
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
        # File bytes draw from the shared data budget (same caps as the server).
        if data_budget is not None and not data_budget.allow(
            name=f"files{path}", size=len(content)
        ):
            console.print(
                f"[yellow]warning[/yellow] file '{path}': exceeds the export size "
                f"budget; skipped."
            )
            return
        target_path = files_root.joinpath(*relative_parts)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)
        entry: dict[str, Any] = {
            "path": path,
            "description": item.get("description"),
            "visibility": item.get("visibility"),
        }
        # The directory tree carries no `search_enabled` (DirectoryTreeNode has
        # path/name/kind/visibility/children and nothing else), so reading it
        # from the tree item recorded a null for every file — and the importer's
        # `bool(entry.get("search_enabled", True))` then read that null as False,
        # because the key was present. Every round-tripped document came back
        # unindexed and the copied pod's search silently returned nothing. Ask
        # the file itself; omit the key entirely when we can't, so the importer's
        # default applies instead of a null.
        if "search_enabled" in item:
            entry["search_enabled"] = item["search_enabled"]
        else:
            try:
                stat = to_plain(client.pod(pod_id).files.get(path))
            except Exception:  # noqa: BLE001 — fall back to the import default
                stat = {}
            if stat.get("search_enabled") is not None:
                entry["search_enabled"] = stat["search_enabled"]
        file_manifest.append(entry)
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
        else:
            export_file(item)

    if file_manifest:
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
    data_tables: set[str] | None = None,
    file_folders: list[str] | None = None,
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
    # Row data is seeded only for the tables named here, and files only for the
    # folders named there. Neither has an "everything" switch. Independent of
    # `names` (which scopes whole resources).
    seed_tables = {
        name.strip() for name in (data_tables or set()) if name and name.strip()
    }
    wants_data = bool(seed_tables)
    data_table_warnings: list[str] = []
    export_warnings = data_table_warnings
    # Same caps the server enforces (lemma_pod_bundle.limits): data + files share
    # one byte pool; app builds get their own.
    row_budget = _RowBudget()
    data_budget = _ByteBudget(
        per_item=MAX_ITEM_BYTES,
        total=MAX_DATA_TOTAL_BYTES,
        warnings=data_table_warnings,
    )
    app_budget = _ByteBudget(
        per_item=MAX_APP_BYTES, total=MAX_APPS_TOTAL_BYTES, warnings=data_table_warnings
    )

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
        exported_table_names: set[str] = set()
        for table in sorted(tables, key=lambda item: str(item.get("name", ""))):
            table_name = str(table.get("name") or "")
            exported_table_names.add(table_name)
            resource_dir = bundle_root / "tables" / table_name
            resource_dir.mkdir(parents=True, exist_ok=True)
            full_table = to_plain(pod_sdk.tables.get(table_name))
            _write_json(
                resource_dir / f"{table_name}.json",
                _normalize_table_payload(full_table),
            )
            if table_name in seed_tables:
                _export_table_data(
                    pod_sdk,
                    table_name,
                    resource_dir,
                    row_budget=row_budget,
                    data_budget=data_budget,
                    warnings=data_table_warnings,
                )
        for missing in sorted(seed_tables - exported_table_names):
            data_table_warnings.append(
                f"data-table '{missing}' is not a table in this pod; skipped"
            )

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
            function_permissions = _stamp_account_grant_metadata(
                client, to_plain(pod_sdk.functions.permissions(function_name))
            )
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
            agent_permissions = _stamp_account_grant_metadata(
                client, to_plain(pod_sdk.agents.permissions(agent_name))
            )
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
            account_id = full_schedule.get("account_id")
            if account_id:
                connector_id, connector_kind = _resolve_account_connector_info(
                    client, str(account_id)
                )
                full_schedule["connector_id"] = connector_id
                full_schedule["connector_kind"] = connector_kind
            _write_json(
                resource_dir / f"{schedule_name}.json",
                _normalize_schedule_payload(full_schedule),
            )

    surfaces: list[dict[str, Any]] = []
    if should_export("surfaces"):
        # Keyed by the surface's pod-unique name. Deduping by platform here is
        # what silently dropped every surface after the first Slack one.
        seen_names: set[str] = set()
        for surface in list_items(pod_sdk.surfaces.list(limit=100)):
            raw_surface = to_plain(surface)
            account_id = raw_surface.get("account_id")
            if account_id:
                connector_id, connector_kind = _resolve_account_connector_info(
                    client, str(account_id)
                )
                raw_surface["connector_id"] = connector_id
                raw_surface["connector_kind"] = connector_kind
            payload = _normalize_surface_payload(raw_surface)
            platform = str(payload.get("platform") or "")
            surface_key = str(payload["name"]).lower()
            if not platform or surface_key in seen_names:
                continue
            if not should_export_name(surface, payload["name"]):
                continue
            seen_names.add(surface_key)
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
            _write_json(
                resource_dir / f"{app_name}.json", _normalize_app_payload(full_app)
            )
            _download_app_assets(
                client, pod_id, app_name, resource_dir, app_budget=app_budget
            )

    file_counts = {"folders": 0, "files": 0}
    if should_export("files"):
        file_counts = _export_pod_files(
            client,
            pod_id,
            bundle_root,
            file_folders=file_folders,
            warnings=export_warnings,
            data_budget=data_budget,
        )

    # Replace non-portable member/account ids with ${name} variables recorded in
    # pod.json, so the bundle can be re-imported into another pod/org.
    variables = _extract_portable_variables(bundle_root)
    _stamp_cli_account_defaults(bundle_root, variables)

    # Record what this bundle carries (selective scope + whether row data / file
    # bytes were captured) so a re-import seeds them automatically and a re-export
    # can refresh exactly this set.
    _record_export_contents(
        bundle_root,
        included=included,
        excluded=excluded,
        names=selected_names,
        with_data=wants_data,
        with_files=bool(file_folders),
    )

    # Printed, not just returned. Every one of these says something was left
    # out of the bundle, and they were reaching the caller only inside a summary
    # panel that folds long fields -- so the one line saying a file had been
    # skipped was the line most likely to be hidden. The import path already
    # prints its advisories this way.
    for warning in data_table_warnings:
        console.print(f"[yellow]warning[/yellow] {warning}")

    return {
        "ok": True,
        "path": str(bundle_root),
        "pod_id": pod_id,
        "pod_name": pod_name,
        "excluded": sorted(excluded),
        "included": sorted(included),
        "names": sorted(selected_names),
        "data_tables": sorted(seed_tables),
        "warnings": data_table_warnings,
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
        if (
            _resource_manifest_path(path, path.name, resource_type=resource_type)
            is not None
        ):
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


def _build_existing_schedule_map(
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
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
) -> tuple[dict[str, list[str]], list[BundleValidationIssue], list[str]]:
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
                issues.append(
                    BundleValidationIssue(path=str(resource_dir), message=str(exc))
                )
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
            manifest_path = str(resource_dir / f"{resource_name}.json")
            # The same unknown-field check the apply step enforces, run here so a
            # typo fails the plan instead of aborting a half-written import.
            # `permissions` is split out before any request is built, so it is
            # excluded from the comparison rather than reported.
            issues.extend(
                _validate_payload_fields(
                    resource_type,
                    resource_name,
                    _strip_keys(payload, {"permissions"}),
                    manifest_path,
                )
            )
            if resource_type == "functions":
                issues.extend(
                    _validate_function_payload(resource_dir, resource_name, payload)
                )
            if resource_type == "schedules":
                if not payload.get("name"):
                    payload["name"] = resource_name
                issues.extend(
                    _validate_schedule_config(payload, resource_name, manifest_path)
                )
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
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir),
                        message=f"Table already exists: {table_name}",
                    )
                )
        else:
            summary["tables"].append(f"created:{table_name}")

    existing_functions = _build_existing_map(
        list_items(pod_sdk.functions.list(limit=1000))
    )
    for resource_dir in _resource_dirs(source_dir, "functions"):
        function_name = resource_dir.name
        if function_name in existing_functions:
            if upsert:
                summary["functions"].append(f"updated:{function_name}")
            else:
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir),
                        message=f"Function already exists: {function_name}",
                    )
                )
        else:
            summary["functions"].append(f"created:{function_name}")

    existing_agents = _build_existing_map(list_items(pod_sdk.agents.list(limit=1000)))
    for resource_dir in _resource_dirs(source_dir, "agents"):
        agent_name = resource_dir.name
        if agent_name in existing_agents:
            if upsert:
                summary["agents"].append(f"updated:{agent_name}")
            else:
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir),
                        message=f"Agent already exists: {agent_name}",
                    )
                )
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
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir),
                        message=f"Workflow already exists: {workflow_name}",
                    )
                )
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
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir),
                        message=f"Schedule already exists: {schedule_name}",
                    )
                )
        else:
            summary["schedules"].append(f"created:{schedule_name}")

    surface_dirs = _resource_dirs(source_dir, "surfaces")
    # Surfaces are addressed by their pod-unique name (defaulting to the
    # lowercased platform), the same key the server-side applier upserts on.
    existing_surface_names = (
        {
            _surface_name_from_payload(to_plain(item), "")
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
        surface_key = _surface_name_from_payload(payload, surface_name)
        if surface_key in existing_surface_names:
            if upsert:
                summary["surfaces"].append(f"updated:{surface_name}")
            else:
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir),
                        message=f"Surface already exists: {surface_key}",
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
            source_subdir = resource_dir / "source"
            if source_subdir.exists():
                # Classify exactly as the deploy path does. Only a Vite project
                # gets built here (a dry run should catch a broken build); a
                # no-build source is uploaded as-is, so demanding a package.json
                # rejected the very bundles export writes.
                if classify_app_source(source_subdir) == "vite":
                    _build_app_bundle(
                        resource_dir,
                        stream_output=False,
                    )
        except ValueError as exc:
            issues.append(
                BundleValidationIssue(path=str(resource_dir), message=str(exc))
            )
            continue
        if app_name in existing_apps:
            if upsert:
                summary["apps"].append(f"updated:{app_name}")
            else:
                issues.append(
                    BundleValidationIssue(
                        path=str(resource_dir),
                        message=f"App already exists: {app_name}",
                    )
                )
        else:
            summary["apps"].append(f"created:{app_name}")

    files_root = source_dir / "files"
    existing_folder_map = _build_existing_folder_map(
        _list_pod_visible_items(client, pod_id)
    )
    if files_root.exists():
        for folder_dir in sorted(
            [path for path in files_root.rglob("*") if path.is_dir()],
            key=lambda path: len(path.relative_to(files_root).parts),
        ):
            parts = list(folder_dir.relative_to(files_root).parts)
            if not parts:
                continue
            path_key = _file_path_key(parts)
            if path_key not in existing_folder_map:
                summary["files"].append(f"created-folder:{path_key}")

    _validate_grant_references(
        source_dir,
        issues,
        client=client,
        pod_sdk=pod_sdk,
        # Names already listed while building the plan; anything else the
        # validator needs is fetched lazily, only if a grant names that type.
        known={
            "datastore_table": set(existing_tables),
            "function": set(existing_functions),
            "agent": set(existing_agents),
        },
        valid_folder_keys=set(existing_folder_map) | _bundle_folder_keys(files_root),
    )

    # Which workloads this import will CREATE (as opposed to upsert) — a new
    # workload with no grants is inert, while an existing one keeps whatever it
    # already holds, so only the former is worth warning about.
    created_names = {
        kind: {
            entry.split(":", 1)[1]
            for entry in summary.get(kind, [])
            if entry.startswith("created:")
        }
        for kind in ("agents", "functions")
    }
    return (
        summary,
        issues,
        _collect_grant_advisories(source_dir, created_names=created_names),
    )


# Every key a resource type can legitimately carry, unioned across its create and
# update requests plus the fields the importer applies itself (a schedule's
# `is_active` rides a follow-up update; `permissions` is split out before the
# request is built). A key in neither is an authoring mistake whichever branch
# the import ends up taking, so the plan can say so without knowing yet.
def _accepted_bundle_fields(resource_type: str) -> frozenset[str] | None:
    from ..cli_core.payload import accepted_field_names

    models: dict[str, tuple[Any, ...]] = {
        "tables": (CreateTableRequest, UpdateTableRequest),
        "functions": (CreateFunctionRequest, UpdateFunctionRequest),
        "agents": (CreateAgentRequest, UpdateAgentRequest),
        "workflows": (WorkflowCreateRequest, WorkflowUpdateRequest),
        "schedules": (CreateScheduleRequest, UpdateScheduleRequest),
        "apps": (CreateAppRequest, UpdateAppRequest),
    }
    if resource_type not in models:
        return None
    accepted: set[str] = {"permissions"}
    for model in models[resource_type]:
        accepted |= accepted_field_names(model) or set()
    if resource_type == "workflows":
        accepted |= {"nodes", "edges"}  # applied via update_graph, not the request
    if resource_type == "schedules":
        accepted |= {"is_active", "connector_id", "connector_kind"}
    if resource_type == "apps":
        accepted |= {"html", "source"}
    return frozenset(accepted)


# Required `config` keys per schedule_type, from the backend's domain models
# (schedule/domain/schedule.py). The importer filters `config` through as an
# opaque blob, so a wrong key only surfaces as a 422 from the server — after the
# rest of the bundle has already been written.
_SCHEDULE_CONFIG_RULES: dict[str, tuple[str, ...]] = {
    "DATASTORE": ("table_name", "operations"),
    "WEBHOOK": ("source",),
}


def _validate_schedule_config(
    payload: dict[str, Any], resource_name: str, path: str
) -> list[BundleValidationIssue]:
    schedule_type = str(payload.get("schedule_type") or "").upper()
    config = payload.get("config")
    required = _SCHEDULE_CONFIG_RULES.get(schedule_type)
    issues: list[BundleValidationIssue] = []
    if schedule_type == "TIME":
        if isinstance(config, dict) and not (
            config.get("cron") or config.get("scheduled_at")
        ):
            issues.append(
                BundleValidationIssue(
                    path=path,
                    message=(
                        f"Schedule '{resource_name}' is TIME but its config sets "
                        "neither `cron` nor `scheduled_at`."
                    ),
                )
            )
        return issues
    if required is None:
        return issues
    if not isinstance(config, dict):
        return [
            BundleValidationIssue(
                path=path,
                message=(
                    f"Schedule '{resource_name}' is {schedule_type} but has no "
                    f"config. Required: {', '.join(required)}."
                ),
            )
        ]
    for field in required:
        if not config.get(field):
            issues.append(
                BundleValidationIssue(
                    path=path,
                    message=(
                        f"Schedule '{resource_name}' ({schedule_type}) is missing "
                        f"required config field `{field}`. The server rejects this "
                        f"with a 422 — required: {', '.join(required)}."
                    ),
                )
            )
    return issues


def _validate_payload_fields(
    resource_type: str, resource_name: str, payload: dict[str, Any], path: str
) -> list[BundleValidationIssue]:
    """Flag keys the API has no field for, at PLAN time.

    The apply step already refuses them, but it refuses mid-write: an import with
    no transactions had created eleven resources before dying on the twelfth.
    Dry-run is the only safety net there is, so it has to run the same check."""
    accepted = _accepted_bundle_fields(resource_type)
    if accepted is None:
        return []
    # Compare against what the import will actually send: a bundle exported by an
    # older CLI legitimately carries server-owned fields (`input_schema`,
    # `revision_hash`, an app's `url`) that the apply step strips. Those are not
    # authoring mistakes and must not be reported as such.
    sanitizers = {
        "functions": _sanitize_function_payload_for_import,
        "apps": _sanitize_app_payload_for_import,
    }
    sanitize = sanitizers.get(resource_type)
    if sanitize is not None:
        payload = sanitize(payload)
    unknown = sorted(key for key in payload if key not in accepted)
    if not unknown:
        return []
    singular = resource_type.rstrip("s")
    return [
        BundleValidationIssue(
            path=path,
            message=(
                f"Unrecognized field(s) on {singular} '{resource_name}': "
                f"{', '.join(unknown)}. The API has no such field, so they would "
                f"be dropped silently. Run `lemma {singular} schema` for the "
                "accepted shape."
            ),
        )
    ]


def _validate_grant_references(
    source_dir: Path,
    issues: list[BundleValidationIssue],
    *,
    client: Lemma,
    pod_sdk: Any,
    known: dict[str, set[str]],
    valid_folder_keys: set[str],
) -> None:
    """Fail the import plan up front if any agent/function grant references a
    resource that neither the bundle creates nor the pod already has — so a
    dangling grant never leaves a half-imported pod (grants apply last).

    A ``connector_account`` grant names a raw account id, so it can only be
    checked against the live API; an id the session can't reach is an error
    here rather than a 400 from the final permissions pass, after every other
    resource has already been written. A grant still holding a ``${var}``
    placeholder is left alone — the apply step resolves or drops it.
    ``connector`` grants name an org-global connector and are not pod-scoped, so
    they stay advisory."""
    # resource_type -> (bundle dir, how to list the pod's existing names). The
    # listing is deferred: a bundle whose grants never mention workflows must
    # not pay for a workflows.list, and the plan builder only fetched the ones
    # it needed for its own diff.
    _LISTERS = {
        "datastore_table": ("tables", lambda: pod_sdk.tables.list(limit=1000)),
        "function": ("functions", lambda: pod_sdk.functions.list(limit=1000)),
        "agent": ("agents", lambda: pod_sdk.agents.list(limit=1000)),
        "workflow": ("workflows", lambda: pod_sdk.workflows.list(limit=1000)),
        "schedule": ("schedules", lambda: pod_sdk.schedules.list(limit=1000)),
        "app": ("apps", lambda: pod_sdk.apps.list(limit=1000)),
    }
    resolved_targets: dict[str, set[str]] = {}

    def targets_for(rtype: str) -> set[str]:
        """Every name of this type the pod will have after the import: what it
        already holds, plus what this bundle creates."""
        if rtype not in resolved_targets:
            bundle_dir, lister = _LISTERS[rtype]
            names = set(known.get(rtype) or set())
            if rtype not in known:
                names |= {
                    str(item.get("name"))
                    for item in list_items(lister())
                    if isinstance(item, dict) and item.get("name")
                }
            resolved_targets[rtype] = names | {
                d.name for d in _resource_dirs(source_dir, bundle_dir)
            }
        return resolved_targets[rtype]

    account_cache: dict[str, bool] = {}

    def account_reachable(account_id: str) -> bool:
        if account_id not in account_cache:
            try:
                client.connectors.accounts.get(account_id)
            except LemmaAPIError as exc:
                # 403/404 means "not ours / not here" — a real plan error. A 429
                # or 5xx is transient; don't fail the plan on infrastructure.
                account_cache[account_id] = getattr(exc, "status_code", None) not in (
                    403,
                    404,
                )
            except Exception:  # noqa: BLE001 — a check we can't run isn't a finding
                # No accounts API on this client, or the lookup itself broke.
                # Treat as reachable: the apply step still surfaces a real
                # failure, and a validator that can't validate must not block.
                account_cache[account_id] = True
            else:
                account_cache[account_id] = True
        return account_cache[account_id]

    for kind in ("agents", "functions"):
        for resource_dir in _resource_dirs(source_dir, kind):
            try:
                _, permissions = _split_resource_permissions_payload(
                    load_resource_payload(
                        resource_dir, resource_dir.name, resource_type=kind
                    )
                )
            except Exception:
                continue  # payload errors are already reported elsewhere
            for grant in (permissions or {}).get("grants", []):
                if not isinstance(grant, dict):
                    continue
                rtype = str(grant.get("resource_type") or "")
                rname = str(grant.get("resource_name") or "")
                if not rname or _PLACEHOLDER_RE.fullmatch(rname):
                    continue
                if rtype in ("folder", "document"):
                    found = (
                        _file_path_key([part for part in rname.split("/") if part])
                        in valid_folder_keys
                    )
                    detail = (
                        "Add the folder to the bundle (export it with --with-files) "
                        "or drop the grant."
                    )
                elif rtype == "connector_account":
                    found = account_reachable(rname)
                    detail = (
                        "A pinned-account grant names a connector account id, which "
                        "is specific to the org that exported it. Connect an account "
                        "in this org and pass its id via --var, or drop the grant to "
                        "use the invoking user's own account."
                    )
                elif rtype in _LISTERS:
                    found = rname in targets_for(rtype)
                    detail = "Add the resource to the bundle or drop the grant."
                else:
                    continue  # e.g. connector — org-global, not validatable here
                if not found:
                    issues.append(
                        BundleValidationIssue(
                            path=str(resource_dir / f"{resource_dir.name}.json"),
                            message=(
                                f"Grant references unknown {rtype} '{rname}' — not "
                                f"created by this bundle or present in the pod. {detail}"
                            ),
                        )
                    )


def _collect_grant_advisories(
    source_dir: Path,
    *,
    created_names: dict[str, set[str]] | None = None,
) -> list[str]:
    """Non-fatal findings about the bundle's grants — printed, never blocking.

    The hard-fail pass (`_validate_grant_references`) catches dangling
    references; this pass surfaces things that are valid but commonly cause
    silent runtime 403s or surprises after import.
    """
    advisories: list[str] = []
    created = created_names or {}
    for kind in ("agents", "functions"):
        for resource_dir in _resource_dirs(source_dir, kind):
            name = resource_dir.name
            try:
                payload, permissions = _split_resource_permissions_payload(
                    load_resource_payload(resource_dir, name, resource_type=kind)
                )
            except Exception:
                continue  # payload errors are already reported as issues
            grants = [
                grant
                for grant in (permissions or {}).get("grants", [])
                if isinstance(grant, dict)
            ]
            # A workload starts with ZERO access, so one created without grants
            # imports clean and then 403s the first time it touches anything.
            # That failure used to surface only at runtime, as
            # MISSING_WORKLOAD_RESOURCE_GRANT from inside an agent run. Say it
            # here, while the author is still looking at the bundle.
            if not grants and name in created.get(kind, set()):
                singular = kind[:-1]
                reason = (
                    "declares no 'permissions' block"
                    if permissions is None
                    else "declares an empty grants list"
                )
                advisories.append(
                    f"{singular} '{name}' {reason}, so it will be created with NO "
                    "access — it cannot read any table, folder, or connector and "
                    "will fail with MISSING_WORKLOAD_RESOURCE_GRANT at runtime. "
                    f"Add permissions.grants, or run `lemma {kind} grant {name} "
                    "<resource>:<perms>`."
                )
            connector_targets = sorted(
                {
                    str(grant.get("resource_name") or "?")
                    for grant in grants
                    if str(grant.get("resource_type") or "")
                    in ("connector", "connector_account")
                }
            )
            if connector_targets:
                advisories.append(
                    f"{kind[:-1]} '{name}' has connector grant(s) on "
                    f"{', '.join(connector_targets)} — connector accounts are "
                    "environment-specific; verify a connected account exists in "
                    "the target pod after import."
                )
            destructive = sorted(
                {
                    permission_id
                    for grant in grants
                    for permission_id in grant.get("permission_ids", [])
                    if permission_id in DESTRUCTIVE_PERMISSION_IDS
                }
            )
            if destructive:
                advisories.append(
                    f"{kind[:-1]} '{name}' is granted destructive permission(s) "
                    f"{', '.join(destructive)} — standing authority, it will NOT "
                    "prompt for user approval at runtime."
                )
            if kind == "agents":
                toolsets = {
                    str(entry).upper()
                    for entry in (payload.get("toolsets") or [])
                    if isinstance(entry, str)
                }
                has_agent_grants = any(
                    str(grant.get("resource_type") or "") == "agent" for grant in grants
                )
                if "SUBAGENTS" in toolsets and not has_agent_grants:
                    advisories.append(
                        f"agent '{name}' has the SUBAGENTS toolset but no agent "
                        "grants — it can only spawn copies of itself. Grant "
                        "`agent:<other>:execute` to let it dispatch other agents."
                    )
    return advisories


def _build_existing_folder_map(
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
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

    def sync_existing_folder(
        path_key: str, folder_dir: Path, existing: dict[str, Any]
    ) -> None:
        desired = desired_folder_metadata(folder_dir)
        update_args: dict[str, Any] = {}
        if existing.get("description") != desired["description"]:
            update_args["description"] = desired["description"]
        if (
            str(existing.get("visibility") or "").upper()
            != str(desired["visibility"]).upper()
        ):
            update_args["visibility"] = desired["visibility"]
        if not update_args:
            return

        _progress_start("file", path_key, "updating folder")
        updated = to_plain(
            pod_sdk.files.update(
                "/" + path_key,
                Update.from_dict({"path": "/" + path_key, **update_args}),
            )
        )
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
        parent_parts = parts[:-1]
        if parent_parts:
            ensure_folder(parent_parts, files_root.joinpath(*parent_parts))
        folder_meta = desired_folder_metadata(folder_dir)
        _progress_start("file", path_key, "creating folder")
        try:
            created = to_plain(
                pod_sdk.files.create_folder(
                    path="/" + path_key,
                    description=folder_meta.get("description"),
                    visibility=folder_meta.get("visibility"),
                )
            )
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
        folder_summaries.extend(_upload_bundled_files(pod_sdk, files_root))
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

        # Index unless the manifest explicitly says not to. `get(key, True)`
        # could never fire its default here: older exports wrote the key with a
        # null value, so `bool(None)` disabled indexing on every file and the
        # imported pod's search went quiet — with `doctor` still reporting ok.
        search_enabled = entry.get("search_enabled")
        search_enabled = True if search_enabled is None else bool(search_enabled)

        def _do_upload() -> None:
            pod_sdk.files.upload(
                local_file,
                directory_path=directory_path,
                name=name,
                description=entry.get("description"),
                search_enabled=search_enabled,
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
                UpdateAppRequest,
                _strip_keys(payload, {"name"}),
                context=f"app {app_name}",
                strict=True,
            ),
        )
        return "updated"

    try:
        pod_sdk.apps.create(
            build_request(
                CreateAppRequest, payload, context=f"app {app_name}", strict=True
            )
        )
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
                strict=True,
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
            app_name,
            build_request(
                UpdateAppRequest, update_payload, context=f"app {app_name}", strict=True
            ),
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
                strict=True,
            ),
        )


def _schedule_create_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in SCHEDULE_APPLY_FIELDS
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
                strict=True,
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


def _resolve_import_pod_member_id(
    client: Lemma, pod_sdk: Any, override: str | None
) -> str:
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

    summary, issues, advisories = _build_import_plan(
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
            "errors": [
                {"path": issue.path, "message": issue.message} for issue in issues
            ],
            "advisories": advisories,
        }
    if issues:
        rendered = "\n".join(f"- {issue.path}: {issue.message}" for issue in issues)
        raise ValueError(f"Bundle validation failed:\n{rendered}")
    for advisory in advisories:
        console.print(f"[yellow]advisory[/yellow] {advisory}")

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
                    strict=True,
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
            raise ValueError(
                f"Table already exists and --no-upsert was requested: {table_name}"
            )

        # `--with-data` seeds new tables only, so the natural sequence — import
        # the structure, then re-import with data — seeds nothing, and used to
        # say nothing either. Name the table and the way out rather than letting
        # someone conclude the rows arrived.
        if with_data and _has_seed_data(resource_dir):
            console.print(
                f"[yellow]warning[/yellow] table '{table_name}': skipped the data "
                "seed (the table already exists; --with-data seeds new tables "
                f"only). Load it with `lemma records import {table_name} "
                f"{resource_dir / TABLE_DATA_FILE}`."
            )
            summary["tables"].append(f"data-skipped:{table_name}")

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
        pod_sdk.tables.update(table_name, UpdateTableRequest.from_dict(update_fields))
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
                build_request(
                    AddColumnRequest,
                    {"column": column},
                    context=f"table {table_name} column",
                    strict=True,
                ),
            )
        for column_name in diff.to_remove:
            pod_sdk.tables.remove_column(table_name, column_name)
        summary["tables"].append(f"updated:{table_name}")
        _progress_done("table", table_name, "updated")

    # Resolve ${name} variables (and the legacy $POD_MEMBER token) lazily and
    # once: member resolution costs a members.list call we skip for bundles that
    # carry no placeholders. Built HERE, before the first resource that can hold
    # a placeholder — agents and functions used to import ahead of this and so
    # never had theirs resolved, sending a literal "${gmail_account}" to the API.
    apply_variables = _build_variable_applier(
        client,
        pod_sdk,
        source_dir=source_dir,
        var_overrides=variables,
        member_override=pod_member_id,
    )

    # Grants reference resources by name, and may point at workflows, apps,
    # schedules, or folders that import later than agents/functions. Collect
    # permission payloads here and apply them in one pass at the end.
    pending_permissions: list[tuple[str, str, dict[str, Any]]] = []

    existing_functions = _build_existing_map(
        list_items(pod_sdk.functions.list(limit=1000))
    )
    for resource_dir in _resource_dirs(source_dir, "functions"):
        function_name = resource_dir.name
        payload, permissions_payload = _split_resource_permissions_payload(
            load_resource_payload(resource_dir, function_name)
        )
        payload = _sanitize_function_payload_for_import(apply_variables(payload))
        permissions_payload = _resolve_grant_permissions(
            apply_variables, permissions_payload, kind="function", name=function_name
        )
        if function_name in existing_functions:
            if not upsert:
                raise ValueError(
                    f"Function already exists and --no-upsert was requested: {function_name}"
                )
            _progress_start("function", function_name, "updating")
            update_payload = _strip_keys(
                payload,
                {"name", "input_schema", "output_schema", "config_schema", "config"},
            )
            if update_payload:
                pod_sdk.functions.update(
                    function_name,
                    build_request(
                        UpdateFunctionRequest,
                        update_payload,
                        context=f"function {function_name}",
                        strict=True,
                    ),
                )
            summary["functions"].append(f"updated:{function_name}")
            _progress_done("function", function_name, "updated")
        else:
            _progress_start("function", function_name, "creating")
            pod_sdk.functions.create(
                build_request(
                    CreateFunctionRequest,
                    payload,
                    context=f"function {function_name}",
                    strict=True,
                )
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
        payload = apply_variables(payload)
        permissions_payload = _resolve_grant_permissions(
            apply_variables, permissions_payload, kind="agent", name=agent_name
        )
        if agent_name in existing_agents:
            if not upsert:
                raise ValueError(
                    f"Agent already exists and --no-upsert was requested: {agent_name}"
                )
            _progress_start("agent", agent_name, "updating")
            existing_agent = to_plain(pod_sdk.agents.get(agent_name))
            update_payload = _prepare_agent_update_payload(payload, existing_agent)
            if update_payload:
                pod_sdk.agents.update(
                    agent_name,
                    build_request(
                        UpdateAgentRequest,
                        update_payload,
                        context=f"agent {agent_name}",
                        strict=True,
                    ),
                )
            summary["agents"].append(f"updated:{agent_name}")
            _progress_done("agent", agent_name, "updated")
        else:
            _progress_start("agent", agent_name, "creating")
            pod_sdk.agents.create(
                build_request(
                    CreateAgentRequest,
                    payload,
                    context=f"agent {agent_name}",
                    strict=True,
                )
            )
            summary["agents"].append(f"created:{agent_name}")
            _progress_done("agent", agent_name, "created")
        if permissions_payload is not None:
            pending_permissions.append(("agent", agent_name, permissions_payload))

    apps = _build_existing_map(list_items(pod_sdk.apps.list(limit=1000)))
    for resource_dir in _resource_dirs(source_dir, "apps"):
        app_name = resource_dir.name
        payload = _sanitize_app_payload_for_import(
            apply_variables(
                load_resource_payload(resource_dir, app_name, resource_type="apps")
            )
        )
        app_exists = app_name in apps
        if app_exists:
            if not upsert:
                raise ValueError(
                    f"App already exists and --no-upsert was requested: {app_name}"
                )
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
            # A no-build app's files ARE its source: there is no build step that
            # turns one tree into another, so the same archive is both. Uploading
            # only the dist left the pod with no source archive, and export falls
            # back to writing `dist.zip` when there is none — so `html.html` went
            # in and a dist zip came out, and the next import had nothing to edit.
            # A Vite app is the case where the two genuinely differ, and it takes
            # the `source/` branch above, which uploads both.
            pod_sdk.apps.upload_bundle(
                app_name,
                source_archive=dist_archive_path,
                dist_archive=dist_archive_path,
            )
            _progress_done("app", app_name, "uploaded bundle")

    workflow_dirs = _resource_dirs(source_dir, "workflows")
    existing_workflows = (
        _build_existing_map(list_items(pod_sdk.workflows.list(limit=1000)))
        if workflow_dirs
        else {}
    )
    for resource_dir in workflow_dirs:
        workflow_name = resource_dir.name
        payload = apply_variables(load_resource_payload(resource_dir, workflow_name))
        metadata_payload = _strip_keys(payload, {"name", "nodes", "edges"})
        graph_start = payload.get("start")

        if workflow_name in existing_workflows:
            if not upsert:
                raise ValueError(
                    f"Workflow already exists and --no-upsert was requested: {workflow_name}"
                )
            _progress_start("workflow", workflow_name, "updating")
            pod_sdk.workflows.update(
                workflow_name,
                build_request(
                    WorkflowUpdateRequest,
                    metadata_payload,
                    context=f"workflow {workflow_name}",
                    strict=True,
                ),
            )
            action = "updated"
        else:
            create_payload = {"name": workflow_name, **metadata_payload}
            _progress_start("workflow", workflow_name, "creating")
            pod_sdk.workflows.create(
                build_request(
                    WorkflowCreateRequest,
                    create_payload,
                    context=f"workflow {workflow_name}",
                    strict=True,
                )
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
                raise ValueError(
                    f"Schedule already exists and --no-upsert was requested: {schedule_name}"
                )
            _progress_start("schedule", schedule_name, "updating")
            pod_sdk.schedules.update(
                existing_id,
                build_request(
                    UpdateScheduleRequest,
                    _schedule_update_fields(payload),
                    context=f"schedule {schedule_name}",
                    strict=True,
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
            created_name = str(
                created.get("name") or created.get("id") or schedule_name
            )
            summary["schedules"].append(f"created:{created_name}")
            _progress_done("schedule", schedule_name, "created")

    surface_dirs = _resource_dirs(source_dir, "surfaces")
    # Surfaces are addressed by their pod-unique name (defaulting to the
    # lowercased platform), the same key the server-side applier upserts on.
    existing_surface_names = (
        {
            _surface_name_from_payload(to_plain(item), "")
            for item in list_items(pod_sdk.surfaces.list(limit=100))
        }
        if surface_dirs
        else set()
    )
    for resource_dir in surface_dirs:
        surface_name = resource_dir.name
        payload = apply_variables(load_resource_payload(resource_dir, surface_name))
        platform = _surface_platform_from_payload(payload, surface_name)
        surface_key = _surface_name_from_payload(payload, surface_name)
        exists = surface_key in existing_surface_names
        if exists and not upsert:
            raise ValueError(
                f"Surface already exists and --no-upsert was requested: {surface_key}"
            )
        action = "updated" if exists else "created"
        _progress_start("surface", surface_name, "upserting")
        pod_sdk.surfaces.upsert(
            surface_key, {**_surface_upsert_body(payload), "platform": platform}
        )
        summary["surfaces"].append(f"{action}:{surface_name}")
        _progress_done("surface", surface_name, action)

    summary["files"].extend(
        _import_pod_files(client, pod_id, source_dir, with_files=with_files)
    )

    for kind, resource_name, permissions_payload in pending_permissions:
        grants = [
            grant
            for grant in (permissions_payload or {}).get("grants", [])
            if isinstance(grant, dict)
        ]
        # An empty grants list is a deliberate "revoke everything". That is fine
        # for a resource this bundle just created (it had nothing), but on an
        # upsert it strips a live workload's access — and `<resource> init`
        # scaffolds exactly that shape, so re-authoring an existing workload from
        # a fresh scaffold would silently disable it. Say so before doing it.
        if not grants:
            api = pod_sdk.agents if kind == "agent" else pod_sdk.functions
            try:
                had = len(to_plain(api.permissions(resource_name)).get("grants") or [])
            except Exception:  # noqa: BLE001 — advisory only, never block the import
                had = 0
            if had:
                console.print(
                    f"[yellow]warning[/yellow] {kind} '{resource_name}': the bundle "
                    f"declares an EMPTY grants list, revoking all {had} existing "
                    "grant(s). Remove the `permissions` key to leave them alone."
                )
        targets = ", ".join(
            f"{grant.get('resource_type')}:{grant.get('resource_name')}"
            for grant in grants
        )
        detail = f"replacing permissions ({len(grants)} grant(s)"
        detail += f": {targets})" if targets else ")"
        _progress_start(kind, resource_name, detail)
        if kind == "function":
            pod_sdk.functions.replace_permissions(
                resource_name,
                build_request(
                    FunctionPermissionsReplaceRequest,
                    permissions_payload,
                    context=f"function {resource_name} permissions",
                    strict=True,
                ),
            )
        else:
            pod_sdk.agents.replace_permissions(
                resource_name,
                build_request(
                    AgentPermissionsReplaceRequest,
                    permissions_payload,
                    context=f"agent {resource_name} permissions",
                    strict=True,
                ),
            )
        summary[f"{kind}s"].append(f"permissions:{resource_name}:{len(grants)}")
        _progress_done(
            kind, resource_name, f"replaced permissions ({len(grants)} grant(s))"
        )

    return {
        "ok": True,
        "pod_id": pod_id,
        "source_dir": str(source_dir),
        "summary": summary,
        "advisories": advisories,
    }
