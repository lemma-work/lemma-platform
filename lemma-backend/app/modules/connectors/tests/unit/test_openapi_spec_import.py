"""Unit tests for the package-free OpenAPI spec → operation descriptor import."""

from __future__ import annotations

from app.modules.connectors.infrastructure.openapi.spec_import import (
    build_operation_descriptors,
    build_raw_passthrough,
)


SAMPLE_SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://api.example.com"}],
    "components": {
        "schemas": {
            "Issue": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "title": {"type": "string"}},
            },
            # Self-referential schema to exercise cycle-safe $ref resolution.
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/components/schemas/Node"}},
            },
        }
    },
    "paths": {
        "/users/me": {
            "get": {
                "operationId": "users/get-authenticated",
                "summary": "Get the authenticated user",
                "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}}},
            }
        },
        "/repos/{owner}/{repo}/issues": {
            "parameters": [
                {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "get": {
                "operationId": "issues/list-for-repo",
                "parameters": [
                    {"name": "labels", "in": "query", "required": False, "style": "form", "explode": False, "schema": {"type": "array", "items": {"type": "string"}}},
                ],
                "responses": {"200": {"content": {"application/json": {"schema": {"type": "array"}}}}},
            },
            "post": {
                "operationId": "issues/create",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Issue"}}},
                },
                "responses": {"201": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Issue"}}}}},
            },
        },
        "/repos/{owner}/{repo}/releases/{release_id}/assets": {
            "post": {
                "operationId": "repos/upload-release-asset",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "release_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "name", "in": "query", "required": True, "schema": {"type": "string"}},
                ],
                "requestBody": {
                    "required": True,
                    "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
                },
                "responses": {"201": {"content": {"application/json": {"schema": {"type": "object"}}}}},
            }
        },
        "/repos/{owner}/{repo}/tarball/{ref}": {
            "get": {
                "operationId": "repos/download-tarball-archive",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "ref", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"content": {"application/octet-stream": {"schema": {"type": "string"}}}}},
            }
        },
        "/not/allowlisted": {
            "get": {"operationId": "misc/skip-me", "responses": {"200": {}}}
        },
    },
}

ALLOWLIST = [
    {"operation_id": "users/get-authenticated"},
    {"operation_id": "issues/list-for-repo"},
    {"operation_id": "issues/create"},
    {"operation_id": "repos/upload-release-asset"},
    {"method": "GET", "path": "/repos/{owner}/{repo}/tarball/{ref}"},
]


def _by_name(spec_ops):
    return {op.public_name: op for op in spec_ops}


def test_only_allowlisted_operations_selected():
    ops = build_operation_descriptors(
        SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST
    )
    names = _by_name(ops)
    assert set(names) == {
        "users_get_authenticated",
        "issues_list_for_repo",
        "issues_create",
        "repos_upload_release_asset",
        "repos_download_tarball_archive",
    }
    assert "misc_skip_me" not in names  # not allowlisted → excluded


def test_get_operation_splits_path_and_query():
    ops = _by_name(
        build_operation_descriptors(SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST)
    )
    op = ops["issues_list_for_repo"]
    hr = op.execution
    assert hr["mode"] == "openapi"
    assert hr["method"] == "GET"
    assert hr["path"] == "/repos/{owner}/{repo}/issues"
    assert set(hr["path_params"]) == {"owner", "repo"}
    assert [q["name"] for q in hr["query_params"]] == ["labels"]
    assert hr["query_params"][0]["style"] == "form"
    assert hr["query_params"][0]["explode"] is False
    assert set(op.input_schema["required"]) == {"owner", "repo"}
    assert op.input_schema["additionalProperties"] is False


def test_json_body_passthrough():
    ops = _by_name(
        build_operation_descriptors(SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST)
    )
    op = ops["issues_create"]
    rb = op.execution["request_body"]
    assert rb["content_type"] == "application/json"
    assert rb["binary_fields"] == []
    assert "body" in op.input_schema["required"]
    # JSON body schema preserved (ref resolved to the Issue object).
    assert op.input_schema["properties"]["body"]["type"] == "object"


def test_octet_stream_upload_marks_body_as_file():
    ops = _by_name(
        build_operation_descriptors(SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST)
    )
    op = ops["repos_upload_release_asset"]
    rb = op.execution["request_body"]
    # Regression guard: content type must NOT be forced to application/json.
    assert rb["content_type"] == "application/octet-stream"
    assert rb["binary_fields"] == ["body"]
    assert "oneOf" in op.input_schema["properties"]["body"]  # file-input schema


def test_binary_download_adds_output_path_and_flags_response():
    ops = _by_name(
        build_operation_descriptors(SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST)
    )
    op = ops["repos_download_tarball_archive"]
    assert op.execution["response"]["binary"] is True
    assert "output_path" in op.input_schema["properties"]


def test_override_binary_response_and_name():
    ops = _by_name(
        build_operation_descriptors(
            SAMPLE_SPEC,
            server_url="https://api.example.com",
            allowlist=[{"operation_id": "users/get-authenticated"}],
            overrides={"users/get-authenticated": {"name": "whoami", "binary_response": True}},
        )
    )
    assert "whoami" in ops
    assert ops["whoami"].execution["response"]["binary"] is True


def test_cycle_safe_ref_resolution_terminates():
    # The Node schema references itself; resolution must not recurse forever.
    ops = _by_name(
        build_operation_descriptors(
            SAMPLE_SPEC,
            server_url="https://api.example.com",
            allowlist=[{"operation_id": "users/get-authenticated"}],
        )
    )
    schema = ops["users_get_authenticated"].output_schema
    assert isinstance(schema, dict)  # resolved without blowing the stack


def test_raw_passthrough_shape():
    op = build_raw_passthrough("github", server_url="https://api.github.com")
    assert op.public_name == "github_http_request"
    assert op.execution["mode"] == "raw"
    assert op.execution["server_url"] == "https://api.github.com"
    assert set(op.input_schema["required"]) == {"method", "path"}
    assert "output_path" in op.input_schema["properties"]


def test_default_headers_propagate():
    ops = _by_name(
        build_operation_descriptors(
            SAMPLE_SPEC,
            server_url="https://api.example.com",
            allowlist=[{"operation_id": "users/get-authenticated"}],
            default_headers={"User-Agent": "lemma"},
        )
    )
    assert ops["users_get_authenticated"].execution["default_headers"] == {"User-Agent": "lemma"}
