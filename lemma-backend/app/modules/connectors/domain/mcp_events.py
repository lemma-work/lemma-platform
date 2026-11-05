"""Listening to a connected MCP server's events: what is known before any I/O.

The other half of `mcp_access`'s events. There a pod is the server and ChatGPT
the subscriber; here the pod's standing work subscribes to a server someone
connected, through the same working-group draft's webhook slice: `events/list`
says what the server offers, `events/subscribe` names a callback and a
`whsec_` secret of ours, the server proves the callback with a signed challenge,
and every occurrence arrives Standard Webhooks signed with that secret.

A server's events describe the tenant's own systems, so they are kept per
install, beside its discovered tools, never in the global trigger catalog: the
one `mcp` connector is every MCP server anyone has connected.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import JsonValue

#: The revision the draft's methods are offered on.
MCP_EVENTS_PROTOCOL = "2026-07-28"

#: The webhook source an MCP server's deliveries arrive as, and the `source`
#: an MCP event schedule's config names.
MCP_WEBHOOK_SOURCE = "mcp"

#: The callback's query parameter naming the subscription. Ours, not the
#: server's: the challenge arrives before the server has told us its own id.
SUBSCRIPTION_PARAM = "subscription"

#: What we ask for. A server grants what it likes, and the refresh follows that.
REQUESTED_TTL = timedelta(hours=24)

#: A server that answers without `refreshBefore` is assumed to want this.
ASSUMED_TTL = timedelta(hours=1)

#: How often the refresher runs, and so how little lifetime is too little.
REFRESH_INTERVAL = timedelta(minutes=5)

#: How long one pass keeps starting renewals. Shorter than the interval by more
#: than one call's timeout, so a pass that meets a slow server ends before the
#: next one starts.
RENEW_PASS_BUDGET = timedelta(minutes=4)

#: The longest a subscription that keeps failing to renew waits between tries.
MAX_RETRY_DELAY = timedelta(hours=6)

#: One server never offers more than this many events to the catalog.
MAX_EVENTS_PER_INSTALL = 100


@dataclass(frozen=True, slots=True)
class DiscoveredEvent:
    """One `events/list` descriptor, as an install keeps it."""

    name: str
    description: str | None
    input_schema: dict[str, JsonValue] = field(default_factory=dict)
    payload_schema: dict[str, JsonValue] = field(default_factory=dict)


def parse_descriptors(result: object) -> list[DiscoveredEvent]:
    """The descriptors in an `events/list` result, read defensively.

    Third-party JSON: an entry without a usable name is skipped rather than
    failing the rest, and only webhook-deliverable events are kept, because
    that is the only delivery this side receives.
    """
    events = result.get("events") if isinstance(result, dict) else None
    if not isinstance(events, list):
        return []
    found: dict[str, DiscoveredEvent] = {}
    for entry in events:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            continue
        delivery = entry.get("delivery")
        if isinstance(delivery, list) and "webhook" not in delivery:
            continue
        description = entry.get("description")
        found.setdefault(
            name,
            DiscoveredEvent(
                name=name,
                description=description if isinstance(description, str) else None,
                input_schema=_object_schema(entry.get("inputSchema")),
                payload_schema=_object_schema(entry.get("payloadSchema")),
            ),
        )
        if len(found) >= MAX_EVENTS_PER_INSTALL:
            break
    return list(found.values())


def _object_schema(value: object) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def arguments_problem(
    input_schema: dict[str, JsonValue], arguments: dict[str, JsonValue]
) -> str | None:
    """Why these arguments cannot narrow this event, or None.

    Only what is cheap and certain is checked here -- required keys present,
    unknown keys refused when the schema says so, each value of the declared
    JSON type and among the declared `enum` -- because the server is the
    authority on its own arguments and refuses the rest when subscribing. A
    refusal said here names the argument; the server's, at subscribe time,
    often does not.
    """
    properties = input_schema.get("properties")
    known = properties if isinstance(properties, dict) else {}
    required = input_schema.get("required")
    missing = [
        key
        for key in (required if isinstance(required, list) else [])
        if isinstance(key, str) and key not in arguments
    ]
    if missing:
        return "Missing: " + ", ".join(sorted(missing)) + "."
    if input_schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(known))
        if unknown:
            return "Not arguments of this event: " + ", ".join(unknown) + "."
    wrong = sorted(
        name
        for name, value in arguments.items()
        if not _fits(_object_schema(known.get(name)), value)
    )
    if wrong:
        return "Not the kind of value the server takes: " + ", ".join(wrong) + "."
    return None


#: JSON Schema's primitive types, as Python checks them. `bool` is excluded
#: from the numbers on purpose: in Python it is one, and in JSON it is not.
_JSON_TYPES: dict[str, Callable[[object], bool]] = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    ),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}


def _fits(schema: dict[str, JsonValue], value: JsonValue) -> bool:
    """Whether one argument has the type and, if listed, one of the values its
    property schema declares. An undeclared type is not checked."""
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return False
    declared = schema.get("type")
    kinds = declared if isinstance(declared, list) else [declared]
    checks = [
        _JSON_TYPES[kind]
        for kind in kinds
        if isinstance(kind, str) and kind in _JSON_TYPES
    ]
    return not checks or any(check(value) for check in checks)


def new_secret() -> str:
    """A `whsec_` secret: 32 random bytes, which the draft's 24-64 allows."""
    return "whsec_" + base64.b64encode(os.urandom(32)).decode()


def refresh_before_from(value: object, *, now: datetime) -> datetime:
    """The server's `refreshBefore`, or the assumption when it gave none."""
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return now + ASSUMED_TTL


def renew_at(*, granted_at: datetime, refresh_before: datetime) -> datetime:
    """When a granted subscription is next renewed.

    Halfway through what was granted, or two passes before it lapses if that
    is sooner. A server granting less than two passes' worth can still lapse
    between them; it is then renewed late rather than never, by the next pass.
    """
    halfway = granted_at + (refresh_before - granted_at) / 2
    return min(halfway, refresh_before - 2 * REFRESH_INTERVAL)


def retry_at(*, failures: int, refresh_before: datetime, now: datetime) -> datetime:
    """When to try again after `failures` renewals in a row have failed.

    The wait doubles from one pass up to `MAX_RETRY_DELAY`, so a subscription
    the server keeps refusing -- or whose account needs signing in again --
    costs a call every few hours rather than every pass, and never holds the
    place of one that would renew. While the server still holds the
    subscription, the retry is never later than the last pass that could
    renew it in time: a short outage must not cost a working subscription.
    """
    delay = min(REFRESH_INTERVAL * 2 ** min(max(failures - 1, 0), 10), MAX_RETRY_DELAY)
    last_chance = refresh_before - 2 * REFRESH_INTERVAL
    if last_chance > now:
        return min(now + delay, last_chance)
    return now + delay
