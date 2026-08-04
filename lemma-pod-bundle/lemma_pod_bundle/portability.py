"""Portable variables (pod.json manifest).

Some resource fields hold ids that are only valid in the source pod/org and
break a re-import elsewhere: a workflow's assignee_pod_member_id and a
schedule/surface/etc account_id. On export we replace each with a ``${name}``
placeholder and record it under ``pod.json -> variables``; on import the
placeholders are resolved (``--var``, ``--values``, or a per-type default) and
any still-unresolved ones drop their field so the import still succeeds.

An ``account_id`` reference is tokenized generically for *every* resource type
(not a hardcoded per-type list): whatever JSON file under
``<resource_type>/<name>/*.json`` holds an ``account_id`` key must also carry
sibling ``connector_id``/``kind`` keys (stamped by the exporter from a real
account lookup, never inferred from the resource's own name), so the recorded
variable always tells the importer exactly which connector + kind it
needs to reconnect. A resource that has an ``account_id`` but no
``connector_id``/``connector_kind`` sibling is an exporter bug and fails the export
immediately rather than producing a variable the importer can't resolve.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .jsonc import loads_jsonc
from .layout import POD_MEMBER_TOKEN, _read_json, _write_json

_PLACEHOLDER_RE = re.compile(r"\$\{[A-Za-z0-9_]+\}")
# Fields whose values are non-portable ids, by variable type.
_MEMBER_REF_FIELDS = frozenset({"assignee_pod_member_id"})
_ACCOUNT_REF_FIELDS = frozenset({"account_id"})
# A grant on a pinned connector account is the one place an account id lives
# under a *different* key: grants are name-addressed, and an account has no
# human name, so its "name" IS the account id (see the backend's
# resource_names.py). Tokenizing only `account_id` therefore leaked a source-org
# id into every exported bundle that used fixed-account mode, and the import
# then failed its whole permissions pass on an id the target org has never seen.
_GRANT_ACCOUNT_RESOURCE_TYPES = frozenset({"connector_account"})
# Export-only metadata the exporter stamps beside a tokenized grant so the
# variable can record which connector + kind the importer must reconnect. Import
# strips these before the grant reaches the API.
GRANT_METADATA_KEYS = frozenset({"connector_id", "connector_kind", "provider"})
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
    ``connector_id``/``connector_kind`` next to ``account_id``). Skips values already
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


def _iter_account_grants(payload: object):
    """Yield every ``connector_account`` grant dict in a resource manifest.

    Grants live under ``permissions.grants``; a bare top-level ``grants`` list is
    also accepted, matching what the backend applier reads."""
    if not isinstance(payload, dict):
        return
    permissions = payload.get("permissions")
    raw = (
        permissions.get("grants")
        if isinstance(permissions, dict)
        else payload.get("grants")
    )
    for grant in raw or []:
        if (
            isinstance(grant, dict)
            and str(grant.get("resource_type") or "")
            in _GRANT_ACCOUNT_RESOURCE_TYPES
        ):
            yield grant


def _tokenize_grant_account_refs(payload: object, on_value) -> bool:
    """Replace each ``connector_account`` grant's ``resource_name`` (a raw
    account id) with a placeholder, the way ``_tokenize_ref_fields`` does for
    ``account_id`` keys. ``on_value(raw, grant)`` reads the grant's sibling
    connector metadata."""
    changed = False
    for grant in _iter_account_grants(payload):
        raw = grant.get("resource_name")
        if (
            isinstance(raw, str)
            and raw
            and raw != POD_MEMBER_TOKEN
            and not _PLACEHOLDER_RE.fullmatch(raw)
        ):
            grant["resource_name"] = on_value(raw, grant)
            changed = True
    return changed


def _contains_literal_account_ref(node: object) -> bool:
    """True if ``node`` still holds a raw (non-``${...}``) value under an
    account-reference key anywhere in its tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                key in _ACCOUNT_REF_FIELDS
                and isinstance(value, str)
                and value
                and not _PLACEHOLDER_RE.fullmatch(value)
            ):
                return True
            if _contains_literal_account_ref(value):
                return True
        return False
    if isinstance(node, list):
        return any(_contains_literal_account_ref(item) for item in node)
    return False


def _contains_literal_account_grant(payload: object) -> bool:
    """True if a ``connector_account`` grant still names a raw account id."""
    for grant in _iter_account_grants(payload):
        raw = grant.get("resource_name")
        if isinstance(raw, str) and raw and not _PLACEHOLDER_RE.fullmatch(raw):
            return True
    return False


