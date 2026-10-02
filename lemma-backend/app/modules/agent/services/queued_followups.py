"""Which waiting messages a follow-up turn answers, and as which kind of run.

A follow-up answers what nobody read while a run was going. Some of that may be
private notes, and a run answers one kind: a note's answer stays in Lemma, and
anything else goes to the platform (``domain/private_notes``). So a follow-up
takes the kind of the oldest message waiting and leaves the other kind for the
next one -- recording where it waits, because it is queued behind a run that
has already finished, and nothing else would look there again.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.modules.agent.domain.ports import ConversationRepository

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
    repository: ConversationRepository,
    sources: list[UUID],
) -> tuple[UUID, bool] | None:
    """The run whose queue to answer, and whether its oldest message is a note."""
    for source in sources:
        notes = await repository.earliest_queued_is_note(source)
        if notes is not None:
            return source, notes
    return None


async def still_waiting(
    repository: ConversationRepository,
    sources: list[UUID],
    *,
    answering: UUID,
    notes: bool,
) -> list[str]:
    """The sources that will still hold unanswered messages once this one is claimed."""
    waiting: list[str] = []
    for source in sources:
        left = await repository.count_queued_user_messages(
            source, notes=(not notes) if source == answering else None
        )
        if left:
            waiting.append(str(source))
    return waiting
