"""Turn an OpenAPI spec into connector operation descriptors — no codegen.

The pure spec-walking helpers (``resolve_ref``/``deep_resolve_refs``/
``build_parameter_entry``/``pick_content_schema``/``prefers_binary_response`` …)
are ported from ``lemma-connectors/scripts/generate_openapi_metadata.py`` (which
lives under ``scripts/`` and is not importable, and whose ``generate_metadata``/
``sanitize_spec`` are codegen-coupled and force request bodies to
``application/json`` — destroying multipart). We reimplement the walk here so it
is self-contained and multipart-preserving.

Output: for each selected operation, a flat ``input_schema`` the agent fills, an
``output_schema``, and an ``execution`` descriptor ({"kind": "http", ...}) consumed
by ``OpenApiHttpExecutor``.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
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

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

# Multipart-/binary-aware order — we must NOT collapse everything to JSON.
_BODY_PREFERRED_TYPES = [
    "application/json",
    "multipart/form-data",
    "application/octet-stream",
    "*/*",
]
_RESPONSE_PREFERRED_TYPES = ["application/json", "*/*"]


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


# --- descriptor building ----------------------------------------------------


@dataclass
class OpenAPIOperation:
    public_name: str
    display_name: str | None
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    execution: dict[str, Any]
    tags: tuple[str, ...] = field(default_factory=tuple)


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _clean_description(text: str | None, fallback: str) -> str:
    if not text:
        return fallback
    compact = text.strip()
    for marker in ("\n\nArgs:", "\n\nReturns:", "\n\nRaises:"):
        if marker in compact:
            compact = compact.split(marker, 1)[0]
    compact = compact.split("\n\n", 1)[0]
    return " ".join(compact.split()) or fallback


def _file_input_schema(description: str) -> dict[str, Any]:
    """A file argument: a pod datastore path, inline base64, or raw text."""
    return {
        "description": description,
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "pod_path": {
                        "type": "string",
                        "description": "Datastore path (e.g. /me/report.pdf) read server-side.",
                    }
                },
                "required": ["pod_path"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "base64": {
                        "type": "string",
                        "description": "Base64-encoded file bytes.",
                    }
                },
                "required": ["base64"],
                "additionalProperties": False,
            },
            {"type": "string", "description": "Raw text content."},
        ],
    }


def _binary_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": (
            "Binary result. When output_path is supplied a pod file reference is "
            "returned; otherwise base64-encoded content."
        ),
        "additionalProperties": True,
    }


def _output_path_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "Optional datastore path (e.g. /me/out.tar.gz) to write the downloaded "
            "file to. If omitted, the content is returned base64-encoded."
        ),
    }


def _index_allowlist(
    allowlist: list[dict[str, Any]] | None,
) -> tuple[set[str], set[tuple[str, str]]]:
    ids: set[str] = set()
    paths: set[tuple[str, str]] = set()
    for entry in allowlist or []:
        op_id = entry.get("operation_id")
        if op_id:
            ids.add(op_id)
        method = entry.get("method")
        path = entry.get("path")
        if method and path:
            paths.add((method.upper(), path))
    return ids, paths


def _is_binary_property(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("format") == "binary"


def _analyze_body(
    content_type: str,
    body_schema: dict[str, Any],
    override: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Return (binary_fields, form_fields, body_property_schema).

    ``binary_fields`` name payload keys whose values are files the executor must
    turn into raw bytes (from a pod path / base64 / text). ``form_fields`` are the
    remaining multipart form fields sent as-is.
    """
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    forced_binary = set(override.get("binary_body_fields") or [])

    if normalized == "application/json":
        # JSON bodies are sent verbatim; base64/binary strings inside are the
        # provider's concern (e.g. GitHub contents API), not ours to decode.
        return [], [], body_schema

    if normalized == "multipart/form-data" and body_schema.get("type") == "object":
        properties = dict(body_schema.get("properties") or {})
        binary_fields: list[str] = []
        form_fields: list[str] = []
        new_props: dict[str, Any] = {}
        for name, prop in properties.items():
            if _is_binary_property(prop) or name in forced_binary:
                binary_fields.append(name)
                new_props[name] = _file_input_schema(f"File for multipart field '{name}'.")
            else:
                form_fields.append(name)
                new_props[name] = prop
        body_prop = {
            "type": "object",
            "properties": new_props,
            "required": body_schema.get("required", []),
            "additionalProperties": bool(body_schema.get("additionalProperties", False)),
        }
        return binary_fields, form_fields, body_prop

    # octet-stream / */* / any other single-blob body: the whole body is a file.
    return ["body"], [], _file_input_schema("Raw file body for the request.")


