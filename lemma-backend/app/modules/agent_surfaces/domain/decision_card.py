"""Where an approval or question card was posted, as a platform can find it again."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CardRetirement(StrEnum):
    """Why a card's controls are being taken away."""

    #: A new message started a turn, which cancelled the pause the card asks
    #: about.
    SUPERSEDED = "superseded"
    #: The pause was answered another way -- a typed reply or the web app,
    #: not a tap.
    ANSWERED = "answered"

    @property
    def note(self) -> str:
        """The line a rewritten card ends with, saying why it has no buttons."""
        if self is CardRetirement.ANSWERED:
            return "Answered."
        return "No longer needed."


class DecisionCardHandle(BaseModel):
    """One posted card: the chat it is in and the message that is it."""

    platform: str
    channel: str
    message_id: str
    #: For a platform that rewrites a message whole: the card's own content
    #: without its controls, to rewrite it with. Telegram edits the keyboard
    #: alone and keeps none.
    blocks: list[dict[str, object]] = Field(default_factory=list)
    #: The card's notification text, for a platform whose rewrite needs one.
    text: str = ""
    #: Whatever else the platform needs to reach the message again: Teams'
    #: service URL and tenant, WhatsApp's sending number.
    extra: dict[str, str] = Field(default_factory=dict)


__all__ = ["CardRetirement", "DecisionCardHandle"]
