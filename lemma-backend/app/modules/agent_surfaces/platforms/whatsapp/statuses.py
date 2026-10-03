"""WhatsApp's delivery statuses, read for the one that matters: ``failed``.

Meta acknowledges a send with a message id and reports what became of it later,
in the same webhook as inbound messages but under ``value.statuses`` instead of
``value.messages``. ``sent``, ``delivered`` and ``read`` are progress nobody
here acts on; ``failed`` is a message the person never received, and the only
place that is ever said.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from app.modules.agent_surfaces.domain.outbound_messages import FailedDeliveryStatus
from app.modules.agent_surfaces.platforms.common import (
    payload_first,
    payload_section,
    payload_text,
)

#: Outside the 24-hour customer-service window: only a template may be sent.
REENGAGEMENT_REQUIRED = "131047"
#: The number cannot receive it -- not on WhatsApp, an old app, or it blocked us.
RECIPIENT_UNREACHABLE = "131026"
#: The failures a person can still be reached past: by another channel.
UNREACHABLE_ON_WHATSAPP = frozenset({REENGAGEMENT_REQUIRED, RECIPIENT_UNREACHABLE})


def _first_error(status: Mapping[str, object]) -> Mapping[str, object]:
    errors = status.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return errors[0]
    return {}


def _statuses(
    payload: Mapping[str, object],
) -> Iterator[tuple[str, Mapping[str, object]]]:
    """Every status object in a body, with the number that sent its message."""
    entries = payload.get("entry")
    for entry in entries if isinstance(entries, list) else []:
        changes = entry.get("changes") if isinstance(entry, dict) else None
        for change in changes if isinstance(changes, list) else []:
            value = payload_section(change, "value")
            sender = payload_text(payload_section(value, "metadata"), "phone_number_id")
            statuses = value.get("statuses")
            for status in statuses if isinstance(statuses, list) else []:
                if isinstance(status, dict):
                    yield sender, status


def _failure(sender: str, status: Mapping[str, object]) -> FailedDeliveryStatus | None:
    message_id = payload_text(status, "id").strip()
    if status.get("status") != "failed" or not message_id:
        return None
    error = _first_error(status)
    return FailedDeliveryStatus(
        external_message_id=message_id,
        recipient=payload_text(status, "recipient_id") or None,
        code=payload_text(error, "code") or None,
        title=payload_first(error, "title", "message") or None,
        sender_id=sender or None,
    )


def parse_failed_whatsapp_statuses(
    payload: Mapping[str, object],
) -> list[FailedDeliveryStatus]:
    """Every ``failed`` status in a webhook body, across every entry and change."""
    found = (_failure(sender, status) for sender, status in _statuses(payload))
    return [failure for failure in found if failure is not None]


__all__ = [
    "RECIPIENT_UNREACHABLE",
    "REENGAGEMENT_REQUIRED",
    "UNREACHABLE_ON_WHATSAPP",
    "parse_failed_whatsapp_statuses",
]
