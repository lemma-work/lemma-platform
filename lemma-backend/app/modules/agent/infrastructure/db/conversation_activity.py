"""What moves a conversation up the history list.

`last_activity_at` is the list's order, and it is indexed, so every stamp
rewrites the row in every index on `agent_conversations` -- none of them can be
a HOT update. A run writes a message per tool call and per tool result; stamping
each of those would multiply that write for no change anybody could see.

So a conversation is active when something outside the agent happens to it:
a person writes (a USER message, which also covers steering a live run and the
system's "your replies arrived" wake), a notification lands in somebody's
thread, a run starts (every wake -- a timer, a process, a sub-agent, an
approval or an answer -- starts one), or a run finishes. What the agent says
while it works is not activity; its finishing is.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.modules.agent.domain.value_objects import (
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models import ConversationModel


def is_activity(draft: MessageDraft) -> bool:
    """Whether appending this message counts as activity on its own.

    Not keyed on `metadata.source`: the append route passes the caller's
    metadata through, so a client can claim any source it likes.
    """
    return draft.role is MessageRole.USER or draft.kind is MessageKind.NOTIFICATION


def touch_activity(conversation: ConversationModel) -> None:
    conversation.last_activity_at = datetime.now(timezone.utc)
