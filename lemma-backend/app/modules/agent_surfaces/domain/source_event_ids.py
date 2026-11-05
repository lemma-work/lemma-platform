"""The durable identity of one inbound delivery, for every way it can arrive.

A surface event reaches the worker by a webhook, by a native receiver (a poller
or a socket client), or -- for email -- by either of two routes at once. The
durable inbox collapses a redelivery into one run only if every route mints the
same id for the same delivery, so the minting lives in one place.

A provider's own event id is only unique *within one receiver*: a Telegram
``update_id`` is a per-bot counter that starts low, so two pods with their own
bots both produce update 1, update 2, ... Keyed on the platform alone, the second
bot's update is claimed by the inbox as a duplicate of the first's and dropped
without a reply, an error, or a log line. Every id here therefore carries the
receiver it arrived on.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def webhook_source_event_id(
    platform: str, payload: dict, raw_body: bytes, *, receiver: str
) -> str:
    """The identity of one webhook delivery.

    ``receiver`` names the bot the delivery arrived on: the surface id on a
    surface-level webhook, the pooled number on a per-number one, and a constant
    for the shared platform-level endpoint. The pair is unique across the
    deployment; neither half is on its own.
    """
    if platform.lower() == "whatsapp":
        wamids = _whatsapp_delivery_ids(payload)
        if wamids:
            digest = hashlib.sha256("\n".join(wamids).encode()).hexdigest()
            return f"{platform}:{receiver}:wamids-sha256:{digest}"
    candidates: list[object] = [
        payload.get("event_id"),
        payload.get("update_id"),
        payload.get("id"),
        payload.get("message_id"),
        payload.get("data", {}).get("message_id")
        if isinstance(payload.get("data"), dict)
        else None,
    ]
    for candidate in candidates:
        if candidate is not None and str(candidate):
            return f"{platform}:{receiver}:{candidate}"
    digest = hashlib.sha256(raw_body).hexdigest()
    return f"{platform}:{receiver}:content-sha256:{digest}"


def _whatsapp_delivery_ids(payload: Mapping[str, object]) -> list[str]:
    """What a WhatsApp delivery is about, sorted, however Meta batched it.

    A WhatsApp webhook has no id of its own, so the body hash stood in -- and
    Meta retries for days and may re-batch a retry, which hashes differently and
    started a second run once the short Redis claim had expired. A message's
    ``wamid`` is stable across retries. A status names the outbound message it
    is about, so it is keyed with its state: "delivered" and "read" for one
    message are two deliveries.
    """
    found: set[str] = set()
    for value in _whatsapp_change_values(payload):
        for message in _dicts(value.get("messages")):
            if message.get("id"):
                found.add(f"message:{message['id']}")
        for status in _dicts(value.get("statuses")):
            if status.get("id"):
                found.add(f"status:{status['id']}:{status.get('status') or ''}")
    return sorted(found)


def _dicts(items: object) -> list[dict[str, object]]:
    return (
        [item for item in items if isinstance(item, dict)]
        if isinstance(items, list)
        else []
    )


def _whatsapp_change_values(payload: Mapping[str, object]) -> list[dict[str, object]]:
    """Every ``entry[].changes[].value`` of a WhatsApp body, however nested."""
    return [
        value
        for entry in _dicts(payload.get("entry"))
        for change in _dicts(entry.get("changes"))
        if isinstance(value := change.get("value"), dict)
    ]


def native_source_event_id(
    source: str, payload: dict[str, Any], *, receiver_key: str | None
) -> str:
    """The identity of one delivery from a native receiver (poller or socket)."""
    provider_id = (
        payload.get("event_id")
        or payload.get("update_id")
        or payload.get("id")
        or hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
    )
    return f"{source}:native:{receiver_key or 'unkeyed'}:{provider_id}"


def resend_source_event_id(
    normalized: Mapping[str, object], *, receiver: str
) -> str | None:
    """The durable identity of one inbound Resend delivery, or None.

    Resend arrives by two routes, and a deployment can be running both: the
    inbound webhook, and the poller a desktop worker uses when it has no public
    URL. One Resend project serving several environments makes that the ordinary
    state rather than a corner. The durable inbox only collapses the two into
    one delivery if they mint the same id.

    ``email_id`` is Resend's own handle for the message, which both routes
    already carry and which the body fetch needs anyway. ``message_id`` is the
    fallback rather than the first choice because the sender writes it.

    ``receiver`` is the surface the mail was delivered for, as on every other
    platform: the same mail reaching two surfaces is two deliveries.
    """
    for candidate in (normalized.get("email_id"), normalized.get("message_id")):
        identifier = str(candidate or "").strip()
        if identifier:
            return f"resend:{receiver}:{identifier}"
    return None


def resend_webhook_source_event_id(
    normalized: dict, raw_body: bytes, *, receiver: str
) -> str:
    """Resend's own id when the mail carries one, else the generic webhook id."""
    return resend_source_event_id(
        normalized, receiver=receiver
    ) or webhook_source_event_id("resend", normalized, raw_body, receiver=receiver)
