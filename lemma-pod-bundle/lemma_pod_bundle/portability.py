"""Portable variables (pod.json manifest).

Some resource fields hold ids that are only valid in the source pod/org and
break a re-import elsewhere: a workflow's assignee_pod_member_id and a
schedule/surface account_id. On export we replace each with a ``${name}``
placeholder and record it under ``pod.json -> variables``; on import the
placeholders are resolved (``--var``, ``--values``, or a per-type default) and
any still-unresolved ones drop their field so the import still succeeds.
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
