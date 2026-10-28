"""A contact's conversations as they read them: an export, a page's history.

A contact's conversation names them in its ``contact_id`` column
(``domain/outsiders``). What they saw of it is text -- their words, the bot's
answers, members' follow-ups -- never the pod's working, a member's private
note or what a note's run said back (``infrastructure/db/contact_history``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

#: Where a member's follow-up names the line it marks.
FOLLOW_UP_LINE_KEY = "line_id"

#: Where a visitor's own message keeps the name its page gave it.
CLIENT_NONCE_KEY = "client_nonce"

#: The most conversations one page of an export carries.
MAX_EXPORTED_CONVERSATIONS = 20

#: The most messages one conversation contributes to an export.
MAX_EXPORTED_MESSAGES = 2000


class ExportedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    text: str
    created_at: datetime
    sequence: int


class VisibleMessage(ExportedMessage):
    """A message as the visitor's page reads it back.

    ``client_nonce`` is the page's own name for a message it sent, so the page
    can match the server's copy to the one it already drew. It is the page's
    bookkeeping, not what was said, so an export leaves it out.
    """

    client_nonce: str | None = None
    #: Written by a member of the pod, not the agent: a person answered.
    from_person: bool = False


class ExportedConversation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str | None
    created_at: datetime
    messages: tuple[ExportedMessage, ...]


@dataclass(frozen=True, slots=True)
class ConversationCursor:
    """The last conversation one export page carried, in the export's order."""

    created_at: datetime
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class ConversationsPage:
    conversations: tuple[ExportedConversation, ...]
    #: Where the next page starts, or ``None`` when this was the last.
    next_after: ConversationCursor | None
