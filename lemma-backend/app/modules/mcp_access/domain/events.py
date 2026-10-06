"""What a pod offers an outside client to subscribe to, and on what terms.

The webhook slice of the MCP Events working-group draft, which is what ChatGPT
implements: `events/list`, `events/subscribe` with a callback URL and the
client's own `whsec_` secret, `events/unsubscribe`, and Standard Webhooks
signed deliveries. Kept to this module and `app/mcp_events.py`, because the
draft has no SEP yet and its wire format may still move.

A subscription is a credential: whoever holds it receives rows. So it is the
grant's -- revoking the connection ends it -- and every delivery re-checks that
the person it acts for can still read the row, rather than trusting the moment
they subscribed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import timedelta
from uuid import UUID

from pydantic import JsonValue

#: The draft's error codes, as ChatGPT reads them.
NOT_FOUND = -32011
FORBIDDEN = -32012
RESOURCE_EXHAUSTED = -32013
UNSUPPORTED = -32014
CALLBACK_ENDPOINT_ERROR = -32015
INVALID_PARAMS = -32602

#: How long a subscription lasts before the client must refresh it. The client
#: may ask for less; it is never granted more, and never forever, so a
#: connection nobody is using stops receiving on its own.
DEFAULT_TTL = timedelta(hours=1)
MAX_TTL = timedelta(hours=24)
MIN_TTL = timedelta(minutes=5)

#: Per connection. Past it, subscribing answers ResourceExhausted.
MAX_SUBSCRIPTIONS_PER_GRANT = 50

#: The draft's ceiling on one delivery's body.
MAX_PAYLOAD_BYTES = 256 * 1024

RECORD_CREATED = "record.created"

DESCRIPTORS: list[dict[str, JsonValue]] = [
    {
        "name": RECORD_CREATED,
        "description": (
            "A row was added to a table in this pod. Delivered only when the "
            "person this connection acts for can read the row."
        ),
        "delivery": ["webhook"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "The table to watch, by name.",
                }
            },
            "required": ["table"],
            "additionalProperties": False,
        },
        "payloadSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "record_id": {"type": "string"},
                "record": {"type": "object"},
            },
            "required": ["table", "record_id", "record"],
            "additionalProperties": False,
        },
    }
]

EVENT_NAMES = frozenset(str(item["name"]) for item in DESCRIPTORS)


def granted_ttl(requested_ms: int | None) -> timedelta:
    if requested_ms is None:
        return DEFAULT_TTL
    asked = timedelta(milliseconds=max(requested_ms, 0))
    return max(MIN_TTL, min(asked, MAX_TTL))


def canonical_arguments(arguments: dict[str, JsonValue]) -> tuple[str, str]:
    """The arguments as one canonical JSON string, and its digest: two
    subscriptions with the same arguments in a different key order are one."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return canonical, hashlib.sha256(canonical.encode()).hexdigest()


def subscription_id(grant_id: UUID, url: str, name: str, arguments_key: str) -> str:
    """Deterministic, so a refresh with the same identity names the same one.
    Used for routing only: never accepted as input."""
    digest = hashlib.sha256(f"{grant_id}|{url}|{name}|{arguments_key}".encode())
    return "sub_" + digest.hexdigest()[:24]


def valid_secret(secret: object) -> bool:
    """`whsec_` and the base64 of 24 to 64 random bytes, as the draft requires."""
    if not isinstance(secret, str) or not secret.startswith("whsec_"):
        return False
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except binascii.Error, ValueError:
        return False
    return 24 <= len(key) <= 64
