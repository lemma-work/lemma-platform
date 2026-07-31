from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


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

CONTENT_TYPE_OVERRIDES = {
    "message/cpim": "application/json",
    "message/delivery-status": "application/json",
    "message/disposition-notification": "application/json",
    "message/external-body": "application/json",
    "message/feedback-report": "application/json",
    "message/global": "application/json",
    "message/global-delivery-status": "application/json",
    "message/global-disposition-notification": "application/json",
    "message/global-headers": "application/json",
    "message/http": "application/json",
    "message/imdn+xml": "application/json",
    "message/news": "application/json",
    "message/partial": "application/json",
    "message/rfc822": "application/json",
    "message/s-http": "application/json",
    "message/sip": "application/json",
    "message/sipfrag": "application/json",
    "message/tracking-status": "application/json",
    "message/vnd.si.simp": "application/json",
    "message/vnd.wfa.wsc": "application/json",
}

SUCCESS_RESPONSE_CODES = ("200", "201", "202", "203", "204", "206")

JIRA_JSON_NODE_PATCHES = {
    "    int: bool | None = None\n": "    is_int: bool | None = Field(default=None, alias='int')\n",
    "    long: bool | None = None\n": "    is_long: bool | None = Field(default=None, alias='long')\n",
    "    object: bool | None = None\n": "    is_object: bool | None = Field(default=None, alias='object')\n",
    "            Field(discriminator='nodeType'),\n": "            Field(discriminator='node_type'),\n",
}

GMAIL_PYDANTIC_MODEL_PATCHES = {
    "    data: Base64Str | None = None\n": "    data: str | None = None\n",
    "    raw: Base64Str | None = None\n": "    raw: str | None = None\n",
}

SLACK_PYDANTIC_MODEL_PATCHES = {
    "    message: ObjsMessage\n": "    message: Any\n",
    "    channel_actions_count: int\n": "    channel_actions_count: int | None = None\n",
    "    channel_actions_ts: list[int | Any]\n": "    channel_actions_ts: list[int | Any] | None = None\n",
    "    messages: list[ObjsMessage] = Field(..., min_length=1)\n": "    messages: list[Any]\n",
    "    pin_count: int\n": "    pin_count: int | None = None\n",
    "    user: list[ObjsUser1 | ObjsUser2]\n": "    user: Any\n",
    "    cache_ts: int\n": "    cache_ts: int | None = None\n",
    "    members: list[list[ObjsUser1 | ObjsUser2]] = Field(..., min_length=1)\n": "    members: list[Any] = Field(..., min_length=1)\n",
    "    channels: list[list[ObjsConversation1 | ObjsConversation2 | ObjsConversation3]]\n": "    channels: list[Any]\n",
    "    channel: list[\n        Channel1 | list[ObjsConversation1 | ObjsConversation2 | ObjsConversation3]\n    ]\n": "    channel: Any\n",
    "    response_metadata: (\n        list[ObjsResponseMetadata1 | ObjsResponseMetadata2 | ObjsResponseMetadata3]\n        | None\n    ) = None\n": "    response_metadata: Any | None = None\n",
}


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


def build_parameter_entry(spec: dict[str, Any], parameter: dict[str, Any]) -> dict[str, Any] | None:
    parameter = resolve_once(spec, parameter)
    name = parameter["name"]
    if name in IGNORED_PARAMETER_NAMES:
        return None
    raw_schema = parameter.get("schema") or {}
    schema_ref = raw_schema.get("$ref") if isinstance(raw_schema, dict) else None
    python_name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).replace("-", "_")
    python_name = re.sub(r"[^a-zA-Z0-9_]+", "_", python_name).strip("_").lower()
    return {
        "name": name,
        "python_name": python_name,
        "location": parameter["in"],
        "required": parameter.get("required", False),
        "description": parameter.get("description"),
        "schema": normalize_schema(deep_resolve_refs(spec, raw_schema)),
        "schema_ref": schema_ref,
    }


