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
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Node"}
                            }
                        }
                    }
                },
            }
        },
        "/repos/{owner}/{repo}/issues": {
            "parameters": [
                {
                    "name": "owner",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "name": "repo",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
            "get": {
                "operationId": "issues/list-for-repo",
                "parameters": [
                    {
                        "name": "labels",
                        "in": "query",
                        "required": False,
                        "style": "form",
                        "explode": False,
                        "schema": {"type": "array", "items": {"type": "string"}},
                    },
                ],
                "responses": {
                    "200": {
                        "content": {"application/json": {"schema": {"type": "array"}}}
                    }
                },
            },
            "post": {
                "operationId": "issues/create",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Issue"}
                        }
                    },
                },
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Issue"}
                            }
                        }
                    }
                },
            },
        },
        "/repos/{owner}/{repo}/releases/{release_id}/assets": {
            "post": {
                "operationId": "repos/upload-release-asset",
                "parameters": [
                    {
                        "name": "owner",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "repo",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "release_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    },
                    {
                        "name": "name",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    },
                },
                "responses": {
                    "201": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    }
                },
            }
        },
        "/repos/{owner}/{repo}/tarball/{ref}": {
            "get": {
                "operationId": "repos/download-tarball-archive",
                "parameters": [
                    {
                        "name": "owner",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "repo",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "ref",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {
                    "200": {
                        "content": {
                            "application/octet-stream": {"schema": {"type": "string"}}
                        }
                    }
                },
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
        build_operation_descriptors(
            SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST
        )
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
        build_operation_descriptors(
            SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST
        )
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
        build_operation_descriptors(
            SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST
        )
    )
    op = ops["repos_upload_release_asset"]
    rb = op.execution["request_body"]
    # Regression guard: content type must NOT be forced to application/json.
    assert rb["content_type"] == "application/octet-stream"
    assert rb["binary_fields"] == ["body"]
    assert "oneOf" in op.input_schema["properties"]["body"]  # file-input schema


def test_binary_download_adds_output_path_and_flags_response():
    ops = _by_name(
        build_operation_descriptors(
            SAMPLE_SPEC, server_url="https://api.example.com", allowlist=ALLOWLIST
        )
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
            overrides={
                "users/get-authenticated": {"name": "whoami", "binary_response": True}
            },
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
    assert ops["users_get_authenticated"].execution["default_headers"] == {
        "User-Agent": "lemma"
    }


# --- what Slack and Gmail needed that GitHub did not ------------------------

FORM_SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://slack.com/api"}],
    "paths": {
        "/chat.postMessage": {
            "post": {
                "operationId": "chat_postMessage",
                "summary": "Send a message",
                "parameters": [
                    {
                        "name": "token",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {
                    "content": {
                        "application/x-www-form-urlencoded": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "channel": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["channel"],
                            }
                        }
                    }
                },
                "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
            }
        },
        "/conversations.list": {
            "get": {
                "operationId": "conversations_list",
                "parameters": [
                    {"name": "token", "in": "query", "schema": {"type": "string"}},
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                ],
                "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
            }
        },
    },
}


def _form_ops(**kwargs):
    return _by_name(
        build_operation_descriptors(
            FORM_SPEC, server_url="https://slack.com/api", allowlist=None, **kwargs
        )
    )


def test_a_form_urlencoded_body_becomes_form_fields_not_a_single_blob():
    """Slack's 89 POST operations were every one of them declared a file.

    Anything that was not JSON or multipart fell through to the blob case, so
    the descriptor named the whole body a binary field and the executor sent
    raw bytes to an API that wanted form values.
    """
    body = _form_ops()["chat_post_message"].execution["request_body"]

    assert body["content_type"] == "application/x-www-form-urlencoded"
    assert body["binary_fields"] == []
    assert sorted(body["form_fields"]) == ["channel", "text"]


def test_a_form_body_keeps_its_properties_in_the_input_schema():
    props = _form_ops()["chat_post_message"].input_schema["properties"]["body"]

    assert sorted(props["properties"]) == ["channel", "text"]
    assert props["required"] == ["channel"]


def test_drop_parameters_removes_the_name_from_every_place_it_appears():
    """`token` is a credential the executor supplies as a bearer header.

    Left in, the tool schema asks an agent for it, and on the operations that
    mark it required validation fails before the call is ever made.
    """
    ops = _form_ops(drop_parameters={"token"})

    post = ops["chat_post_message"]
    assert "token" not in post.input_schema["properties"]
    assert "token" not in post.input_schema.get("required", [])
    assert post.execution["header_params"] == []

    get = ops["conversations_list"]
    assert "token" not in get.input_schema["properties"]
    assert [q["name"] for q in get.execution["query_params"]] == ["limit"]


def test_without_drop_parameters_the_declared_token_is_still_there():
    post = _form_ops()["chat_post_message"]

    assert "token" in post.input_schema["properties"]
    assert post.execution["header_params"] == ["token"]


def test_a_response_envelope_is_recorded_on_every_operation():
    envelope = {"success_field": "ok", "error_field": "error", "default_status": 400}
    ops = _form_ops(response_envelope=envelope)

    for op in ops.values():
        assert op.execution["response"]["envelope"] == envelope


def test_no_envelope_is_recorded_when_none_is_declared():
    for op in _form_ops().values():
        assert "envelope" not in op.execution["response"]


MEDIA_SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://gmail.googleapis.com"}],
    "paths": {
        "/gmail/v1/users/{userId}/messages/send": {
            "post": {
                "operationId": "gmail.users.messages.send",
                "parameters": [
                    {
                        "name": "userId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                # Twenty `message/*` variants and no JSON, exactly as Gmail
                # declares it. `pick_content_schema` takes the first entry.
                "requestBody": {
                    "content": {
                        "message/cpim": {
                            "schema": {
                                "type": "object",
                                "properties": {"raw": {"type": "string"}},
                            }
                        },
                        "message/rfc822": {
                            "schema": {
                                "type": "object",
                                "properties": {"raw": {"type": "string"}},
                            }
                        },
                    }
                },
                "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
            }
        }
    },
}


def test_body_content_type_override_sends_a_media_typed_body_as_json():
    ops = _by_name(
        build_operation_descriptors(
            MEDIA_SPEC,
            server_url="https://gmail.googleapis.com",
            allowlist=None,
            overrides={
                "gmail.users.messages.send": {
                    "name": "messages_send",
                    "body_content_type": "application/json",
                }
            },
        )
    )
    op = ops["messages_send"]
    body = op.execution["request_body"]

    assert body["content_type"] == "application/json"
    assert body["binary_fields"] == []
    # The schema still comes from the media-typed entry, which is where Gmail
    # documents the message shape.
    assert "raw" in op.input_schema["properties"]["body"]["properties"]


def test_without_the_override_gmails_send_body_collapses_to_a_file():
    """Records the defect the override exists to avoid."""
    body = _by_name(
        build_operation_descriptors(
            MEDIA_SPEC, server_url="https://gmail.googleapis.com", allowlist=None
        )
    )["gmail_users_messages_send"].execution["request_body"]

    assert body["content_type"] == "message/cpim"
    assert body["binary_fields"] == ["body"]


def test_a_path_param_default_is_recorded_and_stops_being_required():
    ops = _by_name(
        build_operation_descriptors(
            MEDIA_SPEC,
            server_url="https://gmail.googleapis.com",
            allowlist=None,
            overrides={
                "gmail.users.messages.send": {
                    "name": "messages_send",
                    "path_param_defaults": {"userId": "me"},
                }
            },
        )
    )
    op = ops["messages_send"]

    assert op.execution["path_param_defaults"] == {"userId": "me"}
    assert "userId" not in op.input_schema.get("required", [])
    # Still offered, so a caller can address another mailbox.
    assert "userId" in op.input_schema["properties"]


def test_a_default_for_a_parameter_the_operation_does_not_have_is_ignored():
    ops = _by_name(
        build_operation_descriptors(
            MEDIA_SPEC,
            server_url="https://gmail.googleapis.com",
            allowlist=None,
            overrides={
                "gmail.users.messages.send": {
                    "name": "messages_send",
                    "path_param_defaults": {"userId": "me", "nonsense": "x"},
                }
            },
        )
    )

    assert ops["messages_send"].execution["path_param_defaults"] == {"userId": "me"}
