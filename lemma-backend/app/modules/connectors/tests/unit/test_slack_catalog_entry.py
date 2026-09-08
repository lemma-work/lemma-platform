"""Properties of the committed Slack catalog entry.

The entry is generated offline by `scripts/generate_slack_static_operations.py`
from Slack's own OpenAPI description. That script is not on the runtime import
path, so nothing else would notice a regeneration that put the `token`
credential back into a tool schema, lost the response envelope, or let a form
body collapse into a single opaque file again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CONFIG = Path(__file__).resolve().parents[5] / "scripts" / "lemma_apps_config.json"


@pytest.fixture(scope="module")
def slack_entry() -> dict:
    apps = json.loads(_CONFIG.read_text(encoding="utf-8"))
    return next(app for app in apps if app["name"] == "slack")


@pytest.fixture(scope="module")
def slack_operations(slack_entry) -> list[dict]:
    return slack_entry["static_operations"]


def test_slack_installs_as_http(slack_entry):
    """The kind is what routes an operation to the OpenAPI executor.

    Left unset it defaults to the vendored-package kind, which is what every
    native connector was before the tenant-configured kinds existed -- and
    there is no vendored Slack client any more.
    """
    assert slack_entry["kind"] == "http"


def test_the_curated_set_covers_a_conversation_end_to_end(slack_operations):
    names = {op["name"] for op in slack_operations}
    for required in (
        "auth_test",
        "chat_post_message",
        "chat_update",
        "conversations_list",
        "conversations_history",
        "conversations_replies",
        "conversations_members",
        "users_list",
        "users_lookup_by_email",
        "users_profile_get",
        "search_messages",
        "slack_http_request",
    ):
        assert required in names, required


def test_the_names_the_vendored_client_published_all_survive(slack_operations):
    """The migration renames nothing.

    `build_tool_name` reproduces the vendored client's names exactly, which is
    why existing grants, pod bundles and the live Slack journey keep working.
    These four are the ones something in the tree names by hand.
    """
    names = {op["name"] for op in slack_operations}
    assert {
        "chat_post_message",
        "conversations_list",
        "conversations_history",
        "auth_test",
    } <= names


def test_no_operation_asks_an_agent_for_a_token(slack_operations):
    """Slack declares `token` on 155 operations and requires it on 112.

    The executor sends the credential as a bearer header. Left in the schema it
    asks an agent for a secret it does not have, and where it is required the
    call fails validation before it is ever made.
    """
    for op in slack_operations:
        assert "token" not in op["input_schema"]["properties"], op["name"]
        assert "token" not in op["input_schema"].get("required", []), op["name"]
        execution = op["execution"]
        assert "token" not in (execution.get("header_params") or []), op["name"]
        query = {q["name"] for q in execution.get("query_params") or []}
        assert "token" not in query, op["name"]


def test_every_post_sends_a_form_body_not_a_file(slack_operations):
    """The regression that made all 89 of Slack's POST operations unusable.

    Anything that was not JSON or multipart fell through to the single-blob
    case, so the descriptor named the whole body a binary field and the executor
    sent raw bytes to an API that wanted form values.
    """
    for op in slack_operations:
        execution = op["execution"]
        body = execution.get("request_body")
        if execution.get("mode") != "openapi" or not body:
            continue
        assert body["content_type"] == "application/x-www-form-urlencoded", op["name"]
        assert body["binary_fields"] == [], op["name"]
        assert body["form_fields"], op["name"]


def test_every_operation_carries_the_response_envelope(slack_operations):
    """Slack reports failure in a 200 body; without this it reads as success."""
    for op in slack_operations:
        if op["execution"].get("mode") != "openapi":
            continue
        envelope = op["execution"]["response"].get("envelope")
        assert envelope, op["name"]
        assert envelope["success_field"] == "ok"
        assert envelope["error_field"] == "error"
        assert envelope["status_by_error"]["invalid_auth"] == 401
        assert envelope["status_by_error"]["channel_not_found"] == 404
        assert envelope["default_status"] == 400


def test_nothing_administrative_or_destructive_is_exposed(slack_operations):
    """Each of these can be done by a person in Slack, and none by an agent.

    They end the install, hand a workspace's files to the public, remove people
    from channels, or rewrite the account holder's own identity.
    """
    names = {op["name"] for op in slack_operations}
    for forbidden in (
        "admin_users_remove",
        "admin_conversations_delete",
        "admin_apps_restrict",
        "oauth_v2_access",
        "auth_revoke",
        "apps_uninstall",
        "conversations_archive",
        "conversations_kick",
        "files_shared_public_url",
        "users_profile_set",
        "users_set_photo",
        "users_set_presence",
        "usergroups_create",
        "usergroups_update",
        "team_access_logs",
        "team_integration_logs",
    ):
        assert forbidden not in names, forbidden


def test_file_upload_is_absent(slack_operations):
    """Slack retired `files.upload` in March 2025.

    The committed spec predates its replacement, so there is no upload path to
    offer and exposing the dead one would ship an operation that always fails.
    """
    assert "files_upload" not in {op["name"] for op in slack_operations}


def test_output_schemas_stay_pruned(slack_operations):
    """One `describe_connector_operation` should not cost thousands of tokens."""
    for op in slack_operations:
        schema = op.get("output_schema")
        if not isinstance(schema, dict):
            continue
        for prop in (schema.get("properties") or {}).values():
            assert set(prop) <= {"type"}, op["name"]


def test_the_entry_stays_small_enough_to_read(slack_operations):
    total = len(json.dumps(slack_operations))
    biggest = max(len(json.dumps(op)) for op in slack_operations)
    assert total < 150_000, total
    assert biggest < 8_000, biggest
