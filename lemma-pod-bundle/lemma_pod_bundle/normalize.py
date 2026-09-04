"""Per-resource payload normalization and validation for pod bundles."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .diff import _is_system_table_column
from .layout import FORMAT_VERSION, _parse_function_headers


@dataclass
class BundleValidationIssue:
    path: str
    message: str


def _strip_keys(payload: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in keys}


def _normalize_resource_permissions_payload(payload: dict[str, Any]) -> dict[str, Any]:
    grants = payload.get("grants", [])
    if not isinstance(grants, list):
        raise ValueError("Embedded permissions must be an object with a grants list.")
    if any(
        not isinstance(grant, dict) or "resource_name" not in grant for grant in grants
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


def _normalize_pod_payload(pod: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "name": pod.get("name"),
        "description": pod.get("description"),
        "icon_url": pod.get("icon_url"),
    }


# Audit/ownership columns dropped from seeded rows: the backend manages the
# timestamps, and a source-pod user_id would orphan ownership in the target pod
# (bulk-create assigns rows to the importer). The primary key is kept so any
# foreign-key references between seeded tables still resolve.
_SEED_STRIP_COLUMNS = frozenset({"created_at", "updated_at", "user_id"})


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


# Server-owned fields on a function response that the create/update request does
# not accept. They must be stripped on BOTH sides: on export so a fresh bundle is
# clean, and again on import so a bundle written by an older exporter (which left
# them in) still applies. `revision_hash` is the one that used to slip through —
# the request model dropped it silently, which is exactly the class of quiet loss
# the importer now refuses.
_FUNCTION_SERVER_FIELDS = frozenset(
    {
        "id",
        "pod_id",
        "user_id",
        "created_at",
        "updated_at",
        "status",
        "code_path",
        "revision_hash",
        "input_schema",
        "output_schema",
        "config_schema",
        "allowed_actions",
    }
)


def _normalize_function_payload(function: dict[str, Any]) -> dict[str, Any]:
    return _strip_keys(function, set(_FUNCTION_SERVER_FIELDS))


def _sanitize_function_payload_for_import(payload: dict[str, Any]) -> dict[str, Any]:
    return _strip_keys(payload, set(_FUNCTION_SERVER_FIELDS))


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
    """A schedule as a definition, with where-it-ran-last left behind.

    The list below drifted from the API: it strips `last_run_at`/`next_run_at`
    and not the fields that replaced them, so export wrote seven pieces of
    runtime state that import rejects by name -- "Unrecognized field(s) on
    schedule". A bundle this code produced could not be imported by it, and the
    only way through was editing the JSON by hand.

    The rule is what a schedule *is*, not how a particular installation's copy
    of it has been getting on: when it last fired, what it returned, how many
    times it has failed, and whether failures have paused it all belong to the
    pod it ran in, never to the pod it is being copied into.

    `provider_trigger_id` and `installation_id` belong to the same category and
    hide one level deeper, inside `config`.
    """
    stripped = _strip_keys(
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
            # Runtime state, added after this list was written.
            "consecutive_failures",
            "is_internal",
            "last_error",
            "last_fire_status",
            "last_fired_at",
            "last_run_id",
            "paused_by_failures",
        },
    )
    # Both of these name something the *source* organization owns, and an
    # imported schedule that kept either would answer to that organization's
    # events rather than the importer's.
    #
    # `provider_trigger_id` is a subscription the source org's account
    # provisioned. `installation_id` is worse: for a GitHub App it *is* the
    # tenant filter -- webhook matching is containment against
    # `{source, installation_id, event}` -- so a bundle carrying it hands the
    # importer schedules wired to the publisher's installation. Observed on a
    # real round trip: the bundle asked for the account as a variable and then
    # baked `installation_id` in beside it.
    #
    # Neither needs carrying. Both are re-derived when the importing pod's
    # account provisions the trigger, from that account.
    config = stripped.get("config")
    portable = {"provider_trigger_id", "installation_id"}
    if isinstance(config, dict) and portable & set(config):
        stripped = {**stripped, "config": _strip_keys(config, portable)}
    return stripped


def _normalize_surface_payload(surface: dict[str, Any]) -> dict[str, Any]:
    platform = str(surface.get("platform") or surface.get("surface_type") or "").upper()
    config = surface.get("config") or {}

    behavior_config: dict[str, Any] = {}
    channels: list[dict[str, Any]] = []
    for channel in config.get("channels") or []:
        if not isinstance(channel, dict) or channel.get("enabled") is False:
            continue
        # A channel is a place the surface's one agent may be spoken to, so
        # only the place travels. It used to carry `agent_name` as well, back
        # when one bot could serve several agents.
        entry = {
            key: channel[key]
            for key in ("channel_id", "channel_name")
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
    telegram = config.get("telegram") or {}
    app_name = str(telegram.get("app_name") or "").strip()
    if app_name:
        behavior_config["telegram"] = {"app_name": app_name}

    status = str(surface.get("status") or "").upper()
    # Prefer the surface's own pod-unique name so several surfaces of the same
    # platform (e.g. two Slack bots) round-trip as distinct resources instead of
    # collapsing onto the platform slug and dropping all but one. Falls back to the
    # lowercased platform for a legacy surface with no explicit name.
    resolved_name = str(surface.get("name") or "").strip() or platform.lower()
    payload: dict[str, Any] = {
        "name": resolved_name,
        "platform": platform,
        "default_agent_name": surface.get("agent_name"),
        "credential_mode": surface.get("credential_mode"),
        "account_id": surface.get("account_id"),
        # Ground-truth connector identity for the bound account, resolved by
        # the exporter from the account itself (never inferred from the
        # surface's own platform) — carried through so the portable-variable
        # tokenizer can stamp every account variable with connector/kind.
        # `provider` is the pre-rename spelling, still read so a bundle
        # exported before it keeps its metadata.
        "connector_id": surface.get("connector_id"),
        "connector_kind": surface.get("connector_kind") or surface.get("provider"),
        "is_enabled": status != "INACTIVE",
    }
    if behavior_config:
        payload["config"] = behavior_config
    return {key: value for key, value in payload.items() if value is not None}


def _surface_platform_from_payload(payload: dict[str, Any], resource_name: str) -> str:
    return str(payload.get("platform") or resource_name).upper()


def _surface_name_from_payload(payload: dict[str, Any], resource_name: str) -> str:
    """The surface's pod-unique name, resolved the way the server-side applier
    resolves it: the payload's own name, else the lowercased platform.

    Surfaces are keyed by this name, not by platform — a pod may run two Slack
    surfaces. The bundle directory name is deliberately not a fallback, so that
    importing a bundle through the CLI lands the same surfaces as importing the
    same bundle through the API.
    """
    name = str(payload.get("name") or "").strip()
    return (name or _surface_platform_from_payload(payload, resource_name)).lower()


# Server-owned app fields, stripped on export and again on import (see
# _FUNCTION_SERVER_FIELDS for why both). `url` is derived from the deployment and
# is not a create/update field.
_APP_SERVER_FIELDS = frozenset(
    {
        "id",
        "pod_id",
        "user_id",
        "created_at",
        "updated_at",
        "status",
        "url",
        "current_release_id",
        "source_archive_path",
        "allowed_actions",
    }
)


def _normalize_app_payload(app: dict[str, Any]) -> dict[str, Any]:
    return _strip_keys(app, set(_APP_SERVER_FIELDS))


def _sanitize_app_payload_for_import(payload: dict[str, Any]) -> dict[str, Any]:
    return _strip_keys(payload, set(_APP_SERVER_FIELDS))


def _validate_function_payload(
    resource_dir: Path,
    resource_name: str,
    payload: dict[str, Any],
) -> list[BundleValidationIssue]:
    issues: list[BundleValidationIssue] = []
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        issues.append(
            BundleValidationIssue(
                path=str(resource_dir), message="Function code is required."
            )
        )
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
