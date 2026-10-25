"""Where a conversation came from, as a listing across pods groups it.

Read off what was stamped on the conversation when it was opened -- the
``source`` in its metadata, a few keys beside it, and its type -- with nothing
fetched to work it out. The web client reads the same keys in the same order
(``lemma-frontend/src/thread/conversation-origin.ts``), and the two have to
agree: a day's bar counted here and the rows the client draws under it are the
same conversations, read twice.

A reader across many pods asks two things of an origin:

- **Did an agent start it?** Nobody typed the first message: a schedule fired,
  a workflow step ran, a notification landed, another pod asked, or a task ran
  on its own. Everything else a person started -- a chat, a message on a
  channel, a document's own chat.
- **Is it a chat?** What a person talks in: one they opened here, or one that
  came in on a channel.

``origin_of`` is the rule in Python; ``recent_conversation_queries.origin_column``
is the same rule in SQL, so a count over thousands of rows never has to load
them. ``test_conversation_origin_sql_agrees`` holds the two to each other over
every shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from app.modules.agent.domain.pod_asks import ASK_SOURCE
from app.modules.agent.domain.run_origin import SCHEDULE_SOURCE

#: ``source`` on a conversation a channel's message opened.
CHANNEL_SOURCE = "agent_surfaces"
#: ``source`` on the thread a notification lands in.
NOTIFICATION_SOURCE = "notification"
#: ``source`` on a workflow AGENT step's conversation.
WORKFLOW_SOURCE = "WORKFLOW_RUN"
#: ``audience`` on the one conversation where people outside the pod ask in a
#: group; it reads as a channel whatever its ``source`` says.
AUDIENCE_KEY = "audience"
OUTSIDERS = "outsiders"
#: The resource a document's or a table's own chat is bound to, as
#: ``kind:name``. Written by the web client when it opens one.
RESOURCE_KEY = "lemma_resource"
#: What a conversation nobody chats in is typed as.
TASK_TYPE = "TASK"


class ConversationOrigin(str, Enum):
    CHAT = "chat"
    CHANNEL = "channel"
    NOTIFICATION = "notification"
    SCHEDULE = "schedule"
    WORKFLOW = "workflow"
    RESOURCE = "resource"
    TASK = "task"
    ASK = "ask"


#: Conversations nobody typed the first message of.
STARTED_BY_AN_AGENT: frozenset[ConversationOrigin] = frozenset(
    {
        ConversationOrigin.NOTIFICATION,
        ConversationOrigin.SCHEDULE,
        ConversationOrigin.WORKFLOW,
        ConversationOrigin.TASK,
        ConversationOrigin.ASK,
    }
)

#: What a person talks in. A conversation of any other origin is still listed
#: with the chats while it waits on its owner: that is where it is answered.
CHATS: frozenset[ConversationOrigin] = frozenset(
    {ConversationOrigin.CHAT, ConversationOrigin.CHANNEL}
)

#: Each ``source`` that alone decides the origin, in the order they are tried.
SOURCE_ORIGINS: tuple[tuple[str, ConversationOrigin], ...] = (
    (CHANNEL_SOURCE, ConversationOrigin.CHANNEL),
    (NOTIFICATION_SOURCE, ConversationOrigin.NOTIFICATION),
    (SCHEDULE_SOURCE, ConversationOrigin.SCHEDULE),
    (ASK_SOURCE, ConversationOrigin.ASK),
    (WORKFLOW_SOURCE, ConversationOrigin.WORKFLOW),
)


def origin_of(
    metadata: Mapping[str, object] | None, conversation_type: str | None
) -> ConversationOrigin:
    """The origin of a conversation with this ``metadata`` and type.

    Values compare exactly, as the SQL twin's ``->>`` does: a key holding
    anything but that exact string does not decide the origin.
    """
    stored: Mapping[str, object] = metadata or {}
    if stored.get(AUDIENCE_KEY) == OUTSIDERS:
        return ConversationOrigin.CHANNEL
    source = stored.get("source")
    for value, origin in SOURCE_ORIGINS:
        if source == value:
            return origin
    resource = stored.get(RESOURCE_KEY)
    if isinstance(resource, str) and resource.strip():
        return ConversationOrigin.RESOURCE
    if conversation_type == TASK_TYPE:
        return ConversationOrigin.TASK
    return ConversationOrigin.CHAT


__all__ = [
    "CHATS",
    "ConversationOrigin",
    "STARTED_BY_AN_AGENT",
    "origin_of",
]
