"""One person's conversations across many pods, as a sidebar and a chart read them.

Read models only: what ``recent_conversation_queries`` returns, so a module
reading them through ``contracts.conversation_summaries`` depends on these
shapes rather than on a row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.modules.agent.domain.conversation_origin import ConversationOrigin


@dataclass(frozen=True, slots=True)
class RecentChat:
    """One of a pod's latest chats, as a sidebar row draws it."""

    pod_id: UUID
    conversation_id: UUID
    title: str | None
    #: A chat or a channel's -- or, while it waits on its owner, whatever
    #: started it: that is where the answer goes.
    origin: ConversationOrigin
    #: RUNNING while an agent works in it; None before its first run.
    status: str | None
    agent_id: UUID | None
    last_activity_at: datetime
    waiting_on_you: bool
    #: The platform a channel's conversation came in on (SLACK, WHATSAPP ...).
    platform: str | None
    #: The channel's name on that platform, when it has one.
    channel_name: str | None


@dataclass(frozen=True, slots=True)
class DayActivity:
    """How many of a person's conversations in one pod were last active on a day.

    Split by who started them: ``by_agent`` nobody typed the first message of
    (``conversation_origin.STARTED_BY_AN_AGENT``), ``by_people`` the rest.
    """

    pod_id: UUID
    day: date
    by_agent: int
    by_people: int


__all__ = ["DayActivity", "RecentChat"]
