"""Pure OpenAPI spec-walking helpers — no descriptor building, no I/O.

Ported from ``lemma-connectors/scripts/generate_openapi_metadata.py`` (which
lives under ``scripts/`` and is not importable, and whose ``generate_metadata``/
``sanitize_spec`` are codegen-coupled and force request bodies to
``application/json`` — destroying multipart). Reimplemented here so the walk is
self-contained and multipart-preserving.

These resolve refs, pick content schemas and name operations. What is built out
of them lives in :mod:`spec_import`.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# --- constants --------------------------------------------------------------

SUCCESS_RESPONSE_CODES = ("200", "201", "202", "203", "204", "206")

# Provider-generic noise params (Google-style) that are auth/formatting knobs,
# never real operation inputs. Harmless to drop for any provider.
IGNORED_PARAMETER_NAMES = {
    "access_token",
    "alt",
    "callback",
    "key",
    "oauth_token",
    "prettyPrint",
    "quotaUser",
    "uploadType",
    "upload_protocol",
    "userIp",
    "$.xgafv",
}


# --- ported pure helpers ----------------------------------------------------


def resolve_ref(spec: dict[str, Any], ref: str) -> Any:
    node: Any = spec
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return node


def resolve_once(spec: dict[str, Any], value: Any) -> Any:
    if isinstance(value, dict) and "$ref" in value:
        return resolve_ref(spec, value["$ref"])
    return value


def deep_resolve_refs(
    spec: dict[str, Any],
    value: Any,
    *,
    seen_refs: set[str] | None = None,
) -> Any:
    seen_refs = seen_refs or set()
    value = copy.deepcopy(value)
    if isinstance(value, dict):
        if "$ref" in value:
            ref = value["$ref"]
            if ref in seen_refs:
                return {"$ref": ref}
            return deep_resolve_refs(
                spec,
                resolve_ref(spec, ref),
                seen_refs={*seen_refs, ref},
            )
        return {
            key: deep_resolve_refs(spec, item, seen_refs=seen_refs)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [deep_resolve_refs(spec, item, seen_refs=seen_refs) for item in value]
    return value


def normalize_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    if not schema:
        return {"type": "object", "additionalProperties": True}
    return schema


def build_parameter_entry(
    spec: dict[str, Any], parameter: dict[str, Any]
) -> dict[str, Any] | None:
    parameter = resolve_once(spec, parameter)
    name = parameter["name"]
    if name in IGNORED_PARAMETER_NAMES:
        return None
    raw_schema = parameter.get("schema") or {}
    return {
        "name": name,
        "location": parameter["in"],
        "required": parameter.get("required", False),
        "description": parameter.get("description"),
        "schema": normalize_schema(deep_resolve_refs(spec, raw_schema)),
        "style": parameter.get("style"),
        "explode": parameter.get("explode"),
    }


def pick_content_schema(
    spec: dict[str, Any],
    content: dict[str, Any] | None,
    *,
    preferred_types: list[str] | None = None,
) -> tuple[str, dict[str, Any], str | None]:
    if not content:
        return "application/json", {"type": "object", "additionalProperties": True}, None
    preferred = preferred_types or ["application/json", "*/*"]
    for content_type in preferred:
        if content_type in content:
            item = resolve_once(spec, content[content_type])
            raw_schema = item.get("schema") or {}
            schema_ref = raw_schema.get("$ref") if isinstance(raw_schema, dict) else None
            return content_type, normalize_schema(deep_resolve_refs(spec, raw_schema)), schema_ref
    first_type, first_value = next(iter(content.items()))
    item = resolve_once(spec, first_value)
    raw_schema = item.get("schema") or {}
    schema_ref = raw_schema.get("$ref") if isinstance(raw_schema, dict) else None
    return first_type, normalize_schema(deep_resolve_refs(spec, raw_schema)), schema_ref


def build_tool_name(operation_id: str, method: str, path: str) -> str:
    if operation_id:
        parts = []
        for chunk in operation_id.replace("/", ".").split("."):
            chunk = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", chunk)
            chunk = re.sub(r"[^a-zA-Z0-9]+", "_", chunk).strip("_").lower()
            if chunk:
                parts.append(chunk)
        return "_".join(parts)
    slug = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    return f"{method.lower()}_{slug}"


def pick_success_response(operation: dict[str, Any]) -> dict[str, Any] | None:
    responses = operation.get("responses", {}) or {}
    for status_code in SUCCESS_RESPONSE_CODES:
        response = responses.get(status_code)
        if response is not None:
            return response
    for status_code, response in responses.items():
        if str(status_code).startswith("2"):
            return response
    return None


def prefers_binary_response(
    *,
    operation_id: str,
    path: str,
    content: dict[str, Any] | None,
) -> bool:
    marker = f"{operation_id} {path}".lower()
    keyword_match = any(
        token in marker
        for token in ("download", "export", "thumbnail", "avatar", "image", "tarball", "zipball")
    )
    if not content:
        return keyword_match
    media_types = {str(item).split(";", 1)[0].strip().lower() for item in content}
    has_binary_media = any(
        media_type in {"*/*", "application/octet-stream"}
        or media_type.startswith("image/")
        or media_type.startswith("audio/")
        or media_type.startswith("video/")
        for media_type in media_types
    )
    return keyword_match and has_binary_media