def pick_content_schema(
    spec: dict[str, Any],
    content: dict[str, Any] | None,
    *,
    preferred_types: list[str] | None = None,
) -> tuple[str, dict[str, Any], str | None]:
    if not content:
        return "application/json", {"type": "object", "additionalProperties": True}, None
    preferred = preferred_types or [
        "application/json",
        "multipart/related",
        "message/rfc822",
        "*/*",
    ]
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
        for chunk in operation_id.split("."):
            chunk = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", chunk)
            parts.append(chunk.lower())
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
    success_status_codes: set[str] | None = None,
) -> bool:
    marker = f"{operation_id} {path}".lower()
    keyword_match = any(
        token in marker
        for token in ("download", "export", "thumbnail", "avatar", "image", "content")
    )
    if not content:
        return keyword_match
    media_types = {str(item).split(";", 1)[0].strip().lower() for item in content}
    has_binary_media = any(
        media_type in {"*/*", "application/octet-stream"}
        or media_type.startswith("image/")
        or media_type.startswith("audio/")
        or media_type.startswith("video/")
        or media_type.startswith("text/")
        for media_type in media_types
    )
    partial_or_redirect = bool(success_status_codes and {"206", "303"} & success_status_codes)
    return keyword_match and (has_binary_media or partial_or_redirect)


def build_module_index(client_root: Path, module_root: str) -> dict[str, str]:
    module_index: dict[str, str] = {}
    api_root = client_root / "api"
    for path in api_root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        relative = path.relative_to(api_root).with_suffix("")
        module_path = ".".join([module_root, *relative.parts])
        module_index[path.stem] = module_path
    return module_index


def collect_pydantic_class_names(path: Path) -> tuple[list[str], list[str]]:
    module = ast.parse(path.read_text())
    class_names: list[str] = []
    enum_names: list[str] = []
    for node in module.body:
        if not isinstance(node, ast.ClassDef):
            continue
        class_names.append(node.name)
        base_names = {
            getattr(base, "id", None)
            or getattr(base, "attr", None)
            for base in node.bases
        }
        if {"Enum", "StrEnum"} & base_names:
            enum_names.append(node.name)
    return sorted(class_names), sorted(enum_names)


