"""Which waiting messages a follow-up turn answers, and as which kind of run.

A follow-up answers what nobody read while a run was going. A run answers one
kind (``domain/run_places``): a note's answer stays in Lemma and anything else
goes to the platform (``domain/private_notes``), and it goes to one place --
what was said on WhatsApp while a run answered an email waited for that run,
and is answered on WhatsApp. So a follow-up takes the kind of the oldest
message waiting and leaves every other kind for the next one -- recording
where it waits, because it is queued behind a run that has already finished,
and nothing else would look there again.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.modules.agent.domain.conversation_ports import QueuedMessages
from app.modules.agent.domain.run_places import AnswerKind

#: On a follow-up run: the finished runs still holding messages of the kind it
#: did not answer, for the follow-up after it to find.
LEFTOVERS_KEY = "queued_leftovers_behind"


def leftover_sources(run_metadata: Mapping[str, object] | None) -> list[UUID]:
    stored = (run_metadata or {}).get(LEFTOVERS_KEY)
    found: list[UUID] = []
    for value in stored if isinstance(stored, list | tuple) else ():
        try:
            found.append(UUID(str(value)))
        except ValueError:
            continue
    return found


async def next_queue(
    repository: QueuedMessages,
    sources: list[UUID],
    *,
    after_sequence: int | None = None,
) -> tuple[UUID, AnswerKind] | None:
    """The run whose queue to answer, and the kind of its oldest message.

    ``after_sequence`` keeps to what was said after that point (after Stop).
    """
    for source in sources:
        kind = await repository.earliest_queued_kind(
            source, after_sequence=after_sequence
        )
        if kind is not None:
            return source, kind
    return None


async def still_waiting(
    repository: QueuedMessages,
    sources: list[UUID],
    *,
    answering: UUID,
    kind: AnswerKind,
    after_sequence: int | None = None,
) -> list[str]:
    """The sources that will still hold unanswered messages once this one is claimed."""
    waiting: list[str] = []
    for source in sources:
        left = await repository.count_queued_user_messages(
            source,
            beside=kind if source == answering else None,
            after_sequence=after_sequence,
        )
        if left:
            waiting.append(str(source))
    return waiting