@dataclass(frozen=True, slots=True)
class _Parameters:
    """The flattened parameter set of one operation, grouped by location."""

    properties: dict[str, Any]
    required: list[str]
    path_params: list[str]
    query_params: list[dict[str, Any]]
    header_params: list[str]


def _collect_parameters(
    spec: dict[str, Any], shared_parameters: list[Any], operation: dict[str, Any]
) -> _Parameters:
    """Merge path-level and operation-level parameters, grouped by location."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    path_params: list[str] = []
    query_params: list[dict[str, Any]] = []
    header_params: list[str] = []

    for raw in list(shared_parameters) + list(operation.get("parameters") or []):
        entry = build_parameter_entry(spec, raw)
        # Cookie parameters have no representation in the execution descriptor.
        if entry is None or entry["location"] == "cookie":
            continue
        name = entry["name"]
        schema = dict(entry["schema"])
        if entry.get("description") and "description" not in schema:
            schema["description"] = entry["description"]
        properties[name] = schema
        if entry["required"]:
            required.append(name)
        if entry["location"] == "path":
            path_params.append(name)
        elif entry["location"] == "query":
            query_params.append(
                {"name": name, "style": entry.get("style"), "explode": entry.get("explode")}
            )
        elif entry["location"] == "header":
            header_params.append(name)

    return _Parameters(
        properties=properties,
        required=required,
        path_params=path_params,
        query_params=query_params,
        header_params=header_params,
    )


def _collect_request_body(
    spec: dict[str, Any],
    operation: dict[str, Any],
    override: dict[str, Any],
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any] | None:
    """Fold the request body into the input schema, describing how to send it."""
    request_body = operation.get("requestBody")
    if not request_body:
        return None
    request_body = resolve_once(spec, request_body)
    content_type, body_schema, _ = pick_content_schema(
        spec, request_body.get("content") or {}, preferred_types=_BODY_PREFERRED_TYPES
    )
    binary_fields, form_fields, body_prop = _analyze_body(content_type, body_schema, override)
    properties["body"] = body_prop
    if request_body.get("required"):
        required.append("body")
    return {
        "content_type": content_type,
        "field": "body",
        "binary_fields": binary_fields,
        "form_fields": form_fields,
    }


def _resolve_response(
    spec: dict[str, Any],
    operation: dict[str, Any],
    override: dict[str, Any],
    *,
    op_id: str,
    path: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Decide whether the response is binary, and what its schema is."""
    binary = bool(override.get("binary_response"))
    success = pick_success_response(operation)
    if success is None:
        return binary, {"type": "object", "additionalProperties": True}

    resp_content = resolve_once(spec, success).get("content") or {}
    if not binary:
        binary = prefers_binary_response(operation_id=op_id, path=path, content=resp_content)
    if binary:
        return True, _binary_output_schema()
    _, output_schema, _ = pick_content_schema(
        spec, resp_content, preferred_types=_RESPONSE_PREFERRED_TYPES
    )
    return False, output_schema


