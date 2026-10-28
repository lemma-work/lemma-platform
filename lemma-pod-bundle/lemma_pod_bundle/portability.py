"""Portable variables (pod.json manifest).

Some resource fields hold ids that are only valid in the source pod and break a
re-import elsewhere: a workflow's ``assignee_pod_member_id``, an app's
platform-unique ``public_slug``. On export we replace each with a ``${name}``
placeholder and record it under ``pod.json -> variables``; on import the
placeholders are resolved (``--var``, ``--values``, or a per-type default) and
any still-unresolved ones drop their field so the import still succeeds.

Connected accounts are not among them. A surface or schedule names the pod
connector it runs through by its slug (``pod_connector``), which means the same
thing in every pod that declares it; which account that is, is the importing
pod's connector's decision and its account owners'. So a resource that still
names an ``account_id`` is an exporter bug and fails the export, and a grant on
a pod account -- which decides nothing for a workload, and is never portable --
is left out of the bundle. The pod connectors themselves travel as
``connectors/<slug>/<slug>.json`` -- the catalog connector and the mode, never
the installation or the shared account -- and a grant on one names its slug.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .apply_fields import GRANT_HOLDER_DIRS
from .jsonc import loads_jsonc
from .layout import POD_MEMBER_TOKEN, _read_json, _write_json

_PLACEHOLDER_RE = re.compile(r"\$\{[A-Za-z0-9_]+\}")
# Fields whose values are non-portable ids, by variable type.
_MEMBER_REF_FIELDS = frozenset({"assignee_pod_member_id"})
# A key no exported resource may carry: an org account is never portable.
_ACCOUNT_REF_FIELDS = frozenset({"account_id"})
# A bundled pod connector names its catalog connector and nothing of the org it
# came from: neither the installation it was mapped to nor the account a SHARED
# one ran as. Both are the importer's choice in their own org.
_CONNECTOR_ORG_REF_FIELDS = _ACCOUNT_REF_FIELDS | frozenset(
    {"auth_config_id", "shared_pod_account_id", "pod_account_id", "shared_account"}
)
# Grants on these resource types are never exported (see the module docstring).
_UNPORTABLE_GRANT_RESOURCE_TYPES = frozenset({"connector_account"})
# An app's public slug is unique platform-wide, so it cannot be reused verbatim in
# another pod; tokenize it into a variable (with the original as the default) that
# the importer can override, and the applier makes unique on collision.
_APP_SLUG_FIELDS = frozenset({"public_slug"})


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
    placeholder returned by ``on_value(raw, container)`` — ``container`` is the
    dict the field was found on, so a caller can read sibling metadata (e.g.
    the owning resource). Skips values already
    templated."""
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
                node[key] = on_value(value, node)
                changed = True
            elif _tokenize_ref_fields(value, field_keys, on_value):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _tokenize_ref_fields(item, field_keys, on_value):
                changed = True
    return changed


def _drop_unportable_grants(payload: object) -> bool:
    """Remove grants on pod accounts from a resource manifest, in place.

    Grants live under ``permissions.grants``; a bare top-level ``grants`` list is
    also accepted, matching what the backend applier reads."""
    if not isinstance(payload, dict):
        return False
    permissions = payload.get("permissions")
    holder = permissions if isinstance(permissions, dict) else payload
    grants = holder.get("grants")
    if not isinstance(grants, list):
        return False
    kept = [
        grant
        for grant in grants
        if not (
            isinstance(grant, dict)
            and str(grant.get("resource_type") or "")
            in _UNPORTABLE_GRANT_RESOURCE_TYPES
        )
    ]
    if len(kept) == len(grants):
        return False
    holder["grants"] = kept
    return True


def _contains_account_ref(
    node: object, keys: frozenset[str] = _ACCOUNT_REF_FIELDS
) -> bool:
    """True if ``node`` holds a value under one of ``keys`` anywhere."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and value:
                return True
            if _contains_account_ref(value, keys):
                return True
        return False
    if isinstance(node, list):
        return any(_contains_account_ref(item, keys) for item in node)
    return False


def _assert_no_account_ids(bundle_root: Path) -> None:
    for resource_json in sorted(bundle_root.glob("*/*/*.json")):
        data = loads_jsonc(resource_json.read_text(encoding="utf-8"))
        is_connector = resource_json.parent.parent.name == "connectors"
        if is_connector and _contains_account_ref(data, _CONNECTOR_ORG_REF_FIELDS):
            raise ValueError(
                f"{resource_json.relative_to(bundle_root)} names an installation "
                "or an account. A bundled connector names its catalog connector "
                "and mode; the importer chooses both in their own organization."
            )
        if _contains_account_ref(data):
            raise ValueError(
                f"{resource_json.relative_to(bundle_root)} names an account_id. "
                "Bundles name the pod connector a resource runs through "
                "(`pod_connector: <slug>`), never an organization's account."
            )


def _extract_portable_variables(bundle_root: Path) -> dict[str, Any]:
    """Replace non-portable ids in workflows and apps with
    ``${name}`` placeholders and record them under ``pod.json -> variables``.
    Returns the variables map (possibly empty)."""
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
            resource_type = resource_json.parent.parent.name
            if _tokenize_ref_fields(
                data,
                field_keys,
                lambda raw, node, owner=owner, resource_type=resource_type: make_ref(
                    owner, raw, node, resource_type
                ),
            ):
                resource_json.write_text(
                    json.dumps(data, indent=2) + "\n", encoding="utf-8"
                )

    def drop_unportable_grants(resource_glob: str) -> None:
        for resource_json in sorted(bundle_root.glob(resource_glob)):
            data = loads_jsonc(resource_json.read_text(encoding="utf-8"))
            if _drop_unportable_grants(data):
                resource_json.write_text(
                    json.dumps(data, indent=2) + "\n", encoding="utf-8"
                )

    rewrite(
        "workflows/*/*.json",
        _MEMBER_REF_FIELDS,
        lambda owner, raw, node, resource_type: register(
            "pod_member",
            raw,
            f"{owner}_assignee",
            {"description": f"Pod member assigned in workflow '{owner}'"},
        ),
    )
    for holder in GRANT_HOLDER_DIRS:
        drop_unportable_grants(f"{holder}/*/*.json")
    rewrite(
        "apps/*/*.json",
        _APP_SLUG_FIELDS,
        lambda owner, raw, node, resource_type: register(
            "app_slug",
            raw,
            f"{owner}_slug",
            {
                "description": (
                    f"Public URL slug for app '{owner}' "
                    "(unique platform-wide; a suffix is added on collision)"
                ),
                "default": raw,
            },
        ),
    )

    if variables:
        pod_data["variables"] = variables
        _write_json(pod_path, pod_data)

    _assert_no_account_ids(bundle_root)
    return variables


def _strip_unresolved_placeholders(node: object) -> object:
    """Drop dict entries whose value is still an unresolved ``${...}`` token so
    a literal placeholder never reaches the API (e.g. an unsupplied member)."""
    if isinstance(node, dict):
        return {
            key: _strip_unresolved_placeholders(value)
            for key, value in node.items()
            if not (isinstance(value, str) and _PLACEHOLDER_RE.fullmatch(value))
        }
    if isinstance(node, list):
        return [_strip_unresolved_placeholders(item) for item in node]
    return node
