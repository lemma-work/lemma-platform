"""One-time offline generator for the Slack connector's ``static_operations``.

Not part of the runtime import path (``import_connector_catalog.py`` never
imports this module). Run by hand whenever the curated operation set changes,
and the output is spliced into ``lemma_apps_config.json``'s ``"slack"`` entry.

The spec is Slack's own OpenAPI description, committed at
``lemma-backend/openapi_specs/slack.json``. Unlike GitHub's it is *not* fetched
on demand: Slack publishes no maintained machine-readable description, so this
copy is the one we have. It is a 2020 document (``info.version: 1.7.0``) and it
shows -- see ``KNOWN SPEC GAPS`` below.

Three things make Slack unlike GitHub, and all three are handled by the shared
OpenAPI machinery rather than here:

* Its Web API is form-encoded RPC, not REST: 89 of its 174 operations post an
  ``application/x-www-form-urlencoded`` body to a flat ``/method`` path.
* It answers *every* call with HTTP 200 and reports failure in the body, so the
  connector declares a ``RESPONSE_ENVELOPE`` and the executor raises on it.
* It declares a ``token`` parameter on 155 operations and marks it required on
  112. We always send the credential as a bearer header, so the parameter is
  dropped rather than asked of an agent.

Usage::

    uv run python scripts/generate_slack_static_operations.py
    uv run python scripts/generate_slack_static_operations.py --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from app.modules.connectors.infrastructure.openapi.spec_import import (  # noqa: E402
    build_operation_descriptors,
    build_raw_passthrough,
)
from scripts._openapi_static_operations import (  # noqa: E402
    LEMMA_APPS_CONFIG_PATH,
    load_spec,
    operation_to_static_entry,
    write_into_lemma_apps_config,
)

CONNECTOR_ID = "slack"
SERVER_URL = "https://slack.com/api"
RAW_PASSTHROUGH_NAME = "slack_http_request"
DEFAULT_HEADERS = {"User-Agent": "lemma-connectors"}

# Slack accepts the credential as a `token` form/query/header parameter as well
# as a bearer header. We always send the header, so leaving the parameter in the
# tool schema asks an agent for a credential it does not have -- and on the 112
# operations that mark it required, schema validation fails before the call.
DROP_PARAMETERS = frozenset({"token"})

# Slack signals failure inside a 200 body. Mapping its own error codes onto HTTP
# statuses is what lets `failure_translation` classify a Slack failure exactly as
# it classifies a real 401 or 404 from any other connector -- and what lets the
# operation breaker count an outage as an outage rather than as a bad request.
RESPONSE_ENVELOPE = {
    "success_field": "ok",
    "error_field": "error",
    "status_by_error": {
        # Who you are.
        "invalid_auth": 401,
        "not_authed": 401,
        "token_revoked": 401,
        "token_expired": 401,
        "account_inactive": 401,
        # What you may do.
        "missing_scope": 403,
        "no_permission": 403,
        "not_allowed_token_type": 403,
        "restricted_action": 403,
        "ekm_access_denied": 403,
        "org_login_required": 403,
        # What you asked for.
        "channel_not_found": 404,
        "user_not_found": 404,
        "users_not_found": 404,
        "message_not_found": 404,
        "file_not_found": 404,
        "thread_not_found": 404,
        "file_comment_not_found": 404,
        # Slow down.
        "ratelimited": 429,
        "rate_limited": 429,
        # Theirs, not ours -- and the only ones the breaker should count.
        "fatal_error": 503,
        "internal_error": 503,
        "service_unavailable": 503,
        "request_timeout": 504,
    },
    "default_status": 400,
}

# Curated from the 174 operations the spec describes. The rules, in the order
# they were applied:
#
#   * Everything under `admin.*` (56 operations) -- workspace administration,
#     already excluded from the connector package this replaces.
#   * `oauth.*`, which exchanges client secrets, and `apps.uninstall` /
#     `auth.revoke`, which end the install the caller is using.
#   * `views.*`, `dialog.*` and `workflows.*`: every one needs a `trigger_id` or
#     `view_id` that only arrives in an interaction payload, which an agent
#     calling an operation does not have. (The spec also declares them GET,
#     which they are not.)
#   * `calls.*` (a third-party call-provider API), `files.remote.*`, `stars.*`
#     and `rtm.*` (websocket) -- surfaces with no agent use.
#   * `team.accessLogs`, `team.billableInfo`, `team.integrationLogs`: workspace
#     surveillance.
#   * Anything that mutates the human's own identity or presence
#     (`users.profile.set`, `users.setPhoto`, `users.setPresence`, `dnd.setSnooze`
#     and friends), makes a file world-readable (`files.sharedPublicURL`), or is
#     the Slack equivalent of deleting a repository (`conversations.archive`,
#     `conversations.kick`, the `usergroups` writes).
#
# `files.upload` is deliberately absent: Slack retired it in March 2025 and this
# spec predates its replacement (`files.getUploadURLExternal` /
# `completeUploadExternal`), so there is no upload path to offer. The external
# flow cannot be reached through the raw passthrough either -- the byte PUT goes
# to files.slack.com, and the passthrough refuses to leave the connector's own
# origin with the install's credentials attached.
ALLOWLIST = [
    # Identity and workspace.
    {"operation_id": "auth_test"},
    {"operation_id": "bots_info"},
    {"operation_id": "team_info"},
    {"operation_id": "team_profile_get"},
    {"operation_id": "emoji_list"},
    # Reading and writing messages -- the reason the connector exists.
    {"operation_id": "chat_postMessage"},
    {"operation_id": "chat_postEphemeral"},
    {"operation_id": "chat_update"},
    {"operation_id": "chat_delete"},
    {"operation_id": "chat_meMessage"},
    {"operation_id": "chat_getPermalink"},
    {"operation_id": "chat_scheduleMessage"},
    {"operation_id": "chat_deleteScheduledMessage"},
    {"operation_id": "chat_scheduledMessages_list"},
    {"operation_id": "search_messages"},
    # Channels, threads and membership.
    {"operation_id": "conversations_list"},
    {"operation_id": "conversations_info"},
    {"operation_id": "conversations_history"},
    {"operation_id": "conversations_replies"},
    {"operation_id": "conversations_members"},
    {"operation_id": "conversations_create"},
    {"operation_id": "conversations_join"},
    {"operation_id": "conversations_leave"},
    {"operation_id": "conversations_invite"},
    {"operation_id": "conversations_open"},
    {"operation_id": "conversations_close"},
    {"operation_id": "conversations_rename"},
    {"operation_id": "conversations_setPurpose"},
    {"operation_id": "conversations_setTopic"},
    {"operation_id": "conversations_mark"},
    # People.
    {"operation_id": "users_list"},
    {"operation_id": "users_info"},
    {"operation_id": "users_lookupByEmail"},
    {"operation_id": "users_profile_get"},
    {"operation_id": "users_conversations"},
    {"operation_id": "users_getPresence"},
    {"operation_id": "usergroups_list"},
    {"operation_id": "usergroups_users_list"},
    {"operation_id": "dnd_info"},
    {"operation_id": "dnd_teamInfo"},
    # Files: read and remove, no upload (see above), no public sharing.
    {"operation_id": "files_list"},
    {"operation_id": "files_info"},
    {"operation_id": "files_delete"},
    {"operation_id": "files_revokePublicURL"},
    # Reactions, pins and reminders.
    {"operation_id": "reactions_add"},
    {"operation_id": "reactions_remove"},
    {"operation_id": "reactions_get"},
    {"operation_id": "reactions_list"},
    {"operation_id": "pins_add"},
    {"operation_id": "pins_remove"},
    {"operation_id": "pins_list"},
    {"operation_id": "reminders_add"},
    {"operation_id": "reminders_complete"},
    {"operation_id": "reminders_delete"},
    {"operation_id": "reminders_info"},
    {"operation_id": "reminders_list"},
]

OVERRIDES: dict[str, dict] = {}


def build_static_operations() -> list[dict]:
    spec = load_spec(CONNECTOR_ID)
    operations = build_operation_descriptors(
        spec,
        server_url=SERVER_URL,
        allowlist=ALLOWLIST,
        overrides=OVERRIDES,
        default_headers=DEFAULT_HEADERS,
        drop_parameters=DROP_PARAMETERS,
        response_envelope=RESPONSE_ENVELOPE,
    )
    raw = build_raw_passthrough(
        CONNECTOR_ID,
        server_url=SERVER_URL,
        name=RAW_PASSTHROUGH_NAME,
        default_headers=DEFAULT_HEADERS,
    )
    return [operation_to_static_entry(op) for op in [*operations, raw]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Splice the generated static_operations directly into the "
        "existing 'slack' entry in lemma_apps_config.json.",
    )
    args = parser.parse_args()

    static_operations = build_static_operations()

    if args.write:
        write_into_lemma_apps_config(CONNECTOR_ID, static_operations)
        print(
            f"Wrote {len(static_operations)} operations into "
            f"{LEMMA_APPS_CONFIG_PATH}'s 'slack' entry."
        )
    else:
        print(json.dumps(static_operations, indent=2))


if __name__ == "__main__":
    main()