def _build_operation(
    spec: dict[str, Any],
    *,
    server_url: str,
    path: str,
    method: str,
    operation: dict[str, Any],
    shared_parameters: list[Any],
    override: dict[str, Any],
    default_headers: dict[str, str] | None,
) -> OpenAPIOperation:
    op_id = operation.get("operationId") or ""
    # Operation-level server override (e.g. GitHub asset uploads use
    # uploads.github.com rather than api.github.com).
    op_servers = operation.get("servers") or []
    if op_servers and isinstance(op_servers[0], dict) and op_servers[0].get("url"):
        server_url = op_servers[0]["url"].rstrip("/")
    public_name = _normalize_name(override.get("name") or build_tool_name(op_id, method, path))
    display_name = operation.get("summary") or public_name
    description = (
        override.get("description")
        or _clean_description(operation.get("summary") or operation.get("description"), public_name)
    )

    params = _collect_parameters(spec, shared_parameters, operation)
    properties = dict(params.properties)
    required = list(params.required)

    request_body_desc = _collect_request_body(spec, operation, override, properties, required)
    binary, output_schema = _resolve_response(spec, operation, override, op_id=op_id, path=path)

    if binary:
        properties["output_path"] = _output_path_schema()

    input_schema: dict[str, Any] = {
        "type": "object",
        "title": public_name,
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = required

    execution: dict[str, Any] = {
        "kind": "http",
        "mode": "openapi",
        "method": method,
        "path": path,
        "server_url": server_url,
        "path_params": params.path_params,
        "query_params": params.query_params,
        "header_params": params.header_params,
        "request_body": request_body_desc,
        "response": {"binary": binary},
    }
    if default_headers:
        execution["default_headers"] = dict(default_headers)

    return OpenAPIOperation(
        public_name=public_name,
        display_name=display_name,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        execution=execution,
        tags=tuple(operation.get("tags") or ()),
    )


def build_operation_descriptors(
    spec: dict[str, Any],
    *,
    server_url: str,
    allowlist: list[dict[str, Any]] | None,
    overrides: dict[str, Any] | None = None,
    default_headers: dict[str, str] | None = None,
) -> list[OpenAPIOperation]:
    """Walk ``spec.paths`` and materialize operations.

    ``allowlist=None`` selects ALL operations (generic OpenAPI-URL connectors);
    a list (even empty) selects only the matching operationIds / method+path.
    """
    overrides = overrides or {}
    select_all = allowlist is None
    allowed_ids, allowed_paths = _index_allowlist(allowlist)
    paths = spec.get("paths") or {}

    results: list[OpenAPIOperation] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        shared_parameters = path_item.get("parameters") or []
        for raw_method, operation in path_item.items():
            method = raw_method.upper()
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId") or ""
            if not select_all and op_id not in allowed_ids and (method, path) not in allowed_paths:
                continue
            override = overrides.get(op_id) or overrides.get(f"{method} {path}") or {}
            results.append(
                _build_operation(
                    spec,
                    server_url=server_url,
                    path=path,
                    method=method,
                    operation=operation,
                    shared_parameters=shared_parameters,
                    override=override,
                    default_headers=default_headers,
                )
            )
    return results


def build_raw_passthrough(
    connector_id: str,
    *,
    server_url: str,
    name: str | None = None,
    default_headers: dict[str, str] | None = None,
) -> OpenAPIOperation:
    """A generic authenticated HTTP passthrough for the connector's API."""
    public_name = _normalize_name(name or f"{connector_id}_http_request")
    input_schema = {
        "type": "object",
        "title": public_name,
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                "description": "HTTP method.",
            },
            "path": {
                "type": "string",
                "description": f"Path relative to {server_url}, e.g. /repos/{{owner}}/{{repo}}.",
            },
            "query": {"type": "object", "additionalProperties": True},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
            "body": {
                "description": "JSON body (object), or a file object ({pod_path}/{base64}) for uploads."
            },
            "output_path": _output_path_schema(),
        },
        "required": ["method", "path"],
        "additionalProperties": False,
    }
    execution = {"kind": "http", "mode": "raw", "server_url": server_url}
    if default_headers:
        execution["default_headers"] = dict(default_headers)
    return OpenAPIOperation(
        public_name=public_name,
        display_name=public_name,
        description=f"Perform an arbitrary authenticated request against the {connector_id} API.",
        input_schema=input_schema,
        output_schema={"type": "object", "additionalProperties": True},
        execution=execution,
    )
