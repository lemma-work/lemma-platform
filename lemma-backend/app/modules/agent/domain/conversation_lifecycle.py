"""Which conversation statuses count as still going, and which as finished.

A separate module from ``value_objects`` for the reason ``conversation_run_queries``
is separate from the repository: that file is at the architecture ratchet's size
limit, and these belong with each other more than with anything already in it.

``WAITING`` is deliberately in neither set. A run that ends by asking a question
leaves its conversation waiting, and that is a resting state — neither active nor
finished. Treating it as active would tear down the pause ``request_approval``
and ``ask_user`` are built on; treating it as finished would report a turn as
over while somebody is still being asked.
"""

from __future__ import annotations

from app.modules.agent.domain.value_objects import ConversationStatus

ACTIVE_CONVERSATION_STATUSES = frozenset(
    {ConversationStatus.RUNNING, ConversationStatus.STOP_REQUESTED}
)

TERMINAL_CONVERSATION_STATUSES = frozenset(
    {
        ConversationStatus.COMPLETED,
        ConversationStatus.FAILED,
        ConversationStatus.STOPPED,
    }
)