def pascal_case(value: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", value)
    collapsed: list[str] = []
    for part in parts:
        if not part:
            continue
        chunk = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", part)
        collapsed.extend(token.capitalize() for token in chunk.split())
    return "".join(collapsed)


def build_operation_model_candidates(operation_name: str) -> dict[str, list[str]]:
    base_name = pascal_case(operation_name)
    return {
        "request": [
            f"{base_name}Request",
            f"{base_name}RequestBody",
        ],
        "response": [
            f"{base_name}Response",
            f"{base_name}Response200",
            f"{base_name}ResponseDefault",
        ],
        "query_parameters": [f"{base_name}ParametersQuery"],
        "path_parameters": [f"{base_name}ParametersPath"],
        "header_parameters": [f"{base_name}ParametersHeader"],
    }
 

def collect_schema_ref_map(
    spec: dict[str, Any],
    *,
    class_names: set[str],
) -> dict[str, str]:
    refs: dict[str, str] = {}
    for name in (spec.get("components", {}) or {}).get("schemas", {}):
        if name in class_names:
            refs[f"#/components/schemas/{name}"] = name
    return refs


def postprocess_pydantic_models(*, app_name: str, path: Path) -> None:
    content = path.read_text()
    patched = content
    if app_name == "jira":
        for before, after in JIRA_JSON_NODE_PATCHES.items():
            patched = patched.replace(before, after)
    if app_name == "gmail":
        for before, after in GMAIL_PYDANTIC_MODEL_PATCHES.items():
            patched = patched.replace(before, after)
    if app_name == "slack":
        for before, after in SLACK_PYDANTIC_MODEL_PATCHES.items():
            patched = patched.replace(before, after)
    if patched != content:
        path.write_text(patched)


def generate_metadata(
    spec: dict[str, Any],
    *,
    module_index: dict[str, str],
    app_name: str,
) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    for path, path_item in spec.get("paths", {}).items():
        shared_parameters = path_item.get("parameters", [])
        for method, operation in path_item.items():
            if method == "parameters" or method.startswith("x-"):
                continue
            operation_id = operation.get("operationId", "")
            tags = operation.get("tags", [])
            if app_name == "slack" and (
                operation_id.startswith("admin.")
                or any(tag.startswith("admin") for tag in tags)
            ):
                continue
            parameters = []
            for parameter in [*shared_parameters, *operation.get("parameters", [])]:
                entry = build_parameter_entry(spec, parameter)
                if entry:
                    parameters.append(entry)

            request_body = None
            if operation.get("requestBody"):
                body = resolve_once(spec, operation["requestBody"])
                content_type, schema, schema_ref = pick_content_schema(spec, body.get("content"))
                request_body = {
                    "required": body.get("required", False),
                    "content_type": content_type,
                    "schema": schema,
                    "schema_ref": schema_ref,
                }

            response_schema = {"type": "object", "additionalProperties": True}
            response_schema_ref = None
            response_content_type = "application/json"
            binary_response_hint = False
            preferred_response = pick_success_response(operation)
            if preferred_response:
                preferred_response = resolve_once(spec, preferred_response)
                response_content = preferred_response.get("content")
                success_status_codes = {
                    str(status_code)
                    for status_code in (operation.get("responses", {}) or {})
                    if str(status_code).startswith("2") or str(status_code) == "303"
                }
                preferred_types = None
                binary_response_hint = prefers_binary_response(
                    operation_id=operation_id,
                    path=path,
                    content=response_content,
                    success_status_codes=success_status_codes,
                )
                if binary_response_hint:
                    preferred_types = [
                        "application/octet-stream",
                        "image/png",
                        "image/svg+xml",
                        "text/plain",
                        "*/*",
                        "application/json",
                    ]
                (
                    response_content_type,
                    response_schema,
                    response_schema_ref,
                ) = pick_content_schema(
                    spec,
                    response_content,
                    preferred_types=preferred_types,
                )

            operation_name = build_tool_name(operation.get("operationId", ""), method, path)
            module_path = module_index.get(operation_name)
            if not module_path:
                continue

            tools.append(
                {
                    "name": operation_name,
                    "method": method.upper(),
                    "path": path,
                    "module_path": module_path,
                    "title": operation.get("operationId", path),
                    "operation_id": operation.get("operationId"),
                    "description": operation.get("description")
                    or operation.get("summary")
                    or operation.get("operationId")
                    or path,
                    "tags": tags,
                    "deprecated": operation.get("deprecated", False),
                    "parameters": parameters,
                    "request_body": request_body,
                    "response_schema": response_schema,
                    "response_schema_ref": response_schema_ref,
                    "response_content_type": response_content_type,
                    "binary_response_hint": binary_response_hint,
                }
            )

    tools.sort(key=lambda item: item["name"])
    return {"tools": tools}


def sanitize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    spec = copy.deepcopy(spec)
    for path_item in spec.get("paths", {}).values():
        for method, operation in list(path_item.items()):
            if method == "parameters" or not isinstance(operation, dict):
                continue
            request_body = operation.get("requestBody")
            if not isinstance(request_body, dict):
                continue
            content = request_body.get("content")
            if not isinstance(content, dict) or not content:
                continue
            if "application/json" not in content:
                first_schema = next(iter(content.values()))
                content["application/json"] = first_schema
    return spec


def filter_spec_for_app(spec: dict[str, Any], *, app_name: str) -> dict[str, Any]:
    if app_name != "slack":
        return spec

    filtered_paths: dict[str, Any] = {}
    for path, path_item in spec.get("paths", {}).items():
        new_path_item: dict[str, Any] = {}
        if "parameters" in path_item:
            new_path_item["parameters"] = path_item["parameters"]
        for method, operation in path_item.items():
            if method == "parameters" or method.startswith("x-"):
                continue
            operation_id = operation.get("operationId", "")
            tags = operation.get("tags", [])
            if operation_id.startswith("admin.") or any(tag.startswith("admin") for tag in tags):
                continue
            new_path_item[method] = operation
        if any(key != "parameters" for key in new_path_item):
            filtered_paths[path] = new_path_item

    spec["paths"] = filtered_paths
    return spec


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--generated-client-output", required=True)
    parser.add_argument("--pydantic-models-output", required=True)
    parser.add_argument("--pydantic-registry-output", required=True)
    parser.add_argument("--module-root", required=True)
    args = parser.parse_args()

    spec = sanitize_spec(json.loads(Path(args.spec).read_text()))
    spec = filter_spec_for_app(spec, app_name=args.app)
    output_client_root = Path(args.generated_client_output)
    output_pydantic_models_path = Path(args.pydantic_models_output)
    output_registry_path = Path(args.pydantic_registry_output)
    output_client_root.parent.mkdir(parents=True, exist_ok=True)
    output_pydantic_models_path.parent.mkdir(parents=True, exist_ok=True)
    output_registry_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"{args.app}_openapi_") as temp_dir:
        temp_spec_path = Path(temp_dir) / f"{args.app}.sanitized.json"
        temp_client_path = Path(temp_dir) / "client"
        temp_pydantic_models_path = Path(temp_dir) / "pydantic_models.py"
        temp_config_path = Path(temp_dir) / "openapi_python_client.json"
        temp_spec_path.write_text(json.dumps(spec))
        temp_config_path.write_text(
            json.dumps(
                {
                    "content_type_overrides": CONTENT_TYPE_OVERRIDES,
                    "use_path_prefixes_for_title_model_names": True,
                    "post_hooks": [],
                    "docstrings_on_attributes": False,
                }
            )
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "openapi_python_client",
                "generate",
                "--path",
                str(temp_spec_path),
                "--config",
                str(temp_config_path),
                "--meta",
                "none",
                "--output-path",
                str(temp_client_path),
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(temp_spec_path),
                "--input-file-type",
                "openapi",
                "--openapi-scopes",
                "schemas",
                "paths",
                "parameters",
                "requestbodies",
                "--output",
                str(temp_pydantic_models_path),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.14",
                "--snake-case-field",
                "--use-operation-id-as-name",
                "--use-schema-description",
                "--use-field-description",
                "--use-default-kwarg",
                "--field-constraints",
                "--enum-field-as-literal",
                "one",
                "--extra-fields",
                "forbid",
                "--collapse-root-models",
                "--reuse-model",
                "--disable-timestamp",
            ],
            check=True,
        )
        if output_client_root.exists():
            shutil.rmtree(output_client_root)
        shutil.copytree(temp_client_path, output_client_root)
        shutil.copyfile(temp_pydantic_models_path, output_pydantic_models_path)
        postprocess_pydantic_models(app_name=args.app, path=output_pydantic_models_path)

    module_index = build_module_index(output_client_root, args.module_root)
    metadata = generate_metadata(spec, module_index=module_index, app_name=args.app)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    class_names, enum_names = collect_pydantic_class_names(output_pydantic_models_path)
    pydantic_module_path = args.module_root.rsplit(".client.api", 1)[0] + ".pydantic_models"
    registry = {
        "module_path": pydantic_module_path,
        "class_names": class_names,
        "enum_names": enum_names,
        "schema_refs": collect_schema_ref_map(spec, class_names=set(class_names)),
        "operations": {
            item["name"]: {
                key: next(
                    (candidate for candidate in candidates if candidate in class_names),
                    None,
                )
                for key, candidates in build_operation_model_candidates(item["name"]).items()
            }
            for item in metadata["tools"]
        },
    }
    output_registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(metadata['tools'])} tools for {args.app} to {output_path}")


if __name__ == "__main__":
    main()
