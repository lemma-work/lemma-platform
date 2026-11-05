"""A line to a contact, as their rules let it through or refuse it.

``contacts`` owns the rules (``contacts.contracts.outreach``); this is how a
channel delivery reads their verdict: the text that goes and what the send
carries, or a refusal that ends the delivery with the member's reason and a
``DeliveryReasonCode``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.modules.agent_surfaces.services.delivery_outcome import Outcome
from app.modules.channels.contracts import (
    ChannelMessage,
    DeliveryReasonCode as Code,
    SenderKind,
)
from app.modules.contacts.contracts.outreach import OutreachKind, Verdict


@dataclass(frozen=True, slots=True)
class Ready:
    """A line a contact's rules let through: its text, and what the send
    carries besides (an email's unsubscribe token)."""

    text: str
    metadata: dict[str, object] = field(default_factory=dict)
    #: ``(contact, line)`` for a member's line, which counts against the
    #: contact's day once it can go.
    day_of: tuple[UUID, UUID] | None = None


#: A contact's refusal (``contacts``' ``RefusalCode``) as a delivery's reason.
_CONTACT_REFUSALS = {
    "suppressed": Code.SUPPRESSED,
    "no_handle": Code.NO_HANDLE,
    "outside_window": Code.OUTSIDE_WINDOW,
    "rate_limited": Code.RATE_LIMITED,
}


class ContactRefused(Exception):
    """A line a contact's rules do not let through; the reason is the member's."""

    def __init__(self, reason: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.retryable = retryable

    @classmethod
    def of(cls, verdict: Verdict) -> ContactRefused:
        return cls(
            verdict.reason or "Not sent.",
            code=str(verdict.code),
            retryable=verdict.retryable,
        )

    def outcome(self, platform: str) -> Outcome:
        if self.retryable:
            # The rules could not be read (Redis): try again, not give up.
            return Outcome.failed(self.reason, platform=platform)
        return Outcome.skipped(
            self.reason,
            code=_CONTACT_REFUSALS.get(self.code, Code.SUPPRESSED),
            platform=platform,
        )


def outreach_kind(line: ChannelMessage) -> OutreachKind:
    if line.sender.kind is SenderKind.MEMBER:
        return OutreachKind.MEMBER_LINE
    return OutreachKind.AGENT_ANSWER


__all__ = ["ContactRefused", "Ready", "outreach_kind"]
