"""Who is in a conversation.

A conversation used to have exactly one owner, and ``conversation.user_id``
answered every question about who could reach it. That holds while a
conversation is a DM and stops holding the moment a second person is in one,
which is what this table exists for. See
``docs/design/agent-conversations.md``.

People and agents share the table. A person row is who may read the
conversation at all; an agent row is what gives a router its roster and an
``@mention`` its namespace. Exactly one of the two columns is set on any row,
enforced by the database rather than trusted of callers.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from app.core.domain.entity import CreatedEntity


class ConversationParticipantRole(str, Enum):
    """Why this participant is here."""

    #: Opened the conversation. One per conversation, and not removable -- a
    #: person cannot be evicted from a conversation they started, so the access
    #: check needs no separate owner clause once every row has one of these.
    OWNER = "OWNER"
    #: Added afterwards.
    MEMBER = "MEMBER"


class ConversationParticipant(CreatedEntity):
    """One person, or one agent, in a conversation."""

    conversation_id: UUID
    user_id: UUID | None = None
    agent_id: UUID | None = None
    role: ConversationParticipantRole = ConversationParticipantRole.MEMBER
    #: What to call them on screen. Resolved when the roster is read, not
    #: stored: a name that was copied here would go stale the moment somebody
    #: changed theirs, and a transcript that attributes a message to the wrong
    #: name is worse than one that shows an email.
    display_name: str | None = None

    @property
    def is_agent(self) -> bool:
        return self.agent_id is not None
