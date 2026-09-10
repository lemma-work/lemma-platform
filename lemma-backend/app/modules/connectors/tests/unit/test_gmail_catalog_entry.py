"""Properties of the committed Gmail catalog entry.

The entry is generated offline by `scripts/generate_gmail_static_operations.py`
from Google's Gmail v1 description. That script is not on the runtime import
path, so nothing else would notice a regeneration that renamed every operation,
turned the send body back into an opaque file, or quietly started offering the
settings writes this connector deliberately withholds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CONFIG = Path(__file__).resolve().parents[5] / "scripts" / "lemma_apps_config.json"


@pytest.fixture(scope="module")
def gmail_entry() -> dict:
    apps = json.loads(_CONFIG.read_text(encoding="utf-8"))
    return next(app for app in apps if app["name"] == "gmail")


@pytest.fixture(scope="module")
def gmail_operations(gmail_entry) -> list[dict]:
    return gmail_entry["static_operations"]


def test_gmail_installs_as_http_with_its_own_oauth(gmail_entry):
    """Gmail had no catalog entry at all: it existed only because the vendored
    package phase created a row for it, and its OAuth endpoints lived in code."""
    assert gmail_entry["kind"] == "http"
    assert gmail_entry["auth_method"] == "OAUTH2"
    oauth = gmail_entry["oauth2_config"]
    assert oauth["token_url"] == "https://oauth2.googleapis.com/token"
    # Without these Google never returns a refresh token and the account dies
    # at the first expiry.
    assert oauth["extra_params"] == {"access_type": "offline", "prompt": "consent"}
    assert "https://www.googleapis.com/auth/gmail.modify" in oauth["default_scopes"]
    assert gmail_entry["system_oauth"]["client_id_env"] == [
        "CONNECTOR_GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_ID",
    ]


def test_no_operation_keeps_the_gmail_users_prefix(gmail_operations):
    """Every Gmail operationId is `gmail.users.…`, so the derived names would
    all read `gmail_users_messages_send`. Stripping the prefix also reproduces
    the vendored client's names, so the migration renames nothing."""
    for op in gmail_operations:
        assert not op["name"].startswith("gmail_users_"), op["name"]


def test_the_curated_set_covers_a_mailbox_end_to_end(gmail_operations):
    names = {op["name"] for op in gmail_operations}
    for required in (
        "get_profile",
        "messages_list",
        "messages_get",
        "messages_send",
        "messages_modify",
        "messages_attachments_get",
        "threads_get",
        "threads_list",
        "drafts_create",
        "drafts_send",
        "labels_list",
        "gmail_http_request",
    ):
        assert required in names, required


def test_the_send_and_draft_bodies_are_json_not_an_opaque_blob(gmail_operations):
    """Gmail describes these bodies with twenty `message/*` types and no JSON.

    Left alone the content-type preference cannot help, the spec's first entry
    wins -- `message/cpim` -- and the body collapses to a single file field.
    """
    by_name = {op["name"]: op for op in gmail_operations}
    for name in ("messages_send", "drafts_create", "drafts_update", "drafts_send"):
        body = by_name[name]["execution"]["request_body"]
        assert body["content_type"] == "application/json", name
        assert body["binary_fields"] == [], name


def test_user_id_is_defaulted_rather_than_demanded(gmail_operations):
    """`userId` is required on all 79 operations and the only value is "me".

    A profile fetch runs with an empty payload, so without the default it dies
    on a missing path parameter before it reaches Google.
    """
    for op in gmail_operations:
        execution = op["execution"]
        if "userId" not in (execution.get("path_params") or []):
            continue
        assert execution["path_param_defaults"]["userId"] == "me", op["name"]
        assert "userId" not in op["input_schema"].get("required", []), op["name"]
        # Still offered, so another mailbox can be addressed where the token allows.
        assert "userId" in op["input_schema"]["properties"], op["name"]


def test_the_profile_operation_is_the_one_the_catalog_names(gmail_operations):
    """`connector_profile_operations.json` names `get_profile` under LEMMA.

    It was unreachable before: the vendored path short-circuited it under a
    name -- `users_get_profile` -- that no operation ever had.
    """
    profiles = json.loads(
        (
            Path(__file__).resolve().parents[5]
            / "scripts"
            / "connector_profile_operations.json"
        ).read_text(encoding="utf-8")
    )
    names = {op["name"] for op in gmail_operations}
    for operation_name in profiles["gmail"]["LEMMA"]:
        assert operation_name in names, operation_name


def test_nothing_that_creates_a_standing_rule_or_forges_mail_is_exposed(
    gmail_operations,
):
    """Two different hazards, both permanent and both invisible afterwards.

    A forwarding address or filter is a standing instruction that outlives the
    conversation that created it and quietly copies future mail elsewhere.
    `import`/`insert` write mail into the mailbox without sending it, and the
    hard deletes bypass the trash. The reads stay, so an agent can still notice
    a rule exists and say so.
    """
    names = {op["name"] for op in gmail_operations}
    for forbidden in (
        "settings_forwarding_addresses_create",
        "settings_forwarding_addresses_delete",
        "settings_update_auto_forwarding",
        "settings_filters_create",
        "settings_filters_delete",
        "settings_delegates_create",
        "settings_send_as_create",
        "settings_send_as_update",
        "settings_send_as_verify",
        "settings_cse_keypairs_obliterate",
        "messages_import",
        "messages_insert",
        "messages_delete",
        "messages_batch_delete",
        "threads_delete",
        "watch",
        "stop",
    ):
        assert forbidden not in names, forbidden

    # The reversible equivalents are offered instead.
    assert {"messages_trash", "messages_untrash", "threads_trash"} <= names
    # And the reads that let an agent report on a rule it cannot create.
    assert {"settings_filters_list", "settings_get_auto_forwarding"} <= names


def test_output_schemas_stay_pruned(gmail_operations):
    for op in gmail_operations:
        schema = op.get("output_schema")
        if not isinstance(schema, dict):
            continue
        for prop in (schema.get("properties") or {}).values():
            assert set(prop) <= {"type"}, op["name"]


def test_the_entry_stays_small_enough_to_read(gmail_operations):
    total = len(json.dumps(gmail_operations))
    biggest = max(len(json.dumps(op)) for op in gmail_operations)
    assert total < 100_000, total
    assert biggest < 8_000, biggest