def _assert_no_literal_account_ids(bundle_root: Path) -> None:
    """Defense in depth: after tokenization, no resource file may still hold a
    literal account id. A source-org account id is meaningless (and often
    inaccessible) to whoever imports the bundle, so leaking one instead of a
    ``${var}`` placeholder would silently break re-import elsewhere."""
    for resource_json in sorted(bundle_root.glob("*/*/*.json")):
        data = loads_jsonc(resource_json.read_text(encoding="utf-8"))
        if _contains_literal_account_ref(data):
            raise ValueError(
                f"{resource_json.relative_to(bundle_root)} still holds a literal "
                "account_id after tokenization — refusing to export a bundle "
                "that leaks a source-org account reference."
            )
        if _contains_literal_account_grant(data):
            raise ValueError(
                f"{resource_json.relative_to(bundle_root)} still holds a "
                "connector_account grant naming a literal account id after "
                "tokenization — refusing to export a bundle that leaks a "
                "source-org account reference."
            )


def require_account_variable_metadata(variables: dict[str, Any] | None) -> None:
    """Raise ``ValueError`` if any declared ``type="account"`` variable is
    missing connector/connector_kind metadata.

    An up-to-date exporter always populates both (enforced by
    :func:`_extract_portable_variables`); this is the import-side backstop that
    catches a bundle built before that guarantee existed, or a hand-edited one,
    at plan/import time instead of importing an account the UI/CLI can't
    resolve to the right connector.
    """
    for name, meta in (variables or {}).items():
        if str((meta or {}).get("type") or "").lower() != "account":
            continue
        has_kind = (meta or {}).get("connector_kind") or (meta or {}).get("provider")
        if not (meta or {}).get("connector") or not has_kind:
            raise ValueError(
                f"Variable '{name}' is a connector account reference but is "
                "missing connector/connector_kind info. Re-export this bundle to "
                "include it."
            )


def _extract_portable_variables(bundle_root: Path) -> dict[str, Any]:
    """Replace non-portable ids in workflows/schedules/surfaces/etc with
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

    def _account_ref_handler(
        owner: str,
        raw: str,
        node: dict[str, Any],
        resource_type: str,
        *,
        where: str = "account_id",
    ) -> str:
        connector_id = node.get("connector_id")
        # `kind` is what an install actually is; `provider` is the two-value
        # vocabulary it replaced, still read here so bundles exported before
        # the rename keep importing.
        kind = node.get("connector_kind") or node.get("provider")
        if not connector_id or not kind:
            raise ValueError(
                f"Bundle resource '{resource_type}/{owner}' has a {where} "
                "but no connector_id/connector_kind metadata — every exported "
                "account reference must carry its connector_id and connector_kind "
                "so the bundle stays portable and the importer can reconnect the "
                "right connector."
            )
        return register(
            "account",
            raw,
            f"{owner}_account",
            {
                "description": f"Connector account for {resource_type} '{owner}'",
                "connector": str(connector_id),
                "connector_kind": str(kind),
            },
        )

    def rewrite_grants(resource_glob: str) -> None:
        """Tokenize the account id a pinned-account grant carries in its
        `resource_name` — the fixed-account half of the connector model."""
        for resource_json in sorted(bundle_root.glob(resource_glob)):
            data = loads_jsonc(resource_json.read_text(encoding="utf-8"))
            owner = resource_json.parent.name
            resource_type = resource_json.parent.parent.name
            if _tokenize_grant_account_refs(
                data,
                lambda raw, grant, owner=owner, resource_type=resource_type: (
                    _account_ref_handler(
                        owner,
                        raw,
                        grant,
                        resource_type,
                        where="connector_account grant",
                    )
                ),
            ):
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
    # Generic, resource-type-agnostic: covers surfaces, schedules, and any
    # future resource type that gains an account_id, with no per-type glue.
    rewrite("*/*/*.json", _ACCOUNT_REF_FIELDS, _account_ref_handler)
    # Grants are the exception the generic pass can't see: the account id sits
    # in `resource_name`, a key that means something different everywhere else.
    for grant_glob in ("agents/*/*.json", "functions/*/*.json"):
        rewrite_grants(grant_glob)
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

    _assert_no_literal_account_ids(bundle_root)
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
