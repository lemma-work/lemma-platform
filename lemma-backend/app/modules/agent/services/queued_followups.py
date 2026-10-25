"""Which waiting messages a follow-up turn answers, and as which kind of run.

A follow-up answers what nobody read while a run was going. Some of that may be
private notes, and a run answers one kind: a note's answer stays in Lemma, and
anything else goes to the platform (``domain/private_notes``). So a follow-up
takes the kind of the oldest message waiting and leaves the other kind for the
next one -- recording where it waits, because it is queued behind a run that
has already finished, and nothing else would look there again.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.infrastructure.db.queued_messages import QueuedMessages

# The finished runs still holding messages of the kind a follow-up did not
# answer are its `queued_leftovers_behind`, for the follow-up after it to find.


async def next_queue(
    repository: QueuedMessages,
    sources: list[UUID],
    *,
    after_sequence: int | None = None,
) -> tuple[UUID, bool] | None:
    """The run whose queue to answer, and whether its oldest message is a note.

    ``after_sequence`` keeps to what was said after that point (after Stop).
    """
    for source in sources:
        notes = await repository.earliest_queued_is_note(
            source, after_sequence=after_sequence
        )
        if notes is not None:
            return source, notes
    return None


async def still_waiting(
    repository: QueuedMessages,
    sources: list[UUID],
    *,
    answering: UUID,
    notes: bool,
    after_sequence: int | None = None,
) -> list[UUID]:
    """The sources that will still hold unanswered messages once this one is claimed."""
    waiting: list[UUID] = []
    for source in sources:
        left = await repository.count_queued_user_messages(
            source,
            notes=(not notes) if source == answering else None,
            after_sequence=after_sequence,
        )
        if left:
            waiting.append(source)
    return waiting
