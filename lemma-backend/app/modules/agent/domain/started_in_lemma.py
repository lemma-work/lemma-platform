"""Whether a run is its person's own, asked for in Lemma itself.

The workspace module asks this before letting a run onto a person's own
machine. Pure: the run's kind and the conversation's row, nothing read.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.run_kind import RunKind


def started_in_lemma(
    conversation: Conversation,
    user_id: UUID,
    *,
    kind: RunKind | None,
    parent_kind: RunKind | None = None,
) -> bool:
    """Whether ``user_id``'s run here was asked for by them, in Lemma.

    A person's default machine holds their logins and their files, so a run
    reaches it only when it is a ``CHAT`` turn (``domain/run_kind``): the
    person in the web app, the CLI or an SDK. Never a channel's line, a
    platform's message, a contact, a schedule, a workflow or another pod. A
    sub-agent answers as the run that spawned it (``parent_kind``) -- depth is
    one, so that run is never a sub-agent itself.

    The kind is the run's, not the conversation's: a person writing into a
    schedule's conversation is a ``CHAT`` turn, and the schedule's own turn
    there is not. The conversation still has to be the person's own and
    answer nobody else -- no contact and no channel to say it in.
    """
    if kind is RunKind.SUBAGENT:
        kind = parent_kind
    return (
        kind is RunKind.CHAT
        and conversation.user_id == user_id
        and conversation.channel_id is None
        and conversation.contact_id is None
    )
