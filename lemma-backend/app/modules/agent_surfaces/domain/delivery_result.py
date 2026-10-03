"""What became of one outbound delivery, beyond "did it arrive".

``deliver_envelope`` returned a bool, and a bool cannot say *where* something
arrived. That stopped being enough once a closed WhatsApp reply window began to
reroute the reply to email: the caller -- an agent tool, ``surface.send`` --
has to be able to say "sent by email" rather than claim a chat message the
person will never see in the chat.

Truthy exactly when delivered, so every caller that only asks "did it go" keeps
reading it as the bool it was.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The surface's own platform delivered it.
CHAT_CHANNEL = "chat"
#: The chat could not take it, and it went to the person's email instead.
EMAIL_CHANNEL = "email"


@dataclass(frozen=True, slots=True)
class SurfaceDeliveryResult:
    delivered: bool
    #: ``chat`` or ``email``; ``None`` when nothing was delivered.
    channel: str | None = None
    #: Why it was not delivered, or why it went where it did, for a person.
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.delivered

    @property
    def by_email(self) -> bool:
        return self.delivered and self.channel == EMAIL_CHANNEL

    @classmethod
    def on_chat(cls) -> SurfaceDeliveryResult:
        return cls(delivered=True, channel=CHAT_CHANNEL)

    @classmethod
    def by_email_because(cls, reason: str) -> SurfaceDeliveryResult:
        return cls(delivered=True, channel=EMAIL_CHANNEL, reason=reason)

    @classmethod
    def undelivered(cls, reason: str | None = None) -> SurfaceDeliveryResult:
        return cls(delivered=False, reason=reason)


__all__ = ["CHAT_CHANNEL", "EMAIL_CHANNEL", "SurfaceDeliveryResult"]
