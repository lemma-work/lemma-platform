"""One-time offline generator for the Gmail connector's ``static_operations``.

Not part of the runtime import path (``import_connector_catalog.py`` never
imports this module). Run by hand whenever the curated operation set changes,
and the output is spliced into ``lemma_apps_config.json``'s ``"gmail"`` entry.

The spec is Google's own Gmail v1 discovery document converted to OpenAPI,
committed at ``lemma-backend/openapi_specs/gmail.json``.

Two things about it need saying, both handled by the shared OpenAPI machinery:

* Every operationId is prefixed ``gmail.users.``, so the derived tool names
  would all read ``gmail_users_messages_send``. ``_public_name`` strips that
  prefix, which is also exactly the name the connector package this replaces
  produced -- so no operation is renamed by the migration.
* ``userId`` is a required path parameter on all 79 operations and the only
  value anyone passes is ``me``. It is declared as a ``path_param_defaults``
  entry so an agent need not supply it and a profile fetch with an empty payload
  still resolves. It stays in the input schema, so another mailbox can still be
  addressed where the token allows it.

Usage::

    uv run python scripts/generate_gmail_static_operations.py
    uv run python scripts/generate_gmail_static_operations.py --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from app.modules.connectors.infrastructure.openapi.spec_helpers import (  # noqa: E402
    build_tool_name,
)
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

CONNECTOR_ID = "gmail"
SERVER_URL = "https://gmail.googleapis.com"
RAW_PASSTHROUGH_NAME = "gmail_http_request"
DEFAULT_HEADERS = {"User-Agent": "lemma-connectors"}

# Curated from the 79 operations the spec describes.
#
# What is deliberately absent, and why:
#
#   * All 11 `settings.cse.*` operations -- client-side-encryption keypairs.
#     `obliterate` destroys a key irrecoverably.
#   * All 4 `settings.delegates.*` -- Workspace-admin mailbox delegation.
#   * Every forwarding and filter *write*: `forwardingAddresses.create`,
#     `updateAutoForwarding`, `filters.create`, `filters.delete`. A mail
#     forwarding rule is a standing instruction that outlives the conversation
#     that created it and quietly copies future mail elsewhere. The reads stay,
#     so an agent can still notice one exists and say so.
#   * `sendAs.create/delete/patch/update/verify` and the 5 `smimeInfo` routes --
#     these change which addresses the mailbox can send *as*.
#   * `messages.import` and `messages.insert`, which write mail into the mailbox
#     without sending it -- a forged-message primitive with no agent use.
#   * `messages.delete`, `messages.batchDelete` and `threads.delete`, which are
#     permanent and bypass the trash. `trash`/`untrash` cover the same intent
#     reversibly and are kept.
#   * `stop` and `watch` (Cloud Pub/Sub push, needs a GCP topic), and the
#     `imap`/`pop`/`language` account toggles.
ALLOWLIST = [
    # Identity.
    {"operation_id": "gmail.users.getProfile"},
    # Reading mail.
    {"operation_id": "gmail.users.messages.list"},
    {"operation_id": "gmail.users.messages.get"},
    {"operation_id": "gmail.users.messages.attachments.get"},
    {"operation_id": "gmail.users.threads.list"},
    {"operation_id": "gmail.users.threads.get"},
    {"operation_id": "gmail.users.history.list"},
    # Sending and drafting.
    {"operation_id": "gmail.users.messages.send"},
    {"operation_id": "gmail.users.drafts.create"},
    {"operation_id": "gmail.users.drafts.update"},
    {"operation_id": "gmail.users.drafts.send"},
    {"operation_id": "gmail.users.drafts.get"},
    {"operation_id": "gmail.users.drafts.list"},
    {"operation_id": "gmail.users.drafts.delete"},
    # Triage: labels, and the reversible removals.
    {"operation_id": "gmail.users.messages.modify"},
    {"operation_id": "gmail.users.messages.batchModify"},
    {"operation_id": "gmail.users.messages.trash"},
    {"operation_id": "gmail.users.messages.untrash"},
    {"operation_id": "gmail.users.threads.modify"},
    {"operation_id": "gmail.users.threads.trash"},
    {"operation_id": "gmail.users.threads.untrash"},
    {"operation_id": "gmail.users.labels.list"},
    {"operation_id": "gmail.users.labels.get"},
    {"operation_id": "gmail.users.labels.create"},
    {"operation_id": "gmail.users.labels.patch"},
    {"operation_id": "gmail.users.labels.update"},
    {"operation_id": "gmail.users.labels.delete"},
    # Settings an assistant legitimately reads or sets on its owner's behalf.
    {"operation_id": "gmail.users.settings.getVacation"},
    {"operation_id": "gmail.users.settings.updateVacation"},
    {"operation_id": "gmail.users.settings.sendAs.list"},
    {"operation_id": "gmail.users.settings.sendAs.get"},
    {"operation_id": "gmail.users.settings.filters.list"},
    {"operation_id": "gmail.users.settings.filters.get"},
    {"operation_id": "gmail.users.settings.getAutoForwarding"},
]

# Gmail describes the bodies of its send/draft routes with twenty `message/*`
# media types and no `application/json`, so the content-type preference list
# cannot help and the spec's first entry wins -- `message/cpim`, which reads as
# a single opaque blob and turns the body into a file. Naming the type keeps the
# documented schema and sends it as JSON, which is what the API accepts.
_JSON_BODY_OPERATIONS = (
    "gmail.users.messages.send",
    "gmail.users.drafts.create",
    "gmail.users.drafts.update",
    "gmail.users.drafts.send",
)


_NAME_PREFIX = "gmail_users_"


def _public_names(spec: dict) -> dict[str, str]:
    """operationId -> the name the operation is published under.

    Derived through the same ``build_tool_name`` the rest of the import uses --
    it is what turns ``batchModify`` into ``batch_modify`` -- and then stripped
    of the ``gmail_users_`` prefix every Gmail operationId carries, which says
    nothing a caller does not already know from the connector id. The result
    reproduces the names the vendored Gmail client produced for all 79
    operations, so nothing that referenced one by name has to change.
    """
    names: dict[str, str] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                continue
            names[operation_id] = build_tool_name(
                operation_id, method.upper(), path
            ).removeprefix(_NAME_PREFIX)
    return names


def _build_overrides(spec: dict) -> dict[str, dict]:
    names = _public_names(spec)
    overrides: dict[str, dict] = {}
    for entry in ALLOWLIST:
        operation_id = entry["operation_id"]
        if operation_id not in names:
            raise SystemExit(f"Allowlisted operation not in the spec: {operation_id}")
        override: dict = {
            "name": names[operation_id],
            "path_param_defaults": {"userId": "me"},
        }
        if operation_id in _JSON_BODY_OPERATIONS:
            override["body_content_type"] = "application/json"
        overrides[operation_id] = override
    return overrides


def build_static_operations() -> list[dict]:
    spec = load_spec(CONNECTOR_ID)
    operations = build_operation_descriptors(
        spec,
        server_url=SERVER_URL,
        allowlist=ALLOWLIST,
        overrides=_build_overrides(spec),
        default_headers=DEFAULT_HEADERS,
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
        "existing 'gmail' entry in lemma_apps_config.json.",
    )
    args = parser.parse_args()

    static_operations = build_static_operations()

    if args.write:
        write_into_lemma_apps_config(CONNECTOR_ID, static_operations)
        print(
            f"Wrote {len(static_operations)} operations into "
            f"{LEMMA_APPS_CONFIG_PATH}'s 'gmail' entry."
        )
    else:
        print(json.dumps(static_operations, indent=2))


if __name__ == "__main__":
    main()
