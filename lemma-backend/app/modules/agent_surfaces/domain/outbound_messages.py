"""A platform's report that a message it accepted never arrived."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FailedDeliveryStatus:
    """One platform report that a message it accepted never arrived."""

    external_message_id: str
    recipient: str | None
    #: The platform's error code, as a string: WhatsApp's are numbers, and
    #: nothing here does arithmetic on them.
    code: str | None
    title: str | None
    #: The number that sent it, on a platform that has several.
    sender_id: str | None = None


__all__ = ["FailedDeliveryStatus"]
