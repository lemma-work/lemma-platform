"""What a report is: who filed it, about which message, why, and a copy of
what they read.

The copy is the point. A chat with a Lemma opens only for its owner, and a
shared chat's line can be edited or removed after it is reported, so the team
reading a report reads what the person saw when they reported it, not
whatever the message says now.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: How long a person's own words with a report may be.
MAX_NOTE_CHARS = 1000
#: How much of each line the copy keeps. The team needs enough to judge it,
#: not a second transcript.
MAX_EXCERPT_CHARS = 2000
#: The reported line and up to this many before it, for what led to it.
LINES_BEFORE = 4


class ReportReason(StrEnum):
    """Why it was reported, as the person picked it."""

    OFFENSIVE = "OFFENSIVE"
    HARMFUL = "HARMFUL"
    SEXUAL = "SEXUAL"
    SPAM = "SPAM"
    OTHER = "OTHER"

    @property
    def said(self) -> str:
        return _REASONS_SAID[self]


_REASONS_SAID = {
    ReportReason.OFFENSIVE: "offensive or hateful",
    ReportReason.HARMFUL: "harmful or dangerous",
    ReportReason.SEXUAL: "sexual",
    ReportReason.SPAM: "spam or a scam",
    ReportReason.OTHER: "something else",
}


class ReportPlace(StrEnum):
    """Where the message was read."""

    #: A person's own chat with a Lemma.
    CONVERSATION = "CONVERSATION"
    #: A shared chat: a group, a contact's chat, a member's own.
    CHANNEL = "CHANNEL"


class AuthorKind(StrEnum):
    """Who wrote the reported message."""

    AGENT = "AGENT"
    MEMBER = "MEMBER"
    CONTACT = "CONTACT"


class ReportStatus(StrEnum):
    """Where the team is with it. Closed by hand, by the team."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class ExcerptLine(BaseModel):
    """One line of the copy, as the reporter read it."""

    model_config = ConfigDict(frozen=True)

    who: str
    text: str = Field(max_length=MAX_EXCERPT_CHARS)
    at: datetime
    #: The line that was reported; the others came before it.
    reported: bool = False

    @classmethod
    def of(
        cls, *, who: str, text: str, at: datetime, reported: bool = False
    ) -> ExcerptLine:
        cut = (
            text
            if len(text) <= MAX_EXCERPT_CHARS
            else text[: MAX_EXCERPT_CHARS - 1] + "…"
        )
        return cls(who=who, text=cut, at=at, reported=reported)


class NewReport(BaseModel):
    """A report, as the module that holds the message hands it over.

    The author and the copy come from the server's own reading of the message,
    never from the client: a report names who wrote what the reporter was
    shown, not what the reporter says they were shown.
    """

    model_config = ConfigDict(frozen=True)

    organization_id: UUID | None
    pod_id: UUID
    reporter_user_id: UUID
    place: ReportPlace
    conversation_id: UUID | None = None
    channel_id: UUID | None = None
    message_id: UUID
    #: The chat's name when it was reported: its title, a group's name.
    place_title: str | None = None
    author_kind: AuthorKind
    #: The member's user id, the contact's id or the agent's id; ``None`` for
    #: somebody a platform did not vouch for, known only by a name.
    author_id: UUID | None = None
    author_name: str | None = None
    #: For a Lemma's reply: the run that wrote it.
    agent_run_id: UUID | None = None
    reason: ReportReason
    note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    excerpt: tuple[ExcerptLine, ...] = ()

    @model_validator(mode="after")
    def _one_place(self) -> NewReport:
        if (self.place is ReportPlace.CONVERSATION) != (
            self.conversation_id is not None
        ):
            raise ValueError("conversation_id is set exactly for a conversation")
        if (self.place is ReportPlace.CHANNEL) != (self.channel_id is not None):
            raise ValueError("channel_id is set exactly for a channel")
        return self


class FiledReport(BaseModel):
    """A report as kept: its id, and when it was first filed."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    created_at: datetime


def clean_note(note: str | None) -> str | None:
    """The person's words, or ``None`` for none."""
    if note is None:
        return None
    kept = note.replace("\x00", "").strip()
    return kept or None
