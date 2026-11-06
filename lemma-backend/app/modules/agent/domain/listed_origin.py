"""Where a conversation came from, as a listing across pods groups it.

Read off the conversation's typed ``origin`` -- set once, by the code that
opened it -- with two refinements the column does not make: a person's chat
bound to a document or a table (``lemma_resource``) is that resource's chat,
and a conversation typed ``TASK`` is one nobody chats in. Nothing is fetched to
work it out.

A reader across many pods asks two things of an origin:

- **Did an agent start it?** Nobody typed the first message: a schedule fired,
  a workflow step ran, a notification landed, another pod asked, or a task ran
  on its own. Everything else a person started -- a chat, a message on a
  channel, a document's own chat.
- **Is it a chat?** What a person talks in: one they opened here, or one that
  came in on a channel.

``listed_origin_of`` is the rule in Python; ``infrastructure/db/
recent_conversations.listed_origin_column`` is the same rule in SQL, so a count
over thousands of rows never has to load them. Both are built from
``BY_ORIGIN`` and tried in the same order, and
``test_listed_origin_sql_agrees`` runs both over every shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from app.modules.agent.domain.state_kinds import ConversationOrigin

#: The resource a document's or a table's own chat is bound to, as
#: ``kind:name``. Written by the web client when it opens one.
RESOURCE_KEY = "lemma_resource"
#: What a conversation nobody chats in is typed as.
TASK_TYPE = "TASK"


class ListedOrigin(str, Enum):
    CHAT = "chat"
    CHANNEL = "channel"
    NOTIFICATION = "notification"
    SCHEDULE = "schedule"
    WORKFLOW = "workflow"
    RESOURCE = "resource"
    TASK = "task"
    ASK = "ask"


#: The origins that alone decide how a conversation is listed. Any other --
#: a member's chat, a project, a call, a template's setup -- is a chat, unless
#: it is bound to a resource or typed as a task.
BY_ORIGIN: dict[ConversationOrigin, ListedOrigin] = {
    # A chat platform, one of Lemma's own channels, or someone outside the
    # pod: each came in on a channel.
    ConversationOrigin.SURFACE: ListedOrigin.CHANNEL,
    ConversationOrigin.CHANNEL: ListedOrigin.CHANNEL,
    ConversationOrigin.CONTACT: ListedOrigin.CHANNEL,
    ConversationOrigin.NOTIFICATION: ListedOrigin.NOTIFICATION,
    ConversationOrigin.SCHEDULE: ListedOrigin.SCHEDULE,
    ConversationOrigin.POD_ASK: ListedOrigin.ASK,
    ConversationOrigin.WORKFLOW_STEP: ListedOrigin.WORKFLOW,
    ConversationOrigin.SUBAGENT: ListedOrigin.TASK,
}

#: Conversations nobody typed the first message of.
STARTED_BY_AN_AGENT: frozenset[ListedOrigin] = frozenset(
    {
        ListedOrigin.NOTIFICATION,
        ListedOrigin.SCHEDULE,
        ListedOrigin.WORKFLOW,
        ListedOrigin.TASK,
        ListedOrigin.ASK,
    }
)

#: What a person talks in. A conversation of any other origin is still listed
#: with the chats while it waits on its owner: that is where it is answered.
CHATS: frozenset[ListedOrigin] = frozenset({ListedOrigin.CHAT, ListedOrigin.CHANNEL})


def listed_origin_of(
    origin: ConversationOrigin,
    conversation_type: str | None,
    metadata: Mapping[str, object] | None,
) -> ListedOrigin:
    """How a conversation of this ``origin``, type and ``metadata`` is listed.

    A resource binding counts only as a non-blank string, as the SQL twin's
    ``jsonb_typeof`` and ``btrim`` read it.
    """
    decided = BY_ORIGIN.get(origin)
    if decided is not None:
        return decided
    resource = (metadata or {}).get(RESOURCE_KEY)
    if isinstance(resource, str) and resource.strip(" \t\r\n"):
        return ListedOrigin.RESOURCE
    if conversation_type == TASK_TYPE:
        return ListedOrigin.TASK
    return ListedOrigin.CHAT


__all__ = [
    "BY_ORIGIN",
    "CHATS",
    "ListedOrigin",
    "RESOURCE_KEY",
    "STARTED_BY_AN_AGENT",
    "TASK_TYPE",
    "listed_origin_of",
]
